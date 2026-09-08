"""发送插件：提供 transmitter 服务（手动/定时循环/文件发送，记历史）。"""

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable

from core.codec import NEWLINE_BYTES


class TransmitterService:
    """发送业务：编码 payload → 写串口 → 记录历史；定时循环用后台循环任务实现。"""

    def __init__(self, ctx: Any, loop: asyncio.AbstractEventLoop,
                 serial, codec, history, log: Callable[[str], None]):
        self._ctx = ctx
        self._loop = loop
        self._serial = serial
        self._codec = codec
        self._history = history
        self._log = log
        self._payload = b""
        self._cycle_task = None
        self._cycle_stop = threading.Event()
        self._cycle_interval = 1.0

    def set_payload(self, text: str, as_hex: bool, encoding: str,
                    add_newline: bool, newline_kind: str) -> tuple[bool, str | None]:
        """把文本按发送选项编码为待发送字节，缓存用于循环发送。"""
        try:
            data = self._codec.parse_send_payload(text, as_hex, encoding)
        except ValueError as e:
            return False, str(e)
        if add_newline:
            data += NEWLINE_BYTES.get(newline_kind, b"\r\n")
        self._payload = data
        return True, None

    def send(self, text: str, as_hex: bool, encoding: str,
             add_newline: bool, newline_kind: str,
             source: str = "human") -> tuple[bool, str | None]:
        if not self._serial.is_open:
            return False, "串口未打开，无法发送"
        ok, err = self.set_payload(text, as_hex, encoding, add_newline, newline_kind)
        if not ok:
            return False, err
        if not self._payload:
            return False, "发送内容为空"
        n = self._serial.write(self._payload, source=source)
        if n > 0 and source == "human":
            self._history.add_send(text)
        return True, None

    def send_file(self, path: str) -> tuple[bool, str | None]:
        if not self._serial.is_open:
            return False, "串口未打开，无法发送"
        data = Path(path).read_bytes()
        if not data:
            return False, "文件为空"
        self._serial.write(data, source="human")
        return True, str(len(data))

    # ---- 定时 / 循环发送 ----
    def start_cycle(self, interval_ms: int) -> None:
        self._cycle_interval = max(0.02, int(interval_ms) / 1000.0)
        if self._cycle_task is not None:
            return
        self._cycle_stop.clear()
        self._cycle_task = asyncio.run_coroutine_threadsafe(
            self._cycle_loop(), self._loop
        )

    def stop_cycle(self) -> None:
        self._cycle_stop.set()
        if self._cycle_task is not None:
            self._cycle_task.cancel()
            self._cycle_task = None

    @property
    def is_cycling(self) -> bool:
        return self._cycle_task is not None

    async def _cycle_loop(self) -> None:
        while not self._cycle_stop.is_set():
            await asyncio.sleep(self._cycle_interval)
            if self._cycle_stop.is_set():
                break
            if self._payload and self._serial.is_open:
                self._serial.write(self._payload, source="human")


class Plugin:
    name = "transmitter"
    inject = ["serial", "codec", "history", "log"]

    def apply(self, ctx):
        log = ctx.get("log")
        loop = asyncio.get_running_loop()
        svc = TransmitterService(
            ctx, loop,
            serial=ctx.get("serial"),
            codec=ctx.get("codec"),
            history=ctx.get("history"),
            log=log,
        )
        ctx.provide("transmitter", svc)
        ctx.effect(lambda: None, svc.stop_cycle)
        log("发送服务已就绪")
