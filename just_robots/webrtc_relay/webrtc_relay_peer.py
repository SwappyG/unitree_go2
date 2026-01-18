from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from logging import getLogger
from types import TracebackType
from typing import Any

from aiortc import (
    MediaStreamTrack,
    RTCDataChannel,
    RTCPeerConnection,
    RTCRtpTransceiver,
    RTCSessionDescription,
)

logger = getLogger(__name__)


class WebRTCRelayPeer:
    def __init__(
        self,
        user_firebase_uid: str,
        user_firebase_email: str,
        peer_connection: RTCPeerConnection,
        data_channel: RTCDataChannel,
        video_transceiver: RTCRtpTransceiver,
    ):
        self.user_firebase_uid = user_firebase_uid
        self.user_firebase_email = user_firebase_email
        self.peer_connection = peer_connection
        self.data_channel = data_channel
        self.video_transceiver = video_transceiver
        self.subscribed_topics = set()
        self.last_activity_time = time.time()

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
        await self.peer_connection.close()
        await asyncio.to_thread(self.data_channel.close)

    def update_video_track(self, video_track: MediaStreamTrack):
        self.video_transceiver.sender.replaceTrack(video_track)

    @staticmethod
    async def create(
        user_firebase_uid: str,
        user_firebase_email: str,
        offer_sdp: str,
        offer_type: str,
        on_datachannel_message: Callable[[Any], Coroutine[Any, None, None]],
        video_track: MediaStreamTrack | None = None,
    ) -> WebRTCRelayPeer:
        logger.info(f"creating new rtc connection to relay data from go2 to caller")

        # TODO (swapnil): Update this with ICE information
        peer_connection = RTCPeerConnection(configuration=None)
        data_channel = peer_connection.createDataChannel("data")
        video_transceiver = peer_connection.addTransceiver(
            "video", direction="recvonly"
        )

        # NOTE: this needs to be created earlier so that the on message callback can update
        # the last activity time on the instance
        self = WebRTCRelayPeer(
            peer_connection=peer_connection,
            data_channel=data_channel,
            video_transceiver=video_transceiver,
            user_firebase_uid=user_firebase_uid,
            user_firebase_email=user_firebase_email,
        )

        def on_datachannel_message_wrapper(message: Any):
            self.last_activity_time = time.time()
            return on_datachannel_message(message)

        data_channel.on("message", on_datachannel_message_wrapper)
        data_channel.on("open", lambda *_: logger.info("relay data channel open"))

        # Attach GO2 video (if present)
        if video_track:
            logger.info(f"adding video track to new relay connection")
            video_transceiver.sender.replaceTrack(video_track)

        # SDP handshake
        logger.info(f"relay RTC setting remote description")
        await peer_connection.setRemoteDescription(
            RTCSessionDescription(sdp=offer_sdp, type=offer_type)
        )
        logger.info(f"relay RTC creating answer")
        answer = await peer_connection.createAnswer()

        logger.info(f"relay RTC setting local description")

        # NOTE: in the newer aiortc versions, answer is always a type.
        # But here in 1.9, it can be None
        # if answer is None:
        #     raise RuntimeError("Failed to create answer")
        await peer_connection.setLocalDescription(answer)  # type: ignore[reportOptionalMemberAccess]

        return self
