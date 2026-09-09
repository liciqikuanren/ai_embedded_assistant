"""RTT 插件：提供 rtt 服务（pylink 连接 J-Link，轮询 RTT ch0/ch1/ch2）。

- ch0 = shell 终端（上/下行，事件 rtt_shell_rx）；
- ch1 = 波形（标准 JustFloat 字节流：float×N 小端 + 帧尾 {00 00 80 7F}，事件 rtt_wave 原样透传，
  由 justfloat 服务按帧尾解析，通道数随帧自动变化）；
- ch2 = 设备日志（事件 rtt_log）。
- pylink 为阻塞库，读轮询跑在独立线程；事件经 run_coroutine_threadsafe 广播。
- 未安装 pylink 时降级：connect() 返回错误，不影响程序启动。
"""

import asyncio
import re
import threading
import time
from typing import Any, Callable

try:
    import pylink
    from pylink.enums import JLinkInterfaces
    from pylink.library import Library
except ImportError:  # pragma: no cover
    pylink = None
    JLinkInterfaces = None
    Library = None

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列。"""
    return _ANSI_RE.sub("", text)


def _find_jlink_dll(config) -> str | None:
    """定位 JLink DLL（64 位优先）：显式配置 → EIDE 自带 → SEGGER 标准安装。"""
    from pathlib import Path

    dll = (config.get("rtt_jlink_dll", "") if config else "") or ""
    if dll and Path(dll).is_file():
        return dll
    home = Path.home()
    candidates = [
        home / ".eide" / "tools" / "jlink" / "JLink_x64.dll",
        home / ".eide" / "tools" / "jlink" / "JLinkARM.dll",
        Path("C:/Program Files/SEGGER/JLink/JLink_x64.dll"),
        Path("C:/Program Files (x86)/SEGGER/JLink/JLink_x64.dll"),
        Path("C:/Program Files/SEGGER/JLink/JLinkARM.dll"),
        Path("C:/Program Files (x86)/SEGGER/JLink/JLinkARM.dll"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


class RttService:
    """封装 pylink 的 RTT 服务（shell/波形/日志三通道）。"""

    def __init__(self, ctx: Any, loop: asyncio.AbstractEventLoop,
                 log: Callable[[str], None], config: Any):
        self._ctx = ctx
        self._loop = loop
        self._log = log
        self._config = config
        self._lock = threading.RLock()
        self._jlink = None
        self._reader: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._connected = False
        self._shell_buf = bytearray()   # shell 响应缓冲（exec_shell 轮询）
        self._log_buf = bytearray()     # 日志环形缓冲（read_log 拉取）
        self._log_max = 64 * 1024
        self._current_source: str | None = None  # 最后一条 shell 命令来源（human/ai/tcp）

    # ---------------- 状态 ----------------
    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self._connected,
                "chip": self._config.get("rtt_chip", ""),
                "interface": self._config.get("rtt_interface", "SWD"),
            }

    def _emit(self, event: str, data: Any = None) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._ctx.emit(event, data), self._loop)
        except RuntimeError:
            pass

    # ---------------- 连接 ----------------
    def connect(self) -> tuple[bool, str | None]:
        if pylink is None:
            err = "pylink 未安装（请执行 pip install pylink-square）"
            self._log(err)
            return False, err
        with self._lock:
            if self._connected:
                return True, None
            chip = self._config.get("rtt_chip", "STM32H743XI") or "STM32H743XI"
            dll = None
            try:
                dll = _find_jlink_dll(self._config)
                if dll and Library is not None:
                    jlink = pylink.JLink(lib=Library(dllpath=dll))
                else:
                    jlink = pylink.JLink()
                serial_no = self._config.get("rtt_serial_no", "") or ""
                jlink.open(serial_no or None)

                iface = (self._config.get("rtt_interface", "SWD") or "SWD").upper()
                if JLinkInterfaces is not None:
                    iface_enum = getattr(JLinkInterfaces, iface, JLinkInterfaces.SWD)
                    jlink.set_tif(iface_enum)

                speed = int(self._config.get("rtt_speed", 4000) or 4000)
                jlink.connect(chip, speed=speed)

                addr = self._config.get("rtt_control_block_addr", "") or ""
                if addr:
                    jlink.rtt_start(int(str(addr), 0))
                else:
                    jlink.rtt_start()

                self._jlink = jlink
                self._connected = True
                self._reader_stop.clear()
                self._reader = threading.Thread(
                    target=self._reader_loop, name="rtt-reader", daemon=True
                )
                self._reader.start()
            except Exception as e:
                self._jlink = None
                self._connected = False
                msg = str(e)
                if dll is None:
                    msg = (
                        "未找到 J-Link DLL：请先安装 SEGGER J-Link 软件"
                        "（https://www.segger.com/downloads/jlink），"
                        "或在配置 rtt_jlink_dll 中指定 JLink_x64.dll 路径。"
                        f"原始错误：{msg}"
                    )
                return False, msg
        self._log(f"RTT 已连接：{chip}")
        self._emit("rtt_status", self.status())
        return True, None

    def disconnect(self) -> None:
        with self._lock:
            self._reader_stop.set()
            jlink, self._jlink = self._jlink, None
            self._connected = False
            if jlink is not None:
                try:
                    jlink.rtt_stop()
                except Exception:
                    pass
                try:
                    jlink.close()
                except Exception:
                    pass
        self._emit("rtt_status", self.status())
        self._log("RTT 已断开")

    # ---------------- shell 下行 ----------------
    def current_source(self) -> str | None:
        """最后一条 shell 命令的来源（human/ai/tcp；命令结束（提示符）后为 None）。"""
        with self._lock:
            return self._current_source

    def send_shell(self, cmd: str, source: str = "human") -> tuple[bool, str | None]:
        with self._lock:
            jlink = self._jlink
            if jlink is None or not self._connected:
                return False, "RTT 未连接"
            ch = int(self._config.get("rtt_shell_channel", 0) or 0)
            # 固件 nr_micro_shell 的结束符是 \r（NR_SHELL_END_OF_LINE == 1）
            data = (cmd + "\r").encode("utf-8")
            try:
                self._current_source = source  # 标记这条命令的来源
                jlink.rtt_write(ch, data)
            except Exception as e:
                self._current_source = None
                return False, str(e)
        self._emit("rtt_shell_tx", {"command": cmd, "source": source})
        return True, None

    def exec_shell(self, cmd: str, timeout: float = 3.0, source: str = "ai") -> dict:
        """响应式执行 shell 命令：发送后收集输出至提示符/超时，返回去 ANSI 文本。"""
        with self._lock:
            jlink = self._jlink
            if jlink is None or not self._connected:
                return {"ok": False, "message": "RTT 未连接"}
            self._shell_buf.clear()
            ch = int(self._config.get("rtt_shell_channel", 0) or 0)
            try:
                self._current_source = source  # 标记 AI 命令来源
                jlink.rtt_write(ch, (cmd + "\r").encode("utf-8"))
            except Exception as e:
                self._current_source = None
                return {"ok": False, "message": str(e)}
        self._emit("rtt_shell_tx", {"command": cmd, "source": source})
        prompt = (self._config.get("rtt_shell_prompt", "") or "").encode("utf-8")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if prompt and prompt in self._shell_buf:
                    break
            time.sleep(0.02)
        with self._lock:
            text = self._shell_buf.decode("utf-8", "ignore")
        return {"ok": True, "output": _strip_ansi(text)}

    def read_log(self, count: int = 500, grep: str = "", level: str = "") -> dict:
        """按需拉取设备日志（RTT ch2）：级别/关键字过滤 + 最近 N 行。"""
        with self._lock:
            text = self._log_buf.decode("utf-8", "ignore")
        lines = _strip_ansi(text).splitlines()
        if level:
            tag = f"[{level.upper()}]"
            lines = [ln for ln in lines if tag in ln]
        if grep:
            lines = [ln for ln in lines if grep in ln]
        if count and count > 0:
            lines = lines[-count:]
        return {"ok": True, "lines": lines, "count": len(lines)}

    # ---------------- 读轮询 ----------------
    def _reader_loop(self) -> None:
        ch_shell = int(self._config.get("rtt_shell_channel", 0) or 0)
        ch_wave = int(self._config.get("rtt_wave_channel", 1) or 1)
        ch_log = int(self._config.get("rtt_log_channel", 2) or 2)
        prompt = (self._config.get("rtt_shell_prompt", "") or "").encode("utf-8")
        recent = bytearray()

        while not self._reader_stop.is_set():
            with self._lock:
                jlink = self._jlink
                if jlink is None:
                    break
                try:
                    data = jlink.rtt_read(ch_shell, 256)
                    if data:
                        self._emit("rtt_shell_rx", bytes(data))
                        self._shell_buf.extend(data)
                        recent.extend(data)
                        if len(recent) > 256:
                            del recent[: len(recent) - 256]
                        if prompt and prompt in recent:
                            # 提示符出现 → 上一条命令结束，清除命令来源标记
                            self._current_source = None

                    data = jlink.rtt_read(ch_wave, 256)
                    if data:
                        # 标准 JustFloat：帧尾定界（{00 00 80 7F}）、通道数随帧变化。
                        # 原样透传，justfloat 服务按帧尾解析；TCP/VoFA 客户端同流接收。
                        self._emit("rtt_wave", bytes(data))

                    data = jlink.rtt_read(ch_log, 256)
                    if data:
                        self._emit("rtt_log", bytes(data))
                        self._log_buf.extend(data)
                        if len(self._log_buf) > self._log_max:
                            del self._log_buf[: len(self._log_buf) - self._log_max]
                except Exception as e:
                    self._log(f"RTT 读取异常：{e}")
                    break
            time.sleep(0.01)
        self._on_reader_exit()

    def _on_reader_exit(self) -> None:
        with self._lock:
            self._connected = False
            self._jlink = None
            self._reader = None
        self._emit("rtt_disconnected", None)
        self._emit("rtt_status", self.status())


class Plugin:
    name = "rtt"
    inject = ["config", "log"]

    def apply(self, ctx):
        config = ctx.get("config")
        log = ctx.get("log")
        loop = asyncio.get_running_loop()

        svc = RttService(ctx, loop, log, config)
        ctx.provide("rtt", svc)
        ctx.effect(lambda: None, svc.disconnect)
        log("RTT 服务已就绪（未连接）")
