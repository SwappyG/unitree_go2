import asyncio
import dataclasses
import logging
import time
from types import TracebackType
from typing import Any, TypeAlias

from aiortc import MediaStreamTrack, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole, MediaRelay
from go2_robot_sdk.domain.entities.robot_data import RobotData
from just_robots_firebase_client.firebase_client import FirebaseClient

from just_robots.utils.settings import JustRobotsSettings, get_just_robots_settings
from just_robots.webrtc_relay.webrtc_relay_go2 import WebRTCRelayGo2
from just_robots.webrtc_relay.webrtc_relay_peers_manager import (
    ConnectionUUID,
    WebRTCRelayPeersManager,
)

logger = logging.getLogger(__name__)

FirebaseUID: TypeAlias = str


@dataclasses.dataclass
class PeerOfferResult:
    """Result of processing a peer offer."""

    sdp: RTCSessionDescription | None
    connection_id: ConnectionUUID


class WebRTCRelay:
    def __init__(
        self,
        firebase_client: FirebaseClient,
        settings: JustRobotsSettings | None = None,
    ):
        if settings is None:
            self._settings = get_just_robots_settings()
        else:
            self._settings = settings

        self._firebase_client = firebase_client
        self._media_relay: MediaRelay = MediaRelay()
        self._go2: WebRTCRelayGo2 | None = None
        self._go2_video_track: MediaStreamTrack | None = None
        self._video_blackhole: MediaBlackhole | None = None
        self._peers = WebRTCRelayPeersManager(
            settings=self._settings,
            media_relay=self._media_relay,
            on_datachannel_message=self._on_datachannel_message,
            on_peer_removed=self._on_peer_removed,
        )

        self._no_peers_monitor_task = asyncio.create_task(self._no_peers_monitor())

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ):
        await self.shutdown()

    async def shutdown(self):
        self._no_peers_monitor_task.cancel()
        try:
            await self._no_peers_monitor_task
        except asyncio.CancelledError:
            pass

        if self._video_blackhole is not None:
            await self._video_blackhole.stop()

        await self._peers.shutdown()
        if self._go2 is not None:
            await self._go2.shutdown()

    @property
    def firebase_client(self) -> FirebaseClient:
        return self._firebase_client

    async def connect_to_go2(
        self,
        robot_ip: str,
        robot_num: int,
        token: str,
        reconnect: bool = False,
    ):
        if self._go2 is not None:
            if self._go2.robot_ip != robot_ip or reconnect:
                logger.info(f"removing existing GO2 connection to {self._go2.robot_ip}")
                await self._go2.shutdown()
            else:
                logger.info("GO2 connection already exists")
                return

        self._go2 = await WebRTCRelayGo2.create(
            settings=self._settings,
            robot_ip=robot_ip,
            robot_num=robot_num,
            token=token,
            on_message=self._on_go2_message,
            on_video_frame=self._on_go2_video_track,
        )

    async def disconnect_from_go2(self):
        if self._go2 is not None:
            await self._go2.shutdown()
        self._go2 = None

    async def process_peer_offer(
        self,
        offer_sdp: str,
        offer_type: str,
        user_firebase_uid: FirebaseUID,
        user_firebase_email: str,
    ) -> PeerOfferResult:
        video_track = self._go2_video_track
        if video_track is not None:
            video_track = self._media_relay.subscribe(video_track)

        peer = await self._peers.add_peer(
            offer_sdp=offer_sdp,
            offer_type=offer_type,
            user_firebase_uid=user_firebase_uid,
            user_firebase_email=user_firebase_email,
            video_track=video_track,
        )

        # This will stop the video black hole, since we have a peer consuming the video
        await self._update_video_blackhole()

        return PeerOfferResult(
            sdp=peer.peer_connection.localDescription,
            connection_id=peer.connection_id,
        )

    async def get_subs_for_connection(self, connection_id: ConnectionUUID) -> set[str]:
        return await self._peers.get_connection_subs(connection_id)

    async def add_sub_for_connection(self, connection_id: ConnectionUUID, topic: str):
        if self._go2 is None:
            logger.warning("GO2 connection not established")
            return
        await self._peers.add_sub_to_connection(connection_id, topic)
        await self._go2.send_subscribe_message(topic)

    async def remove_sub_from_connection(
        self, connection_id: ConnectionUUID, topic: str
    ):
        if self._go2 is None:
            logger.warning("GO2 connection not established")
            return
        await self._peers.remove_sub_from_connection(connection_id, topic)
        await self._go2.send_unsubscribe_message(topic)

    async def _on_peer_removed(self) -> None:
        """Called by peers manager when peers are removed (e.g., due to idle timeout)."""
        await self._update_video_blackhole()

    async def _on_go2_message(self, robot_data: RobotData):
        msg_type = type(robot_data.raw_message).__name__
        msg_preview = (
            str(robot_data.raw_message)[:100] if robot_data.raw_message else "None"
        )
        logger.info(
            f"Received message from GO2: type={msg_type}, preview={msg_preview}"
        )

        if isinstance(robot_data.raw_message, (bytes, str)):
            await self._peers.broadcast_to_all_peers(robot_data.raw_message)
        else:
            logger.warning(f"unknown raw type {type(robot_data.raw_message)}")

    async def _on_go2_video_track(self, track: MediaStreamTrack, _robot_num: str | int):
        """
        Store the GO2 video track. We'll attach it to a PC RTCPeerConnection
        when the PC calls /offer. We'll relay via MediaRelay for multi-subscriber safety.
        """
        logger.info(f"received go2 video track, {track=}")

        # Stop existing blackhole if any
        await self._stop_video_blackhole()

        if self._go2_video_track is not None:
            # TODO (swapnil): determine if the proxy tracks need to be manually stopped
            self._go2_video_track.stop()
            self._media_relay = MediaRelay()

        self._go2_video_track = track

        # Start blackhole if no peers are connected
        await self._update_video_blackhole()
        await self._peers.update_video_track(self._media_relay, track)

    async def _start_video_blackhole(self) -> None:
        """Start the blackhole to consume video frames and prevent buffering."""
        if self._video_blackhole is not None or self._go2_video_track is None:
            return

        logger.debug("Starting video blackhole (no peers connected)")
        drain_track = self._media_relay.subscribe(self._go2_video_track)
        self._video_blackhole = MediaBlackhole()
        self._video_blackhole.addTrack(drain_track)
        await self._video_blackhole.start()

    async def _stop_video_blackhole(self) -> None:
        """Stop the blackhole."""
        if self._video_blackhole is None:
            return

        logger.debug("Stopping video blackhole")
        await self._video_blackhole.stop()
        self._video_blackhole = None

    async def _update_video_blackhole(self) -> None:
        """Start or stop the blackhole based on whether peers are connected."""
        if self._peers.num_peers == 0:
            await self._start_video_blackhole()
        else:
            await self._stop_video_blackhole()

    async def _on_datachannel_message(self, message: Any):
        """handler for messages inbound from relay'ed webrtc connection"""
        if self._go2 is None:
            logger.debug(f"go2 has no data_channel connected to send message to")
            return

        if not isinstance(message, str):
            logger.warning(
                f"Got unexpected data type in datachannel: {type(message)!s}, {message=}"
            )
            return

        # Assume this is a json string and forward it
        await self._go2.publish_json_str(message)

    async def _no_peers_monitor(self):
        time_since_no_peers = time.time()
        while True:
            current_time = time.time()
            if self._peers.num_peers == 0:
                if (
                    current_time - time_since_no_peers
                    > self._settings.RELAY_NO_PEERS_TIMEOUT_SECONDS
                ):
                    if self._go2 is not None:
                        logger.info("no peers for too long, shutting down GO2")
                        await self._go2.shutdown()
                        self._go2 = None
            else:
                time_since_no_peers = current_time
            await asyncio.sleep(10.0)
