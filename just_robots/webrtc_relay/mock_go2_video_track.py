from aiortc import MediaStreamTrack  # type: ignore
import asyncio
from av import VideoFrame  # pyright: ignore[reportPrivateImportUsage]
from fractions import Fraction
import math
import numpy as np
import time


class MockGo2VideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, width: int = 640, height: int = 360, fps: int = 15):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.enabled = False
        self._t0 = time.time()
        self._frame_index = 0                # <— add this
        self._time_base = Fraction(1, fps)   # <— and this

    async def recv(self) -> VideoFrame:
        await asyncio.sleep(1 / self.fps)
        t = time.time() - self._t0

        if self.enabled:
            x = np.linspace(0, 255, self.width, dtype=np.uint8)
            y = np.linspace(0, 255, self.height, dtype=np.uint8)
            xx, yy = np.meshgrid(x, y)
            phase = int((math.sin(t) * 0.5 + 0.5) * 255)
            img = np.stack([
                (xx + phase) % 256,
                (yy + (phase // 2)) % 256,
                ((xx // 2 + yy // 2 + phase) % 256),
            ], axis=2).astype(np.uint8)
        else:
            img = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        frame = VideoFrame.from_ndarray(img, format="rgb24")
        frame.pts = self._frame_index
        frame.time_base = self._time_base
        self._frame_index += 1
        return frame