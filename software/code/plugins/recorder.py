"""记录插件：提供 recorder 服务（把接收数据写入 .txt/.csv，支持加载历史文件）。"""

import csv
from datetime import datetime
from pathlib import Path

from core.codec import bytes_to_hex, bytes_to_text


class RecorderService:
    """记录串口接收数据到文件（CSV 含 timestamp/hex/text 三列，TXT 为可读日志）。"""

    def __init__(self):
        self._fh = None
        self._csv_writer = None
        self._path: Path | None = None
        self._encoding = "UTF-8"
        self._count = 0

    @property
    def is_recording(self) -> bool:
        return self._fh is not None

    @property
    def path(self) -> str:
        return str(self._path) if self._path else ""

    @property
    def count(self) -> int:
        return self._count

    def start(self, path: str, encoding: str = "UTF-8") -> None:
        self.stop()
        self._encoding = encoding
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "w", newline="", encoding="utf-8")
        if self._path.suffix.lower() == ".csv":
            self._csv_writer = csv.writer(self._fh)
            self._csv_writer.writerow(["timestamp", "hex", "text"])
        self._count = 0

    def stop(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None
        self._csv_writer = None

    def write(self, data: bytes) -> None:
        if self._fh is None:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        hexs = bytes_to_hex(data)
        text = bytes_to_text(data, self._encoding)
        try:
            if self._csv_writer is not None:
                self._csv_writer.writerow([ts, hexs, text])
            else:
                self._fh.write(f"{ts}  {hexs}  |  {text}\n")
            self._count += 1
        except Exception:
            pass

    @staticmethod
    def load_text(path: str, encoding: str = "UTF-8", max_bytes: int = 5_000_000) -> str:
        raw = Path(path).read_bytes()
        if len(raw) > max_bytes:
            raw = raw[:max_bytes]
        return raw.decode(encoding, errors="replace")


class Plugin:
    name = "recorder"
    inject = ["codec", "config", "log"]

    def apply(self, ctx):
        log = ctx.get("log")
        recorder = RecorderService()
        ctx.provide("recorder", recorder)

        async def on_data(data):
            recorder.write(data)

        ctx.on("serial_data_received", on_data)
        ctx.effect(lambda: None, recorder.stop)
        log("数据记录服务已就绪")
