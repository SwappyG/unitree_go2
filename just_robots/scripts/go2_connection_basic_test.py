# ruff: noqa: PLW0603 # global variables, ok since this is just a test script
"""
Simple test script to verify Go2Connection works with mock_go2_server.

Prerequisites:
- mock_go2_server must be running on localhost:9991
  Run: python -m just_robots.scripts.mock_go2_server

Usage:
  python -m just_robots.scripts.run_go2_connection
"""

import asyncio
import json
import logging

import cv2
from aiortc import MediaStreamTrack
from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from go2_robot_sdk.domain.entities.robot_data import RobotData
from go2_robot_sdk.infrastructure.webrtc.go2_connection import Go2Connection

from just_robots.scripts.helpers.simple_media_stream_display import (
    SimpleMediaStreamDisplay,
)

# Configure logging with detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Constants
MOCK_GO2_IP = "localhost"
MOCK_GO2_PORT = 9991  # Note: Go2Connection uses port 9991 by default
ROBOT_NUM = 1

# Topics to subscribe to
SUBSCRIBE_TOPIC_ODOM = RTC_TOPIC["ROBOTODOM"]  # "rt/utlidar/robot_pose"
SUBSCRIBE_TOPIC_LIDAR = RTC_TOPIC["ULIDAR_ARRAY"]  # "rt/utlidar/voxel_map_compressed"

# Video display settings
VIDEO_WINDOW_NAME = "Go2 Video Feed"


# Global Variables ---------------------------------------------------------------------
# Events to signal when callbacks are called
validated_event = asyncio.Event()
message_received_event = asyncio.Event()
video_frame_received_event = asyncio.Event()
open_event = asyncio.Event()

# Storage for callback data
validated_robot_id: str | None = None
received_messages: list[RobotData] = []
lidar_message_count = 0
media_stream_display = SimpleMediaStreamDisplay(VIDEO_WINDOW_NAME)
video_display_task: asyncio.Task[None] | None = None
# --------------------------------------------------------------------------------------


def on_open():
    logger.info(">>> CALLBACK: on_open - Data channel is open!")
    open_event.set()


async def on_validated(robot_id: str):
    global validated_robot_id
    logger.info(f">>> CALLBACK: on_validated - Robot {robot_id} validated!")
    validated_robot_id = robot_id
    validated_event.set()


async def on_message(robot_data: RobotData):
    global lidar_message_count
    raw_msg = robot_data.raw_message

    # Check if it's binary data (lidar)
    if isinstance(raw_msg, bytes):
        lidar_message_count += 1
        # Print first 30 bytes in hex
        preview = raw_msg[:30].hex()
        logger.info(
            f">>> CALLBACK: on_message - LIDAR data #{lidar_message_count}, "
            f"len={len(raw_msg)}, first 30 bytes: {preview}"
        )
    else:
        # JSON message
        msg_preview = str(raw_msg)[:150] if raw_msg else "None"
        logger.info(
            f">>> CALLBACK: on_message - JSON from robot {robot_data.robot_id}: {msg_preview}..."
        )

    received_messages.append(robot_data)
    message_received_event.set()


async def on_video_frame(track: MediaStreamTrack, robot_id: str):
    global video_display_task
    logger.info(f">>> CALLBACK: on_video_frame - Got video track for robot {robot_id}")
    video_frame_received_event.set()

    # Spawn a background task to handle video display
    video_display_task = asyncio.create_task(
        media_stream_display.video_display_loop(track)
    )


async def main():
    logger.info("=" * 60)
    logger.info("Starting Go2Connection test script (with video + lidar)")
    logger.info("=" * 60)

    # Create Go2Connection with video enabled
    logger.info(f"Creating Go2Connection to {MOCK_GO2_IP}:{MOCK_GO2_PORT}")
    logger.info("Video: ENABLED, Lidar: ENABLED")
    go2 = Go2Connection(
        robot_ip=MOCK_GO2_IP,
        robot_num=ROBOT_NUM,
        token="",
        on_open=on_open,
        on_validated=on_validated,
        on_message=on_message,
        on_video_frame=on_video_frame,  # Enable video
        decode_lidar=True,  # Enable lidar decoding
        decode_message=True,  # Decode messages
    )

    try:
        # Connect
        logger.info("-" * 60)
        logger.info("Step 1: Connecting to mock_go2...")
        logger.info("-" * 60)
        await go2.connect()
        logger.info("Connection established (SDP exchange complete)")

        # Wait for validation
        logger.info("-" * 60)
        logger.info("Step 2: Waiting for validation...")
        logger.info("-" * 60)
        try:
            await asyncio.wait_for(validated_event.wait(), timeout=10.0)
            logger.info(f"Validation complete! Robot ID: {validated_robot_id}")
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for validation!")  # noqa: TRY400
            logger.info(f"is_validated flag: {go2.is_validated}")
            logger.info(f"data_channel state: {go2.data_channel.readyState}")
            raise

        # Subscribe to odometry topic
        logger.info("-" * 60)
        logger.info(f"Step 3a: Subscribing to odom topic: {SUBSCRIBE_TOPIC_ODOM}")
        logger.info("-" * 60)
        subscribe_msg = json.dumps({"type": "subscribe", "topic": SUBSCRIBE_TOPIC_ODOM})
        await go2.publish_json_str(subscribe_msg)
        logger.info("Odom subscribe message sent")

        # Subscribe to lidar topic
        logger.info("-" * 60)
        logger.info(f"Step 3b: Subscribing to lidar topic: {SUBSCRIBE_TOPIC_LIDAR}")
        logger.info("-" * 60)
        subscribe_msg = json.dumps(
            {"type": "subscribe", "topic": SUBSCRIBE_TOPIC_LIDAR}
        )
        await go2.publish_json_str(subscribe_msg)
        logger.info("Lidar subscribe message sent")

        # Wait for video frame
        logger.info("-" * 60)
        logger.info("Step 4: Waiting for video frame...")
        logger.info("-" * 60)
        try:
            await asyncio.wait_for(video_frame_received_event.wait(), timeout=10.0)
            logger.info("Video track received!")
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout waiting for video frame (video might not be enabled)"
            )

        # Wait for messages
        logger.info("-" * 60)
        logger.info("Step 5: Waiting for messages...")
        logger.info("-" * 60)
        try:
            await asyncio.wait_for(message_received_event.wait(), timeout=15.0)
            logger.info(f"Message received! Total messages: {len(received_messages)}")
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for messages")
            logger.info(f"Total messages received: {len(received_messages)}")

        # Wait for more data
        logger.info("-" * 60)
        logger.info("Step 6: Running for 10 more seconds to collect data...")
        logger.info("        Press 'q' in video window to stop early")
        logger.info("-" * 60)
        await asyncio.sleep(10.0)

        # Summary
        logger.info("=" * 60)
        logger.info("Test Summary:")
        logger.info(f"  - Total messages received: {len(received_messages)}")
        logger.info(f"  - Lidar messages: {lidar_message_count}")
        logger.info(f"  - Video frames: {media_stream_display.video_frame_count}")
        logger.info("=" * 60)
        logger.info("Test completed successfully!")
        logger.info("=" * 60)

    except Exception:
        logger.exception(f"Error during test")
        raise

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

        # Always disconnect
        logger.info("-" * 60)
        logger.info("Step 7: Disconnecting...")
        logger.info("-" * 60)
        try:
            await go2.disconnect()
            logger.info("Disconnected successfully")
        except Exception:
            logger.exception("Error during disconnect")

        logger.info("=" * 60)
        logger.info("Script finished")
        logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
