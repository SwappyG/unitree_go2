import asyncio
import threading
from fractions import Fraction
from logging import getLogger

import cv2
import numpy as np
from aiortc import MediaStreamTrack
from av.video.frame import VideoFrame

logger = getLogger(__name__)


class MockGo2WebcamVideoTrack(MediaStreamTrack):
    """Video track that reads from webcam and displays it locally."""

    kind = "video"

    def __init__(
        self, camera_index: int = 0, width: int = 640, height: int = 480, fps: int = 30
    ):
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.enabled = False

        # Open webcam
        logger.info(f"Attempting to open camera {camera_index}...")
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"Failed to open camera {camera_index}. Make sure the camera is connected and not being used by another application."
            )

        # Set camera properties
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        # Test reading a frame to verify webcam works
        ret, test_frame = self.cap.read()
        if not ret:
            self.cap.release()
            raise RuntimeError(
                f"Camera {camera_index} opened but failed to read frames. Check camera permissions and availability."
            )

        actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        logger.info(
            f"Camera {camera_index} opened successfully. Actual resolution: {actual_width}x{actual_height}, FPS: {actual_fps}"
        )
        logger.info(f"Test frame read: {test_frame.shape}")

        # Frame timing
        self._frame_index = 0
        self._time_base = Fraction(1, fps)
        self._frame_interval = 1.0 / fps

        # Display window
        self._display_enabled = True
        self._display_thread = None
        self._latest_frame = None
        self._frame_lock = threading.Lock()

        # Background frame reading task
        self._frame_reading_task = None
        self._frame_reading_running = False

        logger.info(f"WebcamVideoTrack initialized: {width}x{height} @ {fps}fps")

        # Start background frame reading
        self._start_frame_reading()

    def _display_loop(self):
        """Display loop running in separate thread."""
        window_name = "Webcam Feed (press 'q' to quit display)"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        logger.info(f"Display window '{window_name}' created")

        frame_count = 0
        try:
            while self._display_enabled:
                with self._frame_lock:
                    frame = self._latest_frame

                if frame is not None:
                    cv2.imshow(window_name, frame)
                    frame_count += 1
                    if (
                        frame_count % 30 == 0
                    ):  # Log every 30 frames (~1 second at 30fps)
                        logger.debug(
                            f"Displayed {frame_count} frames, latest frame shape: {frame.shape}"
                        )
                elif frame_count == 0:
                    logger.warning("No frames available for display yet")

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    logger.info("Display window closed by user")
                    self._display_enabled = False
                    break

                # Small sleep to avoid busy waiting
                threading.Event().wait(0.033)  # ~30fps display rate
        except Exception:
            logger.exception(f"Display loop got error")
        finally:
            cv2.destroyAllWindows()
            logger.info("Display window closed")

    async def recv(self) -> VideoFrame:
        """Read frame from webcam and return as VideoFrame."""
        # Start background frame reading if not already started
        if self._frame_reading_task is None or (
            hasattr(self._frame_reading_task, "done")
            and self._frame_reading_task.done()
        ):
            if self._frame_reading_running:
                try:
                    loop = asyncio.get_running_loop()
                    self._frame_reading_task = loop.create_task(
                        self._frame_reading_loop()
                    )
                    logger.info("Started background frame reading task from recv()")
                except RuntimeError:
                    pass

        await asyncio.sleep(self._frame_interval)

        if not self.enabled:
            # Return black frame when disabled
            img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        else:
            # Get the latest frame from background reading (or read directly if not available)
            with self._frame_lock:
                frame = self._latest_frame

            if frame is not None:
                # Convert BGR to RGB for VideoFrame
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # Log periodically to verify frames are being sent
                if self._frame_index % (self.fps * 5) == 0:  # Every 5 seconds
                    logger.info(
                        f"Webcam frame #{self._frame_index} sent over WebRTC: {frame.shape}"
                    )
            else:
                # Fallback: try to read directly if background reading hasn't started yet
                if self._frame_index == 0:
                    logger.warning(
                        "No frame available from background reading, reading directly..."
                    )
                if self.cap.isOpened():
                    ret, frame = self.cap.read()
                    if ret:
                        if (
                            frame.shape[1] != self.width
                            or frame.shape[0] != self.height
                        ):
                            frame = cv2.resize(frame, (self.width, self.height))
                        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        # Update latest frame for display
                        with self._frame_lock:
                            self._latest_frame = frame
                    else:
                        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                else:
                    img = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Create VideoFrame
        video_frame = VideoFrame.from_ndarray(img.astype(np.uint8), format="rgb24")
        video_frame.pts = self._frame_index
        video_frame.time_base = self._time_base
        self._frame_index += 1

        return video_frame

    def _start_frame_reading(self):
        """Start background task to continuously read frames from webcam."""
        if self._frame_reading_task is None or (
            hasattr(self._frame_reading_task, "done")
            and self._frame_reading_task.done()
        ):
            self._frame_reading_running = True
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._frame_reading_task = loop.create_task(
                        self._frame_reading_loop()
                    )
                    logger.info("Started background frame reading task")
                else:
                    # If no event loop is running, we'll start it when recv() is first called
                    logger.info(
                        "Event loop not running yet, will start frame reading when recv() is called"
                    )
            except RuntimeError:
                # No event loop in this thread, will start when recv() is called
                logger.info(
                    "No event loop available, will start frame reading when recv() is called"
                )

    async def _frame_reading_loop(self):
        """Continuously read frames from webcam in background."""
        logger.info("Frame reading loop started")
        while self._frame_reading_running:
            try:
                if not self.cap.isOpened():
                    logger.error("Webcam is not opened in frame reading loop")
                    await asyncio.sleep(1.0)
                    continue

                ret, frame = self.cap.read()
                if ret:
                    # Resize if needed
                    if frame.shape[1] != self.width or frame.shape[0] != self.height:
                        frame = cv2.resize(frame, (self.width, self.height))

                    # Update latest frame for display (BGR format for OpenCV)
                    with self._frame_lock:
                        self._latest_frame = frame
                else:
                    logger.warning("Failed to read frame in background loop")
                    # Try to reopen
                    self.cap.release()
                    await asyncio.sleep(0.1)
                    self.cap = cv2.VideoCapture(self.camera_index)
                    if self.cap.isOpened():
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        self.cap.set(cv2.CAP_PROP_FPS, self.fps)

                await asyncio.sleep(self._frame_interval)
            except Exception:
                logger.exception(f"Frame reading loop got error")
                await asyncio.sleep(1.0)

    def start_display(self):
        """Start the display thread."""
        if self._display_thread is None or not self._display_thread.is_alive():
            self._display_enabled = True
            self._display_thread = threading.Thread(
                target=self._display_loop, daemon=True
            )
            self._display_thread.start()
            logger.info("Started webcam display window")

    def stop_display(self):
        """Stop the display thread."""
        self._display_enabled = False
        if self._display_thread and self._display_thread.is_alive():
            self._display_thread.join(timeout=1.0)
        cv2.destroyAllWindows()

    def stop(self):
        """Cleanup resources."""
        # Stop frame reading
        self._frame_reading_running = False
        if self._frame_reading_task and not self._frame_reading_task.done():
            self._frame_reading_task.cancel()

        self.stop_display()
        if self.cap.isOpened():
            self.cap.release()
        logger.info("WebcamVideoTrack stopped")
