import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import Any, TypeAlias
from uuid import UUID

from aiortc import MediaStreamTrack
from aiortc.contrib.media import MediaRelay
from fastapi.security import HTTPBearer

from just_robots.fastapi_utils.fastapi_exceptions import StateException
from just_robots.utils.settings import JustRobotsSettings
from just_robots.webrtc_relay.webrtc_relay_peer import WebRTCRelayPeer

logger = logging.getLogger(__name__)
bearer_auth = HTTPBearer()

ConnectionUUID: TypeAlias = UUID


class WebRTCRelayPeersManager:
    """Manages multiple WebRTC peer connections, allowing multiple connections per user."""

    def __init__(
        self,
        settings: JustRobotsSettings,
        media_relay: MediaRelay,
        on_datachannel_message: Callable[[Any], Coroutine[Any, None, None]],
        on_peer_removed: Callable[[], Coroutine[Any, None, None]] | None = None,
    ):
        self._settings = settings
        self._media_relay = media_relay
        self._on_datachannel_message = on_datachannel_message
        self._on_peer_removed = on_peer_removed

        # Single index by connection_id
        self._peers: dict[ConnectionUUID, WebRTCRelayPeer] = {}

        self._subscribed_topics_superset: set[str] = set()

        self._idle_peers_monitor_task = asyncio.create_task(self._monitor_peers())

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
        self._idle_peers_monitor_task.cancel()
        try:
            await self._idle_peers_monitor_task
        except asyncio.CancelledError:
            pass

        await self._for_each_peer(lambda peer: peer.shutdown())

    @property
    def num_peers(self) -> int:
        return len(self._peers)

    def _get_peer_by_connection_id(
        self, connection_id: ConnectionUUID
    ) -> WebRTCRelayPeer:
        """Get a peer by connection ID, raising StateException if not found."""
        if connection_id not in self._peers:
            raise StateException(f"connection {connection_id} not found")
        return self._peers[connection_id]

    async def add_peer(
        self,
        offer_sdp: str,
        offer_type: str,
        user_firebase_uid: str,
        user_firebase_email: str,
        video_track: MediaStreamTrack | None = None,
    ) -> WebRTCRelayPeer:
        """Create and add a new peer connection. Returns the peer (use peer.connection_id)."""

        peer = await WebRTCRelayPeer.create(
            offer_sdp=offer_sdp,
            offer_type=offer_type,
            user_firebase_uid=user_firebase_uid,
            user_firebase_email=user_firebase_email,
            video_track=video_track,
            on_datachannel_message=self._on_datachannel_message,
        )

        self._peers[peer.connection_id] = peer

        logger.info(
            f"Added peer {peer.connection_id} for {user_firebase_email}, "
            f"total connections: {len(self._peers)}"
        )
        return peer

    async def get_all_peers_subs(self) -> set[str]:
        return self._subscribed_topics_superset

    async def get_connection_subs(self, connection_id: ConnectionUUID) -> set[str]:
        """Get subscriptions for a specific connection."""
        peer = self._get_peer_by_connection_id(connection_id)
        return peer.subscribed_topics.copy()

    async def add_sub_to_connection(
        self, connection_id: ConnectionUUID, topic: str
    ) -> None:
        """Add subscription to a specific connection."""
        peer = self._get_peer_by_connection_id(connection_id)
        peer.last_activity_time = time.time()
        peer.subscribed_topics.add(topic)

        if topic not in self._subscribed_topics_superset:
            self._subscribed_topics_superset.add(topic)

    async def remove_sub_from_connection(
        self, connection_id: ConnectionUUID, topic: str
    ) -> None:
        """Remove subscription from a specific connection."""
        peer = self._get_peer_by_connection_id(connection_id)
        peer.last_activity_time = time.time()
        peer.subscribed_topics.discard(topic)

        self._reset_subscribed_topics()
        if topic in self._subscribed_topics_superset:
            logger.debug(f"topic {topic} still subscribed by other connections")
            return

        logger.info(f"unsubscribing from topic {topic}")

    async def broadcast_to_all_peers(self, raw_message: bytes | str):
        logger.info(f"Broadcasting to {self.num_peers} peers")

        if self.num_peers == 0:
            logger.warning("No peers to broadcast to!")
            return

        for peer in list(self._peers.values()):
            dc_state = peer.data_channel.readyState
            logger.info(
                f"Peer {peer.connection_id} ({peer.user_firebase_email}): "
                f"datachannel state = {dc_state}"
            )

            if dc_state != "open":
                logger.warning(
                    f"Peer {peer.connection_id} has datachannel not open (state={dc_state})"
                )
                continue

            peer.last_activity_time = time.time()
            try:
                peer.data_channel.send(raw_message)
                logger.info(f"Sent message to peer {peer.connection_id}")
            except Exception:
                logger.exception(f"Failed to send to peer {peer.connection_id}")

    async def update_video_track(
        self, media_relay: MediaRelay, video_track: MediaStreamTrack
    ):
        await self._for_each_peer(
            lambda peer: peer.update_video_track(media_relay.subscribe(video_track))
        )

    async def _for_each_peer(self, func: Callable[[WebRTCRelayPeer], Any]):
        await asyncio.gather(
            *[asyncio.to_thread(func, peer) for peer in list(self._peers.values())]
        )

    def _reset_subscribed_topics(self):
        new_set = set[str]()
        for peer in list(self._peers.values()):
            new_set.update(peer.subscribed_topics)
        self._subscribed_topics_superset = new_set

    async def _monitor_peers(self):
        while True:
            await asyncio.sleep(10.0)

            idle_peers: list[WebRTCRelayPeer] = []
            dead_peers: list[WebRTCRelayPeer] = []
            for peer in list(self._peers.values()):
                if (
                    time.time() - peer.last_activity_time
                    > self._settings.RELAY_IDLE_TIMEOUT_SECONDS
                ):
                    idle_peers.append(peer)
                    logger.info(
                        f"removing idle peer {peer.connection_id} ({peer.user_firebase_email})"
                    )
                if peer.is_dead:
                    dead_peers.append(peer)
                    logger.info(
                        f"removing dead peer {peer.connection_id} ({peer.user_firebase_email})"
                    )

            peers_to_remove = idle_peers + dead_peers
            if len(peers_to_remove) > 0:
                for peer in peers_to_remove:
                    if peer.connection_id not in self._peers:
                        logger.debug(f"Peer {peer.connection_id} already removed")
                        return

                    del self._peers[peer.connection_id]
                    logger.info(
                        f"Removed peer {peer.connection_id} "
                        f"({peer.user_firebase_email})"
                    )

                    # NOTE: only call shutdown if the peer is idle, otherwise it will be
                    # called twice
                    if peer in idle_peers:
                        await peer.shutdown()

                self._reset_subscribed_topics()

                # Notify parent that peers were removed (e.g., to restart video blackhole)
                if self._on_peer_removed is not None:
                    await self._on_peer_removed()
