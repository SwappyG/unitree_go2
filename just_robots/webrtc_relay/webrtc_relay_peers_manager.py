import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import Any, TypeAlias

from aiortc import MediaStreamTrack, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from fastapi.security import HTTPBearer
from just_robots_firebase_client.firebase_client import FirebaseClient

from just_robots.fastapi_utils.fastapi_exceptions import StateException
from just_robots.utils.settings import JustRobotsSettings
from just_robots.webrtc_relay.webrtc_relay_peer import WebRTCRelayPeer

logger = logging.getLogger(__name__)
bearer_auth = HTTPBearer()

FirebaseUID: TypeAlias = str


class WebRTCRelayPeersManager:
    def __init__(
        self,
        settings: JustRobotsSettings,
        media_relay: MediaRelay,
        on_datachannel_message: Callable[[Any], Coroutine[Any, None, None]],
    ):
        self._settings = settings
        self._firebase_client: FirebaseClient | None = None
        self._media_relay = media_relay
        self._on_datachannel_message = on_datachannel_message
        self._peers: dict[FirebaseUID, WebRTCRelayPeer] = {}
        self._subscribed_topics_superset: set[str] = set()

        self._idle_peers_monitor_task = asyncio.create_task(
            self._monitor_for_idle_peers()
        )

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
    def firebase_client(self) -> FirebaseClient:
        if self._firebase_client is None:
            raise RuntimeError("Firebase client not configured")
        return self._firebase_client

    def num_peers(self) -> int:
        return len(self._peers)

    def update_activity(self, peer: FirebaseUID | WebRTCRelayPeer):
        """Update last activity timestamp. Call this on every user interaction."""
        try:
            if isinstance(peer, FirebaseUID):
                peer = self._peers[peer]
            else:
                peer = self._peers[peer.user_firebase_uid]
        except KeyError:
            logger.warning(f"peer {peer} not found")
            return

        peer.last_activity_time = time.time()

    async def add_peer(
        self,
        offer_sdp: str,
        offer_type: str,
        user_firebase_uid: FirebaseUID,
        user_firebase_email: str,
        video_track: MediaStreamTrack | None = None,
    ) -> RTCSessionDescription:
        if user_firebase_uid in self._peers:
            logger.warning(f"peer {user_firebase_uid} already exists")
            peer = self._peers.pop(user_firebase_uid)
            await peer.shutdown()

        peer = await WebRTCRelayPeer.create(
            offer_sdp=offer_sdp,
            offer_type=offer_type,
            user_firebase_uid=user_firebase_uid,
            user_firebase_email=user_firebase_email,
            video_track=video_track,
            on_datachannel_message=self._on_datachannel_message,
        )
        self._peers[user_firebase_uid] = peer
        return peer.peer_connection.localDescription

    async def remove_peer(self, peer: FirebaseUID | WebRTCRelayPeer):
        try:
            if isinstance(peer, FirebaseUID):
                peer = self._peers.pop(peer)
            else:
                peer = self._peers.pop(peer.user_firebase_uid)
        except KeyError:
            logger.warning(f"peer {peer} already closed")
            return
        await peer.shutdown()
        self._reset_subscribed_topics()

    async def remove_all_peers(self):
        peers = list(self._peers.values())
        for peer in peers:
            await peer.shutdown()
        self._reset_subscribed_topics()

    async def get_all_peers_subs(self) -> set[str]:
        return self._subscribed_topics_superset

    async def get_peer_subs(self, user_firebase_uid: FirebaseUID) -> set[str]:
        if user_firebase_uid not in self._peers:
            logger.warning(f"peer {user_firebase_uid} not found")
            raise StateException(f"peer {user_firebase_uid} not found")
        peer = self._peers[user_firebase_uid]
        return peer.subscribed_topics

    async def add_sub_to_peer(self, user_firebase_uid: FirebaseUID, topic: str):
        if user_firebase_uid not in self._peers:
            logger.warning(f"peer {user_firebase_uid} not found")
            return
        peer = self._peers[user_firebase_uid]
        peer.last_activity_time = time.time()
        if topic in peer.subscribed_topics:
            return
        peer.subscribed_topics.add(topic)
        if topic in self._subscribed_topics_superset:
            return

        self._subscribed_topics_superset.add(topic)

    async def remove_sub_from_peer(self, user_firebase_uid: FirebaseUID, topic: str):
        if user_firebase_uid not in self._peers:
            logger.warning(f"peer {user_firebase_uid} not found")
            return
        peer = self._peers[user_firebase_uid]
        peer.last_activity_time = time.time()
        if topic not in peer.subscribed_topics:
            return
        peer.subscribed_topics.remove(topic)
        self._reset_subscribed_topics()
        if topic in self._subscribed_topics_superset:
            logger.debug(f"topic {topic} still subscribed by other peers")
            return

        logger.info(f"unsubscribing from topic {topic}")

    async def broadcast_to_all_peers(self, raw_message: bytes | str):
        for peer in list(self._peers.values()):
            if peer.data_channel.readyState != "open":
                logger.debug(
                    f"peer {peer.user_firebase_email} has datachannel not open"
                )
                continue

            # TODO (swapnil): should activity be updated on outbound messages?
            peer.last_activity_time = time.time()
            try:
                await self._for_each_peer(
                    lambda peer: asyncio.to_thread(peer.data_channel.send, raw_message)
                )
            except Exception:
                logger.exception(f"Failed to JSON-serialize GO2 message")

    async def update_video_track(
        self, media_relay: MediaRelay, video_track: MediaStreamTrack
    ):
        await self._for_each_peer(
            lambda peer: peer.update_video_track(media_relay.subscribe(video_track))
        )

    async def _for_each_peer(self, func: Callable[[WebRTCRelayPeer], Any]):
        await asyncio.gather(
            *[asyncio.to_thread(func, peer) for peer in self._peers.values()]
        )

    def _reset_subscribed_topics(self):
        new_set = set()
        for peer in self._peers.values():
            new_set.update(peer.subscribed_topics)
        self._subscribed_topics_superset = new_set

    async def _monitor_for_idle_peers(self):
        while True:
            peers_to_remove = []
            peers = dict(self._peers)
            for key, peer in peers.items():
                if (
                    time.time() - peer.last_activity_time
                    > self._settings.RELAY_IDLE_TIMEOUT_SECONDS
                ):
                    peers_to_remove.append(key)
                    logger.info(f"removed idle peer {peer.user_firebase_email}")

            if len(peers_to_remove) > 0:
                for key in peers_to_remove:
                    # its possible the peer was removed between the for loop above and
                    # the pop operation below, so handle key errors
                    try:
                        peer = self._peers.pop(key)
                    except KeyError:
                        logger.warning(f"peer {key} not found")
                        continue
                    await peer.shutdown()
                self._reset_subscribed_topics()
            await asyncio.sleep(10.0)
