"""Qt-based test for WebRTCRelayClientQt2.

This mirrors webrtc_relay_client_test.py but uses the Qt-based client.
"""

import asyncio
import signal
import sys
import threading
import typing as t
from uuid import uuid4

import numpy as np
from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from go2_robot_sdk.domain.entities.robot_data import RobotData
from just_robots_firebase_client.qt.firebase_client_qt import FirebaseClientQt
from just_robots_firebase_client.qt.firebase_client_qt_authenticated import (
    FirebaseClientQtAuthenticated,
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

from just_robots.utils.logging import logging
from just_robots.utils.package_paths import get_package_root
from just_robots.utils.settings import get_just_robots_settings
from just_robots.webrtc_relay_client.webrtc_relay_client_qt import (
    WebRTCRelayClientQt,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

WINDOW_TITLE = f"WebRTC Relay Video Feed (Qt) {uuid4().hex[:6]}"


class VideoWindow(QMainWindow):
    """Main window for displaying video feed."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(640, 480)

        # Central widget with layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Video display label
        self._video_label = QLabel()
        self._video_label.setMinimumSize(320, 240)
        self._video_label.setStyleSheet("background-color: black;")
        self._video_label.setScaledContents(True)
        layout.addWidget(self._video_label)

        # Status label
        self._status_label = QLabel("Connecting...")
        self._status_label.setStyleSheet("padding: 5px;")
        layout.addWidget(self._status_label)

    def set_status(self, status: str) -> None:
        """Update the status label."""
        self._status_label.setText(status)

    def display_frame(self, frame: np.ndarray[t.Any, np.dtype[np.uint8]]) -> None:
        """Display a video frame (RGB numpy array)."""
        height, width, channels = frame.shape
        bytes_per_line = channels * width

        # Create QImage from numpy array (RGB format)
        image = QImage(
            frame.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGB888,
        )

        # Convert to pixmap and display
        pixmap = QPixmap.fromImage(image)
        self._video_label.setPixmap(pixmap)


class WebRTCRelayQtTest:
    """Test harness for WebRTCRelayClientQt."""

    def __init__(
        self, app: QApplication, window: VideoWindow, loop: asyncio.AbstractEventLoop
    ) -> None:
        self._app = app
        self._window = window
        self._loop = loop
        self._firebase_client: FirebaseClientQt | None = None
        self._firebase_auth: FirebaseClientQtAuthenticated | None = None
        self._relay_client: WebRTCRelayClientQt | None = None
        self._settings = get_just_robots_settings()

    def start(self) -> None:
        """Start the test by signing in to Firebase."""
        config_path = get_package_root() / self._settings.FIREBASE_CONFIG_PATH

        # Create Firebase client
        self._firebase_client = FirebaseClientQt.from_config(config_path)

        # Connect sign-in result signal
        self._firebase_client.sign_in_with_email_password_result.connect(
            self._on_sign_in_result
        )
        self._firebase_client.error.connect(self._on_firebase_error)

        # Sign in
        email = "abc@gmail.com"
        password = "123456"  # noqa: S105
        logger.info(f"Signing in as {email}...")
        self._firebase_client.sign_in_with_email_and_password(email, password)

    def _on_sign_in_result(self, reply: t.Any) -> None:
        """Handle successful sign-in."""
        logger.info(f"Sign-in successful: {reply.email}")

        if self._firebase_client is None:
            logger.error("Firebase client is None")
            return

        # Create authenticated client
        self._firebase_auth = (
            FirebaseClientQtAuthenticated.from_sign_in_with_email_and_password_reply(
                client=self._firebase_client,
                reply=reply,
            )
        )

        # Now create the WebRTC relay client
        self._create_relay_client()

    def _on_firebase_error(self, url: str, error: str) -> None:
        """Handle Firebase error."""
        logger.error(f"Firebase error at {url}: {error}")
        self._app.quit()

    def _create_relay_client(self) -> None:
        """Create and start the WebRTC relay client."""
        logger.info("Creating WebRTC relay client...")

        if self._firebase_auth is None:
            logger.error("Firebase auth is None")
            return

        self._relay_client = WebRTCRelayClientQt(
            relay_url="http://localhost:8000",
            go2_ip_address="localhost",
            firebase_client=self._firebase_auth,
            loop=self._loop,
            parent=self._app,
        )

        # Connect signals
        self._relay_client.connect_to_go2_complete.connect(
            self._on_connect_to_go2_complete
        )
        self._relay_client.start_relay_complete.connect(self._on_start_relay_complete)
        self._relay_client.robot_data_received.connect(self._on_robot_data)
        self._relay_client.video_frame_received.connect(self._on_video_frame)
        self._relay_client.lidar_frame_received.connect(self._on_lidar_frame)
        self._relay_client.error.connect(self._on_relay_error)

        # Start connecting
        logger.info("Connecting to Go2...")
        self._relay_client.connect_to_go2()

    def _on_connect_to_go2_complete(self) -> None:
        """Handle successful Go2 connection."""
        logger.info("Connected to Go2! Starting relay...")
        self._window.set_status("Connected to Go2, starting relay...")
        if self._relay_client is not None:
            self._relay_client.start_relay()

    def _on_start_relay_complete(self) -> None:
        """Handle successful relay start."""
        logger.info("Relay started!")

        # Subscribe to topics
        if self._relay_client is not None:
            self._relay_client.add_topic_to_subscriptions(RTC_TOPIC["LOW_STATE"])
            self._relay_client.add_topic_to_subscriptions(RTC_TOPIC["ULIDAR_ARRAY"])

        # Update status
        self._window.set_status("Connected - receiving data...")
        logger.info("WebRTC relay is active. Video frames should start arriving.")

    def _on_robot_data(self, robot_data: RobotData) -> None:
        """Handle robot data."""
        if robot_data.lidar_data is not None:
            if robot_data.lidar_data.compressed_data is not None:
                logger.info(f"Lidar data: {robot_data.lidar_data.compressed_data[:20]}")
            else:
                logger.info(f"Lidar data: {robot_data.lidar_data.positions[:20]}")
        else:
            logger.info(f"Robot data: {robot_data}")

    def _on_video_frame(self, frame: np.ndarray[t.Any, np.dtype[np.uint8]]) -> None:
        """Handle video frame (numpy array RGB)."""
        self._window.display_frame(frame)

    def _on_lidar_frame(self, lidar_frame: dict[str, t.Any]) -> None:
        """Handle lidar frame."""
        logger.info(f"Lidar frame received (keys: {list(lidar_frame.keys())})")

    def _on_relay_error(self, error: str) -> None:
        """Handle relay error."""
        logger.error(f"Relay error: {error}")

    def shutdown(self) -> None:
        """Shutdown the test."""
        logger.info("Shutting down...")

        # Stop Firebase token refresh
        if self._firebase_auth:
            self._firebase_auth.stop()

        # Quit the app (this will close the window)
        self._app.quit()


def run_asyncio_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Run the asyncio event loop in a background thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def main() -> None:
    """Main entry point."""
    # Create Qt application
    app = QApplication(sys.argv)

    # Create and show the video window
    window = VideoWindow()
    window.show()

    # Create asyncio event loop in a background thread
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=run_asyncio_loop, args=(loop,), daemon=True)
    loop_thread.start()

    # Create test harness
    test = WebRTCRelayQtTest(app, window, loop)

    # Start the test after event loop is ready
    QTimer.singleShot(100, test.start)

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda *_: test.shutdown())

    # Run Qt event loop
    exit_code = app.exec()

    # Stop asyncio loop
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=2.0)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
