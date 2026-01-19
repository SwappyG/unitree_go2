# ruff: noqa: PLW0603

import asyncio
import getpass  # noqa: F401
import typing as t
from uuid import uuid4

import cv2
from aiortc import MediaStreamTrack
from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from go2_robot_sdk.domain.entities.robot_data import RobotData
from just_robots_firebase_client.firebase_client_authenticated import (
    FirebaseClientAuthenticated,
)

from just_robots.scripts.helpers.simple_media_stream_display import (
    SimpleMediaStreamDisplay,
)
from just_robots.utils.logging import logging
from just_robots.utils.package_paths import get_package_root
from just_robots.utils.settings import get_just_robots_settings
from just_robots.webrtc_relay_client.webrtc_relay_client import WebRTCRelayClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Global state for video display
VIDEO_WINDOW_NAME = f"WebRTC Relay Video Feed {uuid4().hex[:6]}"
media_stream_display = SimpleMediaStreamDisplay(VIDEO_WINDOW_NAME)
video_display_task: asyncio.Task[None] | None = None


async def on_robot_data(robot_data: RobotData):
    if robot_data.lidar_data is not None:
        if robot_data.lidar_data.compressed_data is not None:
            logger.info(f"Lidar data: {robot_data.lidar_data.compressed_data[:20]}")
        else:
            logger.info(f"Lidar data: {robot_data.lidar_data.positions[:20]}")
    else:
        logger.info(f"Robot data: {robot_data}")


async def on_video_track(video_track: MediaStreamTrack):
    global video_display_task
    logger.info(f"Video track received: {video_track}")

    # Spawn a background task to handle video display
    video_display_task = asyncio.create_task(
        media_stream_display.video_display_loop(video_track)
    )


async def on_lidar_frame(lidar_frame: dict[str, t.Any]):
    logger.info(f"Lidar frame: {lidar_frame}")


async def main():
    _settings = get_just_robots_settings()
    # email = getpass.getpass("Enter your email: ")
    # password = getpass.getpass("Enter your password: ")
    email = "abc@gmail.com"
    password = "123456"  # noqa: S105
    auth_cli = (
        await FirebaseClientAuthenticated.from_sign_in_with_email_and_password_reply(
            config=get_package_root() / _settings.FIREBASE_CONFIG_PATH,
            email=email,
            password=password,
        )
    )

    try:
        async with WebRTCRelayClient(
            relay_url="http://localhost:8000",
            go2_ip_address="localhost",
            on_robot_data=on_robot_data,
            on_video_track=on_video_track,
            on_lidar_frame=on_lidar_frame,
            auth_provider=auth_cli,
        ) as client:
            await client.connect_to_go2()
            await client.start_relay()
            await client.add_topic_to_subscriptions(RTC_TOPIC["LOW_STATE"])

            logger.info("Press Enter to exit (or 'q' in video window)...")
            await asyncio.to_thread(lambda: input())

    finally:
        # Cancel video display task if running
        if video_display_task is not None and not video_display_task.done():
            logger.info("Cancelling video display task...")
            video_display_task.cancel()
            try:
                await video_display_task
            except asyncio.CancelledError:
                pass

        # Cleanup video window
        cv2.destroyAllWindows()


if __name__ == "__main__":
    settings = get_just_robots_settings()
    asyncio.run(main())
