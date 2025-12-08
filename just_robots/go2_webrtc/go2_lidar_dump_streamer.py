import base64
import json
from pathlib import Path
from types import TracebackType


class MockGo2LidarDumpStreamer:
    def __init__(self, lidar_dump_filepath: Path):
        self._filepath = lidar_dump_filepath
        self._ff = Path.open(self._filepath, encoding="utf-8")

    def __enter__(self):
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _tb: TracebackType | None,
    ):
        self._ff.close()

    def __iter__(self):
        return self

    def __next__(self):
        line = self._ff.readline()
        if line == "":
            self._ff.seek(0)
            line = self._ff.readline()
            if line == "":
                raise StopIteration

        data = json.loads(line)
        return base64.b64decode(data["frame"])
