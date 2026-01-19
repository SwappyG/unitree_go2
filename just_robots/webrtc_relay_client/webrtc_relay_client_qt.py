"""Qt wrapper for WebRTCRelayClient using composition pattern.

This wraps the async WebRTCRelayClient and provides Qt signals for all callbacks
and results. The asyncio event loop is passed in from outside.
"""

from __future__ import annotations

import asyncio
import logging
import typing as t

from aiortc import MediaStreamTrack, RTCConfiguration
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame  # type: ignore[import-untyped]
from go2_robot_sdk.domain.entities.robot_data import RobotData
from just_robots_firebase_client.qt.firebase_client_qt_authenticated import (
    FirebaseClientQtAuthenticated,
)
from PySide6.QtCore import QObject, Signal
from PySide6.QtCore import QObject as QObjectType
from PySide6.QtWidgets import QApplication

from just_robots.qt.qt_gui_invoker import QtGuiInvoker
from just_robots.webrtc_relay_client.webrtc_relay_client import WebRTCRelayClient

logger = logging.getLogger(__name__)


class _FirebaseClientAsyncAdapter:
    """Adapts FirebaseClientQtAuthenticated to implement AuthTokenProvider protocol.

    The Qt firebase client's get_id_token() is synchronous and returns the cached token,
    so wrapping it in an async function is trivial.
    """

    def __init__(self, qt_client: FirebaseClientQtAuthenticated) -> None:
        self._qt_client = qt_client

    async def get_id_token(self) -> str:
        """Get the current ID token (async wrapper around sync method)."""
        return self._qt_client.get_id_token()


class WebRTCRelayClientQt(QObject):
    """Qt wrapper around WebRTCRelayClient.

    This uses composition to wrap the async client and provides Qt signals
    for all callbacks and results. The asyncio event loop must be passed in
    and should be running in a background thread.

    Signals:
        connect_to_go2_complete: Emitted when connect_to_go2() succeeds
        disconnect_from_go2_complete: Emitted when disconnect_from_go2() succeeds
        start_relay_complete: Emitted when start_relay() succeeds
        shutdown_complete: Emitted when shutdown() completes
        robot_data_received: Emitted when robot data is received (RobotData)
        video_frame_received: Emitted when a video frame is received (numpy array RGB)
        lidar_frame_received: Emitted when lidar data is received (dict)
        error: Emitted when an error occurs (str message)
    """

    # Result signals
    connect_to_go2_complete = Signal()
    disconnect_from_go2_complete = Signal()
    start_relay_complete = Signal()

    # Data callback signals
    robot_data_received = Signal(object)  # RobotData
    video_frame_received = Signal(object)  # numpy array (RGB)
    lidar_frame_received = Signal(object)  # dict

    # Error signal
    error = Signal(str)

    def __init__(
        self,
        relay_url: str,
        go2_ip_address: str,
        firebase_client: FirebaseClientQtAuthenticated,
        loop: asyncio.AbstractEventLoop,
        go2_token: str = "",
        parent: QObjectType | None = None,
    ) -> None:
        """Initialize the Qt WebRTC relay client.

        Args:
            relay_url: URL of the WebRTC relay server
            go2_ip_address: IP address of the Go2 robot
            firebase_client: Qt-based Firebase authenticated client
            loop: Asyncio event loop running in a background thread
            go2_token: Optional token for Go2 authentication
            parent: Parent QObject
        """
        super().__init__(parent)

        self._loop = loop

        # Create GUI invoker for marshalling callbacks to Qt thread
        self._invoker = QtGuiInvoker.make_invoker_on_gui_thread()

        # Create async auth adapter
        auth_adapter = _FirebaseClientAsyncAdapter(firebase_client)

        # Create the wrapped async client
        self._client = WebRTCRelayClient(
            relay_url=relay_url,
            go2_ip_address=go2_ip_address,
            on_robot_data=self._on_robot_data,
            on_video_track=self._on_video_track,
            on_lidar_frame=self._on_lidar_frame,
            auth_provider=auth_adapter,
            go2_token=go2_token,
        )

        # Video frame reading task
        self._video_task: asyncio.Task[None] | None = None

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_about_to_quit)

    # -------------------------------------------------------------------------
    # Public API - called from GUI thread, schedules work on asyncio thread
    # -------------------------------------------------------------------------

    def connect_to_go2(self) -> None:
        """Instruct the relay server to connect to the Go2 robot."""
        asyncio.run_coroutine_threadsafe(self._connect_to_go2_async(), self._loop)

    def disconnect_from_go2(self) -> None:
        """Instruct the relay server to disconnect from the Go2 robot."""
        asyncio.run_coroutine_threadsafe(self._disconnect_from_go2_async(), self._loop)

    def start_relay(self, rtc_configuration: RTCConfiguration | None = None) -> None:
        """Start the WebRTC relay connection."""
        asyncio.run_coroutine_threadsafe(
            self._start_relay_async(rtc_configuration), self._loop
        )

    def move(
        self, forward_velocity: float, strafe_velocity: float, rotation_velocity: float
    ) -> None:
        """Set robot velocities. Must be sent frequently to maintain movement."""
        asyncio.run_coroutine_threadsafe(
            self._client.move(forward_velocity, strafe_velocity, rotation_velocity),
            self._loop,
        )

    def gaze(self, roll_angle: float, pitch_angle: float, yaw_angle: float) -> None:
        """Cause robot to look towards specified angles. 0,0,0 is forward."""
        asyncio.run_coroutine_threadsafe(
            self._client.gaze(roll_angle, pitch_angle, yaw_angle), self._loop
        )

    def stand_up(self) -> None:
        """Cause robot to stand up if sitting."""
        asyncio.run_coroutine_threadsafe(self._client.stand_up(), self._loop)

    def lie_down_on_belly(self) -> None:
        """Robot folds legs to rest on belly."""
        asyncio.run_coroutine_threadsafe(self._client.lie_down_on_belly(), self._loop)

    def sit_on_hind_legs(self) -> None:
        """Robot sits on hind legs like a real dog."""
        asyncio.run_coroutine_threadsafe(self._client.sit_on_hind_legs(), self._loop)

    def stand_up_from(self) -> None:
        """Robot stands up from sitting position."""
        asyncio.run_coroutine_threadsafe(self._client.stand_up_from(), self._loop)

    def recovery_stand(self) -> None:
        """Robot stands up from any position."""
        asyncio.run_coroutine_threadsafe(self._client.recovery_stand(), self._loop)

    def balance_stand(self) -> None:
        """Robot performs balance stand."""
        asyncio.run_coroutine_threadsafe(self._client.balance_stand(), self._loop)

    def stop_move(self) -> None:
        """Stop robot movement."""
        asyncio.run_coroutine_threadsafe(self._client.stop_move(), self._loop)

    def change_obstacle_avoid_state(self, enabled: bool) -> None:
        """Enable or disable obstacle avoidance."""
        asyncio.run_coroutine_threadsafe(
            self._client.change_obstacle_avoid_state(enabled), self._loop
        )

    def add_topic_to_subscriptions(self, topic: str) -> None:
        """Add a topic to subscriptions."""
        asyncio.run_coroutine_threadsafe(
            self._client.add_topic_to_subscriptions(topic), self._loop
        )

    def remove_topic_from_subscriptions(self, topic: str) -> None:
        """Remove a topic from subscriptions."""
        asyncio.run_coroutine_threadsafe(
            self._client.remove_topic_from_subscriptions(topic), self._loop
        )

    # -------------------------------------------------------------------------
    # Internal async methods - run on asyncio thread
    # -------------------------------------------------------------------------

    async def _connect_to_go2_async(self) -> None:
        """Async implementation of connect_to_go2."""
        try:
            await self._client.connect_to_go2()
            self._invoker.call.emit(lambda: self.connect_to_go2_complete.emit())
        except Exception as e:
            logger.exception(f"Error in connect_to_go2")
            self._invoker.call.emit(lambda e=e: self.error.emit(str(e)))

    async def _disconnect_from_go2_async(self) -> None:
        """Async implementation of disconnect_from_go2."""
        try:
            await self._client.disconnect_from_go2()
            self._invoker.call.emit(lambda: self.disconnect_from_go2_complete.emit())
        except Exception as e:
            logger.exception(f"Error in disconnect_from_go2")
            self._invoker.call.emit(lambda e=e: self.error.emit(str(e)))

    async def _start_relay_async(
        self, rtc_configuration: RTCConfiguration | None
    ) -> None:
        """Async implementation of start_relay."""
        try:
            await self._client.start_relay(rtc_configuration)
            self._invoker.call.emit(lambda: self.start_relay_complete.emit())
        except Exception as e:
            logger.exception(f"Error in start_relay")
            self._invoker.call.emit(lambda e=e: self.error.emit(str(e)))

    # -------------------------------------------------------------------------
    # Callbacks from WebRTCRelayClient - run on asyncio thread
    # -------------------------------------------------------------------------

    async def _on_robot_data(self, data: RobotData) -> None:
        """Handle robot data from the relay. Emits signal on GUI thread."""
        # Capture data by value in lambda
        self._invoker.call.emit(lambda data=data: self.robot_data_received.emit(data))

    async def _on_video_track(self, track: MediaStreamTrack) -> None:
        """Handle video track. Starts reading frames and emitting them as numpy arrays."""
        logger.info(f"Received video track: {track}")

        # Start a task to read frames from the track
        self._video_task = asyncio.create_task(self._read_video_frames(track))

    async def _read_video_frames(self, track: MediaStreamTrack) -> None:
        """Read frames from video track and emit them to GUI thread."""
        try:
            while True:
                frame = await track.recv()
                # Convert to numpy array for OpenCV
                if not isinstance(frame, VideoFrame):
                    logger.warning(f"Unexpected frame type: {type(frame)}")
                    continue
                # Convert to numpy array (RGB format)
                img = frame.to_ndarray(format="rgb24")
                # Emit on GUI thread, capture img by value
                self._invoker.call.emit(
                    lambda img=img: self.video_frame_received.emit(img)
                )
        except MediaStreamError:
            logger.info("Video track ended")
        except asyncio.CancelledError:
            logger.info("Video frame reading cancelled")
        except Exception as e:
            logger.exception(f"Error reading video frames")
            self._invoker.call.emit(lambda e=e: self.error.emit(f"Video error: {e}"))

    async def _on_lidar_frame(self, frame: dict[str, t.Any]) -> None:
        """Handle lidar data from the relay. Emits signal on GUI thread."""
        # Capture frame by value in lambda
        self._invoker.call.emit(
            lambda frame=frame: self.lidar_frame_received.emit(frame)
        )

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def _on_about_to_quit(self) -> None:
        """Shutdown the client and clean up resources."""
        logger.info("Destroying WebRTC relay client")
        # Schedule async shutdown (handles video task cancellation internally)
        future = asyncio.run_coroutine_threadsafe(
            self._on_about_to_quit_async(), self._loop
        )

        try:
            future.result(timeout=5.0)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error during shutdown: {e}")

    async def _on_about_to_quit_async(self) -> None:
        """Async shutdown - runs on asyncio thread."""
        # Cancel video task from within the asyncio thread
        logger.info("Cancelling video task")
        if self._video_task is not None:
            self._video_task.cancel()
            try:
                await self._video_task  # Wait for cancellation to complete
            except asyncio.CancelledError:
                pass
            self._video_task = None

        # Now shutdown the client
        logger.info("Shutting down client")
        await self._client.shutdown()
