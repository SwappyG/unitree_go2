import asyncio
import cv2
from aiortc import MediaStreamTrack  # type: ignore
import logging

logger = logging.getLogger(__name__)


async def display_video(track: MediaStreamTrack):
    """Pull frames from the video track and show them with OpenCV."""
    # pass
    window = "GO2 Video (press q to quit)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    try:
        while True:
            frame = await track.recv()

            # NOTE: the static linter can't pick up this C-function, but it should
            # exist. We could do an isinstance check, but frames come frequently and
            # that'd be a waste of compute. 
            img = frame.to_ndarray(format="bgr24")  # pyright: ignore[reportAttributeAccessIssue]
            cv2.imshow(window, img)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except Exception as e:
        logging.warning(f"display video task got an exception. {e=}")
        raise
    except asyncio.CancelledError:
        pass
    finally:
        cv2.destroyAllWindows()


# def _start_video_display_task(_prev_video_task: asyncio.Task[None] | None = None):
#     logger.info(f"starting a new asyncio task for this video track. {event.track=}")
#     self._video_task = asyncio.create_task(self._display_video(event.track))
            
# if self._video_task is not None:
#     logger.info(f"already had a task running for a video track, canceling it")
#     self._video_task.cancel()
#     self._video_task.add_done_callback(_start_video_display_task)
#     return

# _start_video_display_task()