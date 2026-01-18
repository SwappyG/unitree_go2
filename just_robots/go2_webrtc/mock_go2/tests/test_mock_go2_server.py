from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest
import requests
import uvicorn
from aiortc import MediaStreamTrack
from go2_robot_sdk.domain.entities.robot_data import RobotData
from go2_robot_sdk.infrastructure.webrtc.go2_connection import Go2Connection

from just_robots.go2_webrtc.mock_go2.mock_go2_server import app

logger = logging.getLogger(__name__)

# HttpClient hardcodes port 9991
TEST_SERVER_PORT = 9991


@pytest.fixture(scope="function")
def mock_server():
    """
    Start a real uvicorn server in a background thread.
    Cleans up automatically after the test.
    """
    # Configuration
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=TEST_SERVER_PORT,
        log_level="warning",  # Reduce noise during tests
        access_log=False,
    )
    server = uvicorn.Server(config)

    # Run server in background thread
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    max_attempts = 50
    for _ in range(max_attempts):
        try:
            response = requests.post(
                f"http://127.0.0.1:{TEST_SERVER_PORT}/con_notify",
                timeout=0.5,
            )
            if response.status_code == 200:
                logger.info(f"Mock server started on port {TEST_SERVER_PORT}")
                break
        except requests.RequestException:
            time.sleep(0.1)
            continue
    else:
        pytest.fail(f"Mock server failed to start on port {TEST_SERVER_PORT}")

    yield TEST_SERVER_PORT

    # Cleanup - stop the server
    try:
        server.should_exit = True
        # Give server a moment to shut down gracefully
        thread.join(timeout=1.0)
        if thread.is_alive():
            logger.warning("Server thread did not exit cleanly, forcing termination")
    except Exception:  # noqa: BLE001
        pass
    logger.info(f"Mock server stopped on port {TEST_SERVER_PORT}")


@pytest.fixture
def validation_event():
    """Create an event to track when validation completes."""
    return asyncio.Event()


@pytest.fixture
def video_track_received():
    """Track if video track was received."""
    return {"received": False, "track": None}


async def test_go2_connection_full_flow(
    mock_server: int,
    validation_event: asyncio.Event,
    video_track_received: dict[str, any],  # type: ignore[type-arg]
):
    """
    Test full Go2Connection flow:
    1. Create offer with video transceiver
    2. Get public key via /con_notify
    3. Encrypt and send SDP via /con_ing/{path_ending}
    4. Receive answer and set remote description
    5. Wait for validation message via datachannel
    6. Verify connection is fully established
    """
    logger.info("Testing full Go2Connection flow with mock_go2_server")

    validation_callback_called = False
    video_frame_callback_called = False
    message_callback_called = False
    open_callback_called = False

    async def on_validated(robot_num: str) -> None:
        """Callback when robot validation is complete."""
        nonlocal validation_callback_called
        logger.info(f"Validation callback called for robot {robot_num}")
        validation_callback_called = True
        validation_event.set()

    async def on_video_frame(track: MediaStreamTrack, robot_num: str) -> None:
        """Callback when video track is received."""
        nonlocal video_frame_callback_called
        logger.info(f"Video frame callback called for robot {robot_num}")
        video_frame_callback_called = True
        video_track_received["received"] = True
        video_track_received["track"] = track
        # In production, this would read frames in a loop, but for testing
        # we just verify the track was received and return immediately
        # The track will be stopped when disconnect() is called

    async def on_message(robot_data: RobotData) -> None:
        """Callback when data channel message is received."""
        nonlocal message_callback_called
        logger.info(f"Message callback called: {robot_data.robot_id}")
        message_callback_called = True

    def on_open() -> None:
        """Callback when data channel opens."""
        nonlocal open_callback_called
        logger.info("Data channel opened")
        open_callback_called = True

    # Create Go2Connection with callbacks
    # Note: robot_ip is "127.0.0.1" but we need to patch the port
    # HttpClient hardcodes port 9991, so we'll need to patch that or use port 9991
    # For now, let's use 9991 and ensure the server uses that port
    go2_conn = Go2Connection(
        robot_ip="127.0.0.1",
        robot_num=1,
        token="",
        on_validated=on_validated,
        on_message=on_message,
        on_open=on_open,
        on_video_frame=on_video_frame,
        decode_lidar=False,
        decode_message=False,
    )

    try:
        # Start connection (HttpClient already uses port 9991, no patching needed)
        logger.info("Starting Go2Connection.connect()")
        await go2_conn.connect()

        # Wait for validation (with timeout)
        logger.info("Waiting for validation to complete...")
        try:
            await asyncio.wait_for(validation_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pytest.fail("Validation did not complete within 10 seconds")

        # Verify connection is established
        assert go2_conn.is_validated, "Connection should be validated"
        assert validation_callback_called, "Validation callback should have been called"

        # Verify data channel is open
        assert go2_conn.data_channel.readyState == "open", "Data channel should be open"

        # Verify open callback was called
        assert open_callback_called, "Open callback should have been called"

        # Verify video track was received (if video callback was provided)
        # Note: video might arrive after validation, so we wait a bit
        await asyncio.sleep(1.0)
        assert video_frame_callback_called or video_track_received["received"], (
            "Video frame callback should have been called"
        )

        logger.info("Successfully verified full Go2Connection flow")

    finally:
        # Cleanup - stop the track if it was received
        if video_track_received.get("track"):
            try:
                video_track_received["track"].stop()
            except Exception:  # noqa: BLE001
                pass
        # Disconnect
        await go2_conn.disconnect()
        # Give a moment for cleanup to complete
        await asyncio.sleep(0.1)


# async def test_go2_connection_without_video(
#     mock_server: int,
#     validation_event: asyncio.Event,
# ):
#     """
#     Test Go2Connection flow without video callback to ensure it still works.
#     """
#     logger.info("Testing Go2Connection flow without video callback")

#     validation_callback_called = False

#     async def on_validated(robot_num: str) -> None:
#         nonlocal validation_callback_called
#         logger.info(f"Validation callback called for robot {robot_num}")
#         validation_callback_called = True
#         validation_event.set()

#     # Create Go2Connection without video callback
#     go2_conn = Go2Connection(
#         robot_ip="127.0.0.1",
#         robot_num=1,
#         token="",
#         on_validated=on_validated,
#         on_message=None,
#         on_open=None,
#         on_video_frame=None,  # No video callback
#         decode_lidar=False,
#         decode_message=False,
#     )

#     try:
#         # Start connection (HttpClient already uses port 9991, no patching needed)
#         await go2_conn.connect()

#         # Wait for validation
#         try:
#             await asyncio.wait_for(validation_event.wait(), timeout=10.0)
#         except asyncio.TimeoutError:
#             pytest.fail("Validation did not complete within 10 seconds")

#         # Verify connection is established
#         assert go2_conn.is_validated, "Connection should be validated"
#         assert validation_callback_called, "Validation callback should have been called"

#         logger.info("Successfully verified Go2Connection flow without video")

#     finally:
#         await go2_conn.disconnect()
