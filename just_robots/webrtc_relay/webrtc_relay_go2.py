from __future__ import annotations

import asyncio
import json
import logging
from types import TracebackType

from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from go2_robot_sdk.infrastructure.webrtc.go2_connection import (
    Go2Connection,
    OnMessageCB,
    OnVideoFrameCB,
)

from just_robots.utils.settings import JustRobotsSettings

logger = logging.getLogger(__name__)


class WebRTCRelayGo2:
    def __init__(
        self,
        settings: JustRobotsSettings,
        go2: Go2Connection,
    ):
        self._settings = settings
        self._go2: Go2Connection = go2
        self._subscribed_topics_superset = set[str]()

    @property
    def robot_ip(self) -> str:
        return self._go2.robot_ip

    @staticmethod
    async def create(
        settings: JustRobotsSettings,
        robot_ip: str,
        robot_num: int,
        token: str,
        on_message: OnMessageCB,
        on_video_frame: OnVideoFrameCB,
    ) -> WebRTCRelayGo2:
        # Ordering here is a bit weird. validated and video frame callbacks need to be set on the
        # instance, because they use instance variables (_go2 and _go2_video_track). But this can't
        # be moved to the constructor because go2.connect() needs to be awaited.
        go2 = Go2Connection(
            robot_ip=robot_ip,
            robot_num=robot_num,
            token=token,
            on_open=lambda: logger.info("GO2 data channel open"),
            on_message=on_message,
            on_video_frame=on_video_frame,
            decode_message=True,
            decode_lidar=True,
        )
        self = WebRTCRelayGo2(settings, go2)
        # this has to be after construction, because we need `self`
        go2.on_validated = self._on_go2_validated

        await go2.connect()

        # Wait for WebRTC connection to be fully established
        max_wait_time = 10.0  # Maximum wait time in seconds
        wait_interval = 0.1  # Check every 100ms
        waited = 0.0
        while go2.pc.connectionState != "connected" and waited < max_wait_time:
            await asyncio.sleep(wait_interval)
            waited += wait_interval

        if go2.pc.connectionState != "connected":
            await go2.disconnect()
            raise RuntimeError(
                f"GO2 connection state is {go2.pc.connectionState} after {waited:.1f}s"
            )

        logger.info("GO2 connection established")
        return self

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
        await self._go2.disconnect()
        self._subscribed_topics_superset = set()

    async def send_subscribe_message(self, topic: str):
        await self.publish_json_str(json.dumps({"type": "subscribe", "topic": topic}))

    async def send_unsubscribe_message(self, topic: str):
        await self.publish_json_str(json.dumps({"type": "unsubscribe", "topic": topic}))

    async def publish_json_str(self, json_str: str):
        await self._go2.publish_json_str(json_str)

    async def _on_go2_validated(self, _robot_id: str):
        logger.info("on validated called")
        try:
            await self._go2.disableTrafficSaving(True)
            await self._go2.publish(topic=RTC_TOPIC["ULIDAR_SWITCH"], data="on")
        except Exception:
            logger.exception(f"Error in validated callback")
