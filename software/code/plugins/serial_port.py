"""串口插件：提供 serial 服务（pyserial 收发、端口扫描、断线自动重连）。

注意：文件名刻意叫 serial_port.py，避免与 pyserial 的包名 serial 冲突。
"""

import asyncio
import threading
import time
from typing import Any, Callable

import serial
from serial.tools import list_ports


class SerialService:
    """封装 pyserial 的串口服务，事件统一广播到 Cordis 事件总线。

    - 读操作在独立线程中非阻塞轮询，避免阻塞 asyncio/UI；
    - 开/关/写可从任意线程调用（内部加锁，事件经 run_coroutine_threadsafe 广播）。
    """

    def __init__(self, ctx: Any, loop: asyncio.AbstractEventLoop,
                 log: Callable[[str], None]):
        self._ctx = ctx
        self._loop = loop
        self._log = log
        self._lock = threading.RLock()
        self._serial: serial.Serial | None = None
        self._reader: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._closing = False
        self._auto_reconnect = False
        self._reconnect_params: dict | None = None
        self._reconnect_delay = 2.0
        self._reconnecting = False
        self._params: dict | None = None

    @staticmethod
    def list_ports() -> list[dict]:
        return [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in list_ports.comports()
        ]

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._serial is not None and self._serial.is_open

    @property
    def auto_reconnect(self) -> bool:
        return self._auto_reconnect

    @property
    def current_params(self) -> dict | None:
        """当前连接的串口参数（未打开时为 None）。"""
        with self._lock:
            return dict(self._params) if self._params else None

    def _emit(self, event: str, data: Any = None) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._ctx.emit(event, data), self._loop)
        except RuntimeError:
            pass

    def open(self, params: dict) -> tuple[bool, str | None]:
        """打开串口。返回 (是否成功, 错误信息)；成功时广播 serial_opened。"""
        ok, err = self._open_internal(params)
        if ok:
            self._emit("serial_opened", dict(params))
            self._log(f"串口已打开：{params.get('port')} @ {params.get('baudrate')}")
        else:
            self._log(f"串口打开失败：{err}")
        return ok, err

    def _open_internal(self, params: dict) -> tuple[bool, str | None]:
        with self._lock:
            if self._serial is not None and self._serial.is_open:
                return True, None
            try:
                self._closing = False
                # serial_for_url：真实端口（如 COM3）走 Serial，也支持 loop:// 等测试协议
                self._serial = serial.serial_for_url(
                    params["port"],
                    baudrate=int(params["baudrate"]),
                    bytesize=int(params["bytesize"]),
                    parity=params["parity"],
                    stopbits=float(params["stopbits"]),
                    timeout=0,
                    write_timeout=1,
                    rtscts=bool(params.get("rtscts", False)),
                    xonxoff=bool(params.get("xonxoff", False)),
                )
            except Exception as e:
                self._serial = None
                return False, str(e)
            self._params = dict(params)
            self._reader_stop.clear()
            self._reader = threading.Thread(
                target=self._reader_loop, name="serial-reader", daemon=True
            )
            self._reader.start()
            return True, None

    def close(self) -> None:
        with self._lock:
            self._closing = True
            self._auto_reconnect = False
            self._reader_stop.set()
            s, self._serial = self._serial, None
            self._params = None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
            self._emit("serial_closed", None)

    def write(self, data: bytes, source: str = "human") -> int:
        with self._lock:
            s = self._serial
        if s is None or not s.is_open:
            self._emit("serial_error", "串口未打开，无法发送")
            return 0
        try:
            n = s.write(data)
        except Exception as e:
            self._emit("serial_error", f"发送失败：{e}")
            return 0
        self._emit("serial_data_sent", {"data": bytes(data), "source": source})
        return n

    def set_auto_reconnect(self, enabled: bool, params: dict | None = None,
                           delay: float | None = None) -> None:
        with self._lock:
            self._auto_reconnect = enabled
            if params is not None:
                self._reconnect_params = dict(params)
            if delay is not None:
                self._reconnect_delay = float(delay)

    def _reader_loop(self) -> None:
        while not self._reader_stop.is_set():
            with self._lock:
                s = self._serial
            if s is None:
                break
            try:
                waiting = s.in_waiting
                if waiting > 0:
                    data = s.read(waiting)
                    if data:
                        self._emit("serial_data_received", bytes(data))
                else:
                    time.sleep(0.01)
            except (serial.SerialException, OSError, TypeError) as e:
                self._log(f"串口读取异常：{e}")
                break
            except Exception as e:
                self._log(f"串口读取异常：{e}")
                break
        self._on_reader_exit()

    def _on_reader_exit(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._serial = None
            self._reader = None
        self._emit("serial_disconnected", dict(self._reconnect_params or {}))
        if self._auto_reconnect and self._reconnect_params:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            asyncio.run_coroutine_threadsafe(self._reconnect_loop(), self._loop)
        except RuntimeError:
            self._reconnecting = False

    async def _reconnect_loop(self) -> None:
        self._emit("serial_reconnecting", None)
        try:
            while self._auto_reconnect and not self._closing:
                await asyncio.sleep(self._reconnect_delay)
                if self._closing or not self._auto_reconnect:
                    break
                params = self._reconnect_params
                if not params:
                    break
                ok, err = await asyncio.to_thread(self._open_internal, params)
                if ok:
                    self._emit("serial_opened", dict(params))
                    break
                self._emit("serial_reconnect_failed", err)
        finally:
            self._reconnecting = False


class Plugin:
    name = "serial"
    inject = ["config", "log"]

    def apply(self, ctx):
        log = ctx.get("log")
        loop = asyncio.get_running_loop()

        svc = SerialService(ctx, loop, log)
        ctx.provide("serial", svc)

        # 卸载时关闭串口、停止读线程
        ctx.effect(lambda: None, svc.close)
        log("串口服务已就绪")
