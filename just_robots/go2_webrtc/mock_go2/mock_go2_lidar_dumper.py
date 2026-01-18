import base64
import json
from pathlib import Path
from types import TracebackType


class MockGo2LidarDumper:
    def __init__(self, dump_file_path: Path):
        self._ff = Path.open(dump_file_path, "w", encoding="utf-8")

    def __enter__(self):
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _tb: TracebackType | None,
    ):
        self._ff.flush()
        self._ff.close()

    def add(self, frame: bytes | bytearray) -> None:
        b64 = base64.b64encode(frame).decode("ascii")
        record = {"frame": b64}
        self._ff.write(json.dumps(record) + "\n")
