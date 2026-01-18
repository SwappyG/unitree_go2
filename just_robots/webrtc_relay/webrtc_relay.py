import asyncio
import dataclasses
import logging
import time
from types import TracebackType
from typing import Any, TypeAlias

from aiortc import MediaStreamTrack, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from go2_robot_sdk.domain.entities.robot_data import RobotData
from just_robots_firebase_client.firebase_client import FirebaseClient

from just_robots.utils.settings import JustRobotsSettings, get_just_robots_settings
from just_robots.webrtc_relay.webrtc_relay_go2 import WebRTCRelayGo2
from just_robots.webrtc_relay.webrtc_relay_peer import WebRTCRelayPeer
from just_robots.webrtc_relay.webrtc_relay_peers_manager import WebRTCRelayPeersManager

logger = logging.getLogger(__name__)


FirebaseUID: TypeAlias = str


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
        self._media_relay: MediaRelay = dataclasses.field(default_factory=MediaRelay)
        self._go2: WebRTCRelayGo2 | None = None
        self._go2_video_track: MediaStreamTrack | None = None
        self._peers = WebRTCRelayPeersManager(
            settings=self._settings,
            media_relay=self._media_relay,
            on_datachannel_message=self._on_datachannel_message,
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
    ) -> RTCSessionDescription:
        return await self._peers.add_peer(
            offer_sdp=offer_sdp,
            offer_type=offer_type,
            user_firebase_uid=user_firebase_uid,
            user_firebase_email=user_firebase_email,
            video_track=self._go2.video_track if self._go2 is not None else None,
        )

    async def remove_peer(self, peer: FirebaseUID | WebRTCRelayPeer):
        await self._peers.remove_peer(peer)

    async def remove_all_peers(self):
        await self._peers.remove_all_peers()

    async def get_subs_for_peer(self, user_firebase_uid: FirebaseUID) -> set[str]:
        return await self._peers.get_peer_subs(user_firebase_uid)

    async def add_sub_for_peer(self, user_firebase_uid: FirebaseUID, topic: str):
        if self._go2 is None:
            logger.warning("GO2 connection not established")
            return
        await self._peers.add_sub_to_peer(user_firebase_uid, topic)
        await self._go2.send_subscribe_message(topic)

    async def remove_sub_from_peer(self, user_firebase_uid: FirebaseUID, topic: str):
        if self._go2 is None:
            logger.warning("GO2 connection not established")
            return
        await self._peers.remove_sub_from_peer(user_firebase_uid, topic)
        await self._go2.send_unsubscribe_message(topic)

    async def _on_go2_message(self, robot_data: RobotData):
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
        if self._go2_video_track is not None:
            # TODO (swapnil): determine if the proxy tracks need to be manually stopped
            self._go2_video_track.stop()
            self._media_relay = MediaRelay()

        self._go2_video_track = track
        await self._peers.update_video_track(self._media_relay, track)

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
            if self._peers.num_peers() == 0:
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
