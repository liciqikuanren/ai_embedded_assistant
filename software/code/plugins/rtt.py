"""RTT 插件：提供 rtt 服务（探针可选 J-Link 或 DAPLink/CMSIS-DAP，轮询 RTT ch0/ch1/ch2）。

- ch0 = shell 终端（上/下行，事件 rtt_shell_rx）；
- ch1 = 波形（标准 JustFloat 字节流：float×N 小端 + 帧尾 {00 00 80 7F}，事件 rtt_wave 原样透传，
  由 justfloat 服务按帧尾解析，通道数随帧自动变化）；
- ch2 = 设备日志（事件 rtt_log）。

探针后端（配置 rtt_probe，两种都提供同一套 read/write 接口）：
- jlink：SEGGER J-Link，经 pylink（JLink_x64.dll）连接；
- dap：DAPLink / 任意 CMSIS-DAP 探针，经 pyOCD 连接。

RTT 本身与探针无关：它只是目标 RAM 里的控制块 + 环形缓冲，任何能读写目标内存的调试器都能用，
因此 DAPLink 无需改动固件，只要主机知道控制块地址即可。DAP 侧控制块定位顺序：

1. 配置 rtt_control_block_addr（精确地址，最稳）；
2. 配置 rtt_dap_elf 指向固件 ELF（H7_Template.axf），解析符号 _SEGGER_RTT；
3. 在 rtt_cb_search_addr / rtt_cb_search_size 指定范围内扫描 SEGGER RTT 标志；
   两者留空则扫描 STM32H7 常用 RAM 区（AXI/DTCM/SRAM1-3/SRAM4/ITCM）。

注意：pyOCD 内置目标 stm32h743xx 的内存映射不含 AXI SRAM(0x24000000)，其自动扫描会落空；
本工程 _SEGGER_RTT 就在 AXI SRAM，所以走上面的 2/3 步或直接填精确地址。

探针库均为阻塞实现，读轮询跑在独立线程；事件经 run_coroutine_threadsafe 广播。
未安装 pylink / pyocd 时降级：connect() 返回错误，不影响程序启动。
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

# RTT 控制块标志字符串（SEGGER_RTT_CB.acID）
_RTT_CB_ID = b"SEGGER RTT"
# 固件里 RTT 控制块的符号名（SEGGER_RTT.c 中的全局变量 _SEGGER_RTT）
_RTT_CB_SYMBOL = "_SEGGER_RTT"

# DAP 侧默认控制块扫描范围：STM32H7 常用 RAM 区（地址, 长度）
_DAP_CB_RANGES = (
    (0x24000000, 0x00080000),  # AXI SRAM 512K（本工程 _SEGGER_RTT 落此）
    (0x20000000, 0x00020000),  # DTCM 128K
    (0x30000000, 0x00048000),  # SRAM1/2/3
    (0x38000000, 0x00010000),  # SRAM4 64K
    (0x00000000, 0x00010000),  # ITCM 64K
)
_DAP_SCAN_CHUNK = 4096


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列。"""
    return _ANSI_RE.sub("", text)


def _parse_int(value: Any, default: int | None = None) -> int | None:
    """解析配置里的整数（支持 "0x24000000" / "4000" / 空串）。"""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(text, 0)
    except ValueError:
        return default


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


def _elf_symbol_value(path: str, name: str) -> int | None:
    """从 ELF(.axf) 的符号表里取符号地址（pyelftools，pyOCD 的依赖）。"""
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:  # pyelftools 未安装（pyocd 会带）
        return None
    try:
        with open(path, "rb") as f:
            symtab = ELFFile(f).get_section_by_name(".symtab")
            if symtab is None:
                return None
            for sym in symtab.iter_symbols():
                if sym.name == name:
                    value = int(sym["st_value"])
                    return value or None
    except Exception:
        return None
    return None


def _scan_control_block(target, ranges, cb_id: bytes = _RTT_CB_ID) -> int | None:
    """在给定 RAM 范围内扫描 RTT 控制块标志，返回其精确地址。

    自带扫描（不依赖 pyOCD 的 RTTControlBlock.start()：pyOCD 0.39 的字节级扫描在
    32 字节窗口内步进错位，且内置 H743 内存映射缺 AXI SRAM，自动搜索会落空）。
    分块读取并保留跨块的重叠尾巴，避免标志跨块边界时漏检。
    """
    overlap = len(cb_id) - 1
    for start, size in ranges:
        addr = int(start) & ~0x3
        end = addr + int(size)
        prev = b""
        cur = addr
        while cur < end:
            count = min(_DAP_SCAN_CHUNK, end - cur)
            try:
                data = bytes(target.read_memory_block8(cur, count))
            except Exception:
                break  # 该段不可读（空洞/未映射），跳到下一段
            if not data:
                break
            blob = prev + data
            idx = blob.find(cb_id)
            if idx >= 0:
                return cur - len(prev) + idx
            prev = blob[-overlap:] if overlap else b""
            cur += len(data)
    return None


class _Probe:
    """调试探针统一接口：RTT 读/写 + 关闭。"""

    kind = "probe"
    label = "探针"
    detail = ""      # 连接后补充（探针描述 / 目标名等）

    def open(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def read(self, channel: int, count: int = 0) -> bytes:  # pragma: no cover - 抽象
        """读取该 up 通道当前可用的全部字节。count 仅为兼容，不截断以免丢数据。"""
        raise NotImplementedError

    def write(self, channel: int, data: bytes) -> int:  # pragma: no cover - 抽象
        raise NotImplementedError

    def close(self) -> None:
        pass


class _JLinkProbe(_Probe):
    """SEGGER J-Link 探针（pylink）。"""

    kind = "jlink"
    label = "J-Link"

    def __init__(self, config, log: Callable[[str], None]):
        self._config = config
        self._log = log
        self._jl = None
        self._dll: str | None = None

    def open(self) -> None:
        if pylink is None:
            raise RuntimeError("pylink 未安装（请执行 pip install pylink-square）")
        chip = self._config.get("rtt_chip", "STM32H743XI") or "STM32H743XI"
        dll = None
        try:
            dll = _find_jlink_dll(self._config)
            self._dll = dll
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

            speed = _parse_int(self._config.get("rtt_speed", 4000), 4000) or 4000
            jlink.connect(chip, speed=speed)

            addr = _parse_int(self._config.get("rtt_control_block_addr", ""), None)
            if addr:
                jlink.rtt_start(addr)
            else:
                jlink.rtt_start()
            self._jl = jlink
            self.detail = chip
        except Exception as e:  # noqa: BLE001
            self._jl = None
            msg = str(e)
            if dll is None:
                msg = (
                    "未找到 J-Link DLL：请先安装 SEGGER J-Link 软件"
                    "（https://www.segger.com/downloads/jlink），"
                    "或在配置 rtt_jlink_dll 中指定 JLink_x64.dll 路径。"
                    f"原始错误：{msg}"
                )
            raise RuntimeError(msg) from e

    def read(self, channel: int, count: int = 256) -> bytes:
        data = self._jl.rtt_read(channel, count or 256)
        return bytes(data) if data else b""

    def write(self, channel: int, data: bytes) -> int:
        self._jl.rtt_write(channel, data)
        return len(data)

    def close(self) -> None:
        jlink, self._jl = self._jl, None
        if jlink is not None:
            try:
                jlink.rtt_stop()
            except Exception:
                pass
            try:
                jlink.close()
            except Exception:
                pass


class _DapProbe(_Probe):
    """DAPLink / CMSIS-DAP 探针（pyOCD）。"""

    kind = "dap"
    label = "DAPLink(CMSIS-DAP)"

    def __init__(self, config, log: Callable[[str], None]):
        self._config = config
        self._log = log
        self._session = None
        self._cb = None
        self._cb_addr = 0
        self._probe_desc = ""

    # ---------- 连接 ----------
    def open(self) -> None:
        try:
            from pyocd.core.helpers import ConnectHelper
            from pyocd.core.session import Session
            from pyocd.debug.rtt import RTTControlBlock
        except ImportError as e:
            raise RuntimeError("pyocd 未安装（请执行 pip install pyocd）") from e

        target_name = (self._config.get("rtt_dap_target", "") or "").strip()
        if not target_name:
            target_name = (self._config.get("rtt_chip", "stm32h743xx") or "stm32h743xx").strip().lower()

        uid = (self._config.get("rtt_serial_no", "") or "").strip() or None
        speed_khz = _parse_int(self._config.get("rtt_speed", 4000), 4000) or 4000
        iface = (self._config.get("rtt_interface", "SWD") or "SWD").lower()
        if iface not in ("swd", "jtag"):
            iface = "default"

        probes = ConnectHelper.get_all_connected_probes(blocking=False, unique_id=uid)
        if not probes:
            raise RuntimeError(
                "未找到调试探针：请确认 DAPLink 已插好；"
                "Windows 下 pyOCD 需要 WinUSB 驱动（可用 Zadig 或 pyocd 官方驱动包）。"
            )

        def _is_cmsis(p) -> bool:
            name = type(p).__name__.lower()
            desc = (p.description or "").lower()
            return "cmsisdap" in name or "daplink" in desc or "cmsis" in desc

        # 明确要 DAP 时优先选 CMSIS-DAP/DAPLink，避免多个探针时误选 J-Link
        chosen = next((p for p in probes if _is_cmsis(p)), probes[0])
        self._probe_desc = f"{chosen.description} ({chosen.unique_id})"

        options = {"connect_mode": "attach", "dap_protocol": iface}
        pack = (self._config.get("rtt_dap_pack", "") or "").strip()
        if pack:
            options["pack"] = pack

        session = Session(chosen, auto_open=False, options=options,
                          target_override=target_name, frequency=speed_khz * 1000)
        # pyOCD 的 JLinkProbe 用序列号 JLINKARM_EMU_SelectByUSBSN 会失败（老 DLL/多实例），
        # 置空后回退到默认 USB 选择；仅在用户拿 J-Link 走 DAP 通道时才会走到这里。
        if type(chosen).__name__ == "JLinkProbe":
            try:
                chosen._serial_number_int = None
            except Exception:
                pass
        try:
            session.open()
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            raise

        try:
            target = session.target
            addr = self._resolve_control_block(target)
            cb = RTTControlBlock.from_target(target, address=addr)
            cb.start()
        except Exception:
            try:
                session.close()
            except Exception:
                pass
            raise

        self._session = session
        self._cb = cb
        self._cb_addr = addr
        self.detail = f"{target_name} @ {addr:#010x}"

    def _resolve_control_block(self, target) -> int:
        """控制块地址：精确配置 → ELF 符号 → RAM 扫描。"""
        # 1) 精确地址
        addr = _parse_int(self._config.get("rtt_control_block_addr", ""), None)
        if addr:
            return addr

        # 2) 固件 ELF 的 _SEGGER_RTT 符号
        elf = (self._config.get("rtt_dap_elf", "") or "").strip()
        if elf:
            from pathlib import Path
            if Path(elf).is_file():
                value = _elf_symbol_value(elf, _RTT_CB_SYMBOL)
                if value:
                    self._log(f"DAP RTT：ELF 解析 {_RTT_CB_SYMBOL} = {value:#010x}")
                    return value
            else:
                self._log(f"DAP RTT：固件 ELF 不存在，忽略：{elf}")

        # 3) RAM 扫描
        search_addr = _parse_int(self._config.get("rtt_cb_search_addr", ""), None)
        search_size = _parse_int(self._config.get("rtt_cb_search_size", ""), None)
        if search_addr:
            ranges = [(search_addr, search_size or 0x10000)]
        else:
            ranges = list(_DAP_CB_RANGES)
        self._log("DAP RTT：扫描控制块（" + "/".join(f"{a:#x}+{s:#x}" for a, s in ranges) + "）")
        found = _scan_control_block(target, ranges)
        if not found:
            raise RuntimeError(
                "未在目标 RAM 中找到 RTT 控制块（标志 “SEGGER RTT”）。"
                "请确认固件已烧录并运行，或在配置里填 rtt_control_block_addr，"
                "或用 rtt_dap_elf 指向固件 .axf，或调大 rtt_cb_search_addr/size。"
            )
        return found

    # ---------- 读写 ----------
    def read(self, channel: int, count: int = 0) -> bytes:
        up = self._cb.up_channels
        if channel < 0 or channel >= len(up):
            return b""
        # 不按 count 截断：pyOCD 的 read() 已消费目标环形缓冲，截断即丢数据
        return bytes(up[channel].read())

    def write(self, channel: int, data: bytes) -> int:
        down = self._cb.down_channels
        if channel < 0 or channel >= len(down):
            raise RuntimeError(f"RTT down 通道 {channel} 不存在")
        ch = down[channel]
        sent = 0
        deadline = time.monotonic() + 1.0
        # 不用 pyOCD 的 blocking=True：它返回 None，且缓冲区满时会死循环
        while sent < len(data) and time.monotonic() < deadline:
            n = ch.write(data[sent:], blocking=False)
            if not n:
                time.sleep(0.005)
                continue
            sent += n
        if sent < len(data):
            raise RuntimeError(f"RTT down 通道 {channel} 写入超时（{sent}/{len(data)} 字节）")
        return sent

    def close(self) -> None:
        session, self._session = self._session, None
        self._cb = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


class RttService:
    """封装 RTT 服务（J-Link / DAPLink 双探针，shell/波形/日志三通道）。"""

    def __init__(self, ctx: Any, loop: asyncio.AbstractEventLoop,
                 log: Callable[[str], None], config: Any):
        self._ctx = ctx
        self._loop = loop
        self._log = log
        self._config = config
        self._lock = threading.RLock()
        self._probe: _Probe | None = None
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

    def probe_kind(self) -> str:
        return (self._config.get("rtt_probe", "jlink") or "jlink").lower()

    def status(self) -> dict:
        with self._lock:
            probe = self._probe
            kind = probe.kind if probe is not None else self.probe_kind()
            if probe is not None:
                label = probe.label
            else:
                label = "DAPLink(CMSIS-DAP)" if kind in ("dap", "daplink", "cmsis-dap", "cmsisdap", "pyocd") else "J-Link"
            if kind == "dap":
                chip = self._config.get("rtt_dap_target", "") or self._config.get("rtt_chip", "")
            else:
                chip = self._config.get("rtt_chip", "")
            return {
                "connected": self._connected,
                "probe": kind,
                "probe_name": label,
                "detail": probe.detail if probe is not None else "",
                "chip": chip,
                "interface": self._config.get("rtt_interface", "SWD"),
            }

    def _emit(self, event: str, data: Any = None) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._ctx.emit(event, data), self._loop)
        except RuntimeError:
            pass

    # ---------------- 连接 ----------------
    def _make_probe(self) -> _Probe:
        kind = self.probe_kind()
        if kind in ("dap", "daplink", "cmsis-dap", "cmsisdap", "pyocd"):
            return _DapProbe(self._config, self._log)
        return _JLinkProbe(self._config, self._log)

    def connect(self) -> tuple[bool, str | None]:
        with self._lock:
            if self._connected:
                return True, None
            probe = self._make_probe()
            try:
                probe.open()
            except Exception as e:  # noqa: BLE001
                return False, str(e)
            self._probe = probe
            self._connected = True
            self._reader_stop.clear()
            self._reader = threading.Thread(
                target=self._reader_loop, name="rtt-reader", daemon=True
            )
            self._reader.start()
            detail = probe.detail or probe.label
        self._log(f"RTT 已连接：{probe.label} {detail}")
        self._emit("rtt_status", self.status())
        return True, None

    def disconnect(self) -> None:
        with self._lock:
            self._reader_stop.set()
            probe, self._probe = self._probe, None
            self._connected = False
            if probe is not None:
                try:
                    probe.close()
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
            probe = self._probe
            if probe is None or not self._connected:
                return False, "RTT 未连接"
            ch = _parse_int(self._config.get("rtt_shell_channel", 0), 0) or 0
            # 固件 nr_micro_shell 的结束符是 \r（NR_SHELL_END_OF_LINE == 1）
            data = (cmd + "\r").encode("utf-8")
            try:
                self._current_source = source  # 标记这条命令的来源
                probe.write(ch, data)
            except Exception as e:  # noqa: BLE001
                self._current_source = None
                return False, str(e)
        self._emit("rtt_shell_tx", {"command": cmd, "source": source})
        return True, None

    def exec_shell(self, cmd: str, timeout: float = 3.0, source: str = "ai") -> dict:
        """响应式执行 shell 命令：发送后收集输出至提示符/超时，返回去 ANSI 文本。"""
        with self._lock:
            probe = self._probe
            if probe is None or not self._connected:
                return {"ok": False, "message": "RTT 未连接"}
            self._shell_buf.clear()
            ch = _parse_int(self._config.get("rtt_shell_channel", 0), 0) or 0
            try:
                self._current_source = source  # 标记 AI 命令来源
                probe.write(ch, (cmd + "\r").encode("utf-8"))
            except Exception as e:  # noqa: BLE001
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
        ch_shell = _parse_int(self._config.get("rtt_shell_channel", 0), 0) or 0
        ch_wave = _parse_int(self._config.get("rtt_wave_channel", 1), 1)
        ch_log = _parse_int(self._config.get("rtt_log_channel", 2), 2)
        if ch_wave is None:
            ch_wave = 1
        if ch_log is None:
            ch_log = 2
        prompt = (self._config.get("rtt_shell_prompt", "") or "").encode("utf-8")
        recent = bytearray()

        while not self._reader_stop.is_set():
            with self._lock:
                probe = self._probe
                if probe is None:
                    break
                try:
                    data = probe.read(ch_shell, 256)
                    if data:
                        self._emit("rtt_shell_rx", bytes(data))
                        self._shell_buf.extend(data)
                        recent.extend(data)
                        if len(recent) > 256:
                            del recent[: len(recent) - 256]
                        if prompt and prompt in recent:
                            # 提示符出现 → 上一条命令结束，清除命令来源标记
                            self._current_source = None

                    data = probe.read(ch_wave, 256)
                    if data:
                        # 标准 JustFloat：帧尾定界（{00 00 80 7F}）、通道数随帧变化。
                        # 原样透传，justfloat 服务按帧尾解析；TCP/VoFA 客户端同流接收。
                        self._emit("rtt_wave", bytes(data))

                    data = probe.read(ch_log, 256)
                    if data:
                        self._emit("rtt_log", bytes(data))
                        self._log_buf.extend(data)
                        if len(self._log_buf) > self._log_max:
                            del self._log_buf[: len(self._log_buf) - self._log_max]
                except Exception as e:  # noqa: BLE001
                    self._log(f"RTT 读取异常：{e}")
                    break
            time.sleep(0.01)
        self._on_reader_exit()

    def _on_reader_exit(self) -> None:
        with self._lock:
            self._connected = False
            self._probe = None
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
