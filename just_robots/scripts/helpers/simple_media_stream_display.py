import asyncio

import cv2
from aiortc import MediaStreamTrack
from av import VideoFrame  # type: ignore[import-untyped]

from just_robots.utils.logging import logging

logger = logging.getLogger(__name__)


class SimpleMediaStreamDisplay:
    def __init__(self, video_window_name: str):
        self.video_window_name = video_window_name
        self.video_frame_count = 0

    async def video_display_loop(self, track: MediaStreamTrack) -> None:
        """Display video frames from a MediaStreamTrack.

        Note: OpenCV GUI operations (imshow, waitKey) must run on the main thread,
        so we don't use asyncio.to_thread here.
        """
        try:
            while True:
                frame = await track.recv()
                self.video_frame_count += 1

                # Convert to numpy array for OpenCV
                if not isinstance(frame, VideoFrame):
                    logger.warning(f"Unexpected frame type: {type(frame)}")
                    continue

                img = frame.to_ndarray(format="bgr24")

                # Add frame counter overlay
                cv2.putText(
                    img,
                    f"Frame: {self.video_frame_count}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2,
                )

                # Display the frame
                cv2.imshow(self.video_window_name, img)

                # Process window events - REQUIRED or window becomes unresponsive
                # Also check for 'q' key to allow early exit
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    logger.info("User pressed 'q', stopping video display")
                    break

                if self.video_frame_count % 30 == 0:
                    logger.info(f"Video frames received: {self.video_frame_count}")

        except asyncio.CancelledError:
            logger.info("Video display task cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Video track ended or error: {e}")
        finally:
            cv2.destroyWindow(self.video_window_name)
