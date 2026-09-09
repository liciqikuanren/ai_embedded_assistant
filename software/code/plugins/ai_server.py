"""AI 接口插件：内置 MCP Streamable HTTP 服务，让 AI 打开/配置串口并收发数据。

- 提供 6 个工具：list_ports / open_port / close_port / get_status / send / read_received；
- AI 的发送经 transmitter（source="ai"），会在接收区打 [AI] 标签；
- AI 读取接收数据走独立缓冲区，不影响人类接收显示。
- 传输用 streamable-http（兼容 @deepseek-ai/dsh-mcp-client 等客户端）。
- 另起轻量 HTTP 服务（ai_reply_host:ai_reply_port，默认 127.0.0.1:8766）：
  接收 DSH ai-embedded-bridge 回写的 AI 回复（POST / {text}），从串口发出，实现双向联动。
"""

import asyncio
import http.server
import json
import re
import threading
import time
import urllib.request
from collections import deque

from mcp.server.fastmcp import FastMCP

import uvicorn

from core import codec
from core.codec import NEWLINE_BYTES, bytes_to_hex, bytes_to_text


class RxBuffer:
    """线程安全的接收数据缓冲（AI 专用）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._chunks: deque = deque()
        self._total = 0

    def push(self, data: bytes) -> None:
        with self._lock:
            self._chunks.append(bytes(data))
            self._total += len(data)

    def drain(self) -> bytes:
        with self._lock:
            data = b"".join(self._chunks)
            self._chunks.clear()
            return data

    def peek(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)

    @property
    def pending(self) -> int:
        with self._lock:
            return sum(len(c) for c in self._chunks)

    @property
    def total(self) -> int:
        return self._total


def build_server(serial, transmitter, config, log, rxbuf: RxBuffer,
                 extra: dict | None = None, switcher=None) -> FastMCP:
    """构造 FastMCP 实例并注册工具。

    extra：调试模式可选服务 {"tcp_forward":.., "justfloat":.., "float_recorder":..}；
    存在哪个服务就注册对应工具（正常模式不传 → 仅 6 个基础工具）。
    """
    extra = extra or {}
    host = config.get("ai_server_host", "127.0.0.1")
    port = int(config.get("ai_server_port", 8765))
    mcp = FastMCP("AI嵌入式工具", host=host, port=port,
                  streamable_http_path="/mcp", log_level="WARNING",
                  stateless_http=True)

    @mcp.tool()
    def list_ports() -> list:
        """列出可用串口，返回 [{device, description}]。"""
        return serial.list_ports()

    @mcp.tool()
    def open_port(port: str, baudrate: int = 115200, bytesize: int = 8,
                  parity: str = "N", stopbits: float = 1,
                  flow: str = "None") -> dict:
        """打开串口（若已打开会先关闭再打开）。flow 取值 None/RTS-CTS/XON-XOFF。"""
        params = {
            "port": port,
            "baudrate": int(baudrate),
            "bytesize": int(bytesize),
            "parity": parity,
            "stopbits": float(stopbits),
            "flow": flow,
            "rtscts": flow == "RTS-CTS",
            "xonxoff": flow == "XON-XOFF",
        }
        if serial.is_open:
            serial.close()
        ok, err = serial.open(params)
        return {"ok": ok, "message": err or "已打开", "port": port,
                "baudrate": int(baudrate)}

    @mcp.tool()
    def close_port() -> dict:
        """关闭串口。"""
        was_open = serial.is_open
        serial.close()
        return {"ok": True, "message": "已关闭" if was_open else "串口本未打开"}

    @mcp.tool()
    def get_status() -> dict:
        """获取当前连接状态与 AI 缓冲统计。"""
        p = serial.current_params or {}
        return {
            "connected": serial.is_open,
            "port": p.get("port", ""),
            "baudrate": p.get("baudrate", 0),
            "pending_rx_bytes": rxbuf.pending,
            "ai_rx_total_bytes": rxbuf.total,
            "mode": config.get("mode", "serial"),
        }

    debug_mode = bool(extra.get("justfloat"))
    if not debug_mode:
        # ---- 正常模式：通用收发（AI 可任意发送/读取原始字节）----

        @mcp.tool()
        def send(data: str, as_hex: bool = False, encoding: str = "UTF-8",
                 newline: str = "") -> dict:
            """发送数据。newline 可选 ""（不追加）/crlf/lf/cr；as_hex=True 时 data 为十六进制。"""
            newline = (newline or "").upper()
            add_newline = newline in NEWLINE_BYTES
            newline_kind = newline if add_newline else "CRLF"
            ok, err = transmitter.send(data, as_hex, encoding, add_newline,
                                       newline_kind, source="ai")
            bytes_sent = 0
            if ok:
                try:
                    payload = codec.parse_send_payload(data, as_hex, encoding)
                    if add_newline:
                        payload += NEWLINE_BYTES[newline_kind]
                    bytes_sent = len(payload)
                except Exception:
                    bytes_sent = 0
            return {"ok": ok, "message": err or "已发送", "bytes_sent": bytes_sent}

        @mcp.tool()
        def read_received(as_hex: bool = False, encoding: str = "UTF-8",
                          clear: bool = True) -> dict:
            """读取自上次读取以来接收到的数据（默认读取后清空缓冲）。"""
            data = rxbuf.drain() if clear else rxbuf.peek()
            text = bytes_to_hex(data) if as_hex else bytes_to_text(data, encoding)
            return {"data": text, "bytes": len(data)}

    else:
        # ---- 调试模式：AI 只能发送串口指令（FireWater），查看数据走录制 CSV ----
        from plugins import commands

        @mcp.tool()
        def list_commands() -> dict:
            """列出可下发的串口指令参数（FireWater 格式：关键字:数值;，一行一帧）。"""
            return commands.describe_commands()

        @mcp.tool()
        def send_command(command: str) -> dict:
            """发送一条 FireWater 串口指令（如 "temp_sw:1;temp_tar:37;"），校验参数与范围。"""
            try:
                commands.parse_command(command)
            except ValueError as e:
                return {"ok": False, "message": str(e)}
            ok, err = transmitter.send(command.strip(), False, "UTF-8",
                                       True, "CRLF", source="ai")
            return {"ok": ok, "message": err or "指令已发送", "command": command.strip()}

        @mcp.tool()
        def set_params(params: dict) -> dict:
            """按 {参数: 数值} 构造并发送串口指令（如 {"temp_sw": 1, "temp_tar": 37}）。"""
            try:
                command = commands.build_command(params)
            except ValueError as e:
                return {"ok": False, "message": str(e)}
            ok, err = transmitter.send(command, False, "UTF-8",
                                       True, "CRLF", source="ai")
            return {"ok": ok, "message": err or "指令已发送", "command": command}

        @mcp.tool()
        def send_preset(preset: str) -> dict:
            """发送温度 PID 场景预设指令（如 "闭环控制默认"/"调大比例系数"/"增大阻尼"/"切回开环"）。"""
            spec = commands.PRESETS.get(preset)
            if spec is None:
                return {"ok": False, "message": f"未知预设「{preset}」，可用：{', '.join(commands.PRESETS)}"}
            command = commands.build_command(spec["params"])
            ok, err = transmitter.send(command, False, "UTF-8",
                                       True, "CRLF", source="ai")
            return {"ok": ok, "message": err or "指令已发送", "preset": preset,
                    "command": command, "desc": spec["desc"]}

    # ---------------- 配置管理（YAML 按需加载/保存/修改） ----------------
    from plugins.config import DEFAULTS as _CONFIG_KEYS

    @mcp.tool()
    def config_status() -> dict:
        """查询当前配置文件路径与配置项数量。"""
        return {"path": config.path, "keys": len(_CONFIG_KEYS),
                "mode": config.get("mode", "serial")}

    @mcp.tool()
    def config_save(path: str = "") -> dict:
        """按需保存：把当前全部配置写入指定 .yaml/.json（缺省保存到默认配置文件）。"""
        target = path or config.path
        config.save_to(target)
        return {"ok": True, "path": str(target)}

    @mcp.tool()
    def config_load(path: str = "") -> dict:
        """按需加载：从指定 .yaml/.json 读取配置（缺省重新加载默认配置）。
        mode 变化会触发无头热切换：按新模式装卸插件并重启 MCP 刷新工具集（约 1~2s）。"""
        prev_mode = config.get("mode", "serial")
        if path:
            config.load_from(path)
        else:
            config.load()
        new_mode = config.get("mode", "serial")
        switched = new_mode != prev_mode
        if switcher is not None and switched and new_mode in _MODES:
            switcher(new_mode)
        return {"ok": True, "path": config.path,
                "mode": new_mode, "hot_switch": switched}

    @mcp.tool()
    def config_set(key: str, value) -> dict:
        """按需修改：设置一项配置并立即保存（仅支持已定义的配置键）。
        key=mode 时触发无头热切换：按新模式装卸插件并重启 MCP 刷新工具集（约 1~2s）。"""
        if key not in _CONFIG_KEYS:
            return {"ok": False, "message": f"未知配置键：{key}（可用 config_status 查看键数）"}
        prev_mode = config.get("mode", "serial")
        config.set(key, value)
        switched = False
        if key == "mode" and switcher is not None and str(value) != prev_mode and str(value) in _MODES:
            switcher(str(value))
            switched = True
        return {"ok": True, "key": key, "value": config.get(key),
                "hot_switch": switched}

    # ---------------- 调试模式工具（按服务存在与否注册） ----------------
    tcp = extra.get("tcp_forward")
    if tcp is not None:

        @mcp.tool()
        def tcp_forward_start(host: str = "127.0.0.1", port: int = 9000) -> dict:
            """启动 TCP 转发服务端（串口 ↔ TCP 客户端双向透传）。"""
            tcp.start(host, port)
            return {"ok": True, "message": "已请求启动"}

        @mcp.tool()
        def tcp_forward_stop() -> dict:
            """停止 TCP 转发服务端并断开所有客户端。"""
            tcp.stop()
            return {"ok": True, "message": "已请求停止"}

        @mcp.tool()
        def tcp_forward_status() -> dict:
            """查询 TCP 转发运行状态。"""
            return tcp.status()

    jf = extra.get("justfloat")
    if jf is not None:

        @mcp.tool()
        def justfloat_status() -> dict:
            """查询 justfloat 解析统计（帧数/图片帧/丢弃字节/开关）。"""
            return jf.stats()

        @mcp.tool()
        def justfloat_enable(enabled: bool = True) -> dict:
            """启用/停用 justfloat 协议解析。"""
            jf.set_enabled(enabled)
            return {"ok": True, "enabled": bool(enabled)}

        @mcp.tool()
        def justfloat_reset() -> dict:
            """重置 justfloat 解析器（清空缓冲与统计）。"""
            jf.reset()
            return {"ok": True, "stats": jf.stats()}

        @mcp.tool()
        def justfloat_frames(limit: int = 100, clear: bool = True) -> dict:
            """读取累积的浮点帧列表（默认读后清空）。"""
            frames = jf.read_frames(limit, clear)
            return {"frames": frames, "count": len(frames)}

        @mcp.tool()
        def justfloat_latest() -> dict:
            """查询当前解析到的通道：{names, values, count}（重命名后的名字 + 最新数值）。"""
            return jf.latest()

        @mcp.tool()
        def justfloat_rename(mapping: dict) -> dict:
            """重命名解析通道。mapping 形如 {"0": "PumpRPM", "Ch1": "Flow_L_min"}；
            重命名后的名字会作为浮点录制（float_recorder）的表头与数据源。"""
            return jf.rename(mapping)

        @mcp.tool()
        def justfloat_reset_names() -> dict:
            """清除全部通道重命名（恢复 Ch0..）。"""
            return jf.reset_names()

        @mcp.tool()
        def justfloat_apply_doc_channels() -> dict:
            """一键按指令文档（串口指令文档.md）命名 8 个通道
            （temp_kp/temp_ki/temp_kd/temp_sw/temp_tar/temp_value/temp_current/timestamp）。"""
            from plugins import commands
            mapping = {str(i): name for i, (name, _unit) in enumerate(commands.CHANNELS)}
            return jf.rename(mapping)

    fr = extra.get("float_recorder")
    if fr is not None:

        @mcp.tool()
        def float_recorder_start(channels=None, duration: float | None = None) -> dict:
            """开始浮点通道录制。channels 可选表头通道名列表；duration 秒（缺省用默认 5 分钟）。"""
            return fr.start(channels, duration)

        @mcp.tool()
        def float_recorder_pause() -> dict:
            """暂停录制（不写行，时长照走）。"""
            return fr.pause()

        @mcp.tool()
        def float_recorder_resume() -> dict:
            """继续已暂停的录制。"""
            return fr.resume()

        @mcp.tool()
        def float_recorder_stop() -> dict:
            """结束录制并落盘 CSV。"""
            return fr.stop()

        @mcp.tool()
        def float_recorder_status() -> dict:
            """查询录制状态（state/rows/remaining/通道数）。"""
            return fr.status()

        @mcp.tool()
        def float_recorder_list() -> list:
            """列出录制目录下的 CSV 文件。"""
            return fr.list_files()

        @mcp.tool()
        def float_recorder_set_dir(path: str) -> dict:
            """设置录制保存目录。"""
            fr.set_dir(path)
            return {"ok": True, "dir": fr.status()["dir"]}

        @mcp.tool()
        def float_recorder_set_duration(seconds: float) -> dict:
            """设置录制默认时长（秒），下次 start 生效。"""
            fr.set_duration(seconds)
            return {"ok": True, "duration": fr.status()["duration"]}

        @mcp.tool()
        def float_recorder_set_sample_hz(hz: float) -> dict:
            """设置录制采样率（Hz），下次 start 生效。"""
            fr.set_sample_hz(hz)
            return {"ok": True, "sample_hz": fr.status()["sample_hz"]}

    # ---------------- 固件通道名自动发现（wave_names 查询 → 自动重命名） ----------------
    @mcp.tool()
    def justfloat_autoname(timeout: float = 2.0) -> dict:
        """查询固件波形通道名并自动重命名（需固件支持 wave_names）。

        RTT 模式走 ch0 shell 查询；串口 serial_vofa 模式在查询窗口发送
        "wave_names"，固件停发 JustFloat 回一行 "<通道数> <名0> <名1> ..."。
        成功后自动按固件顺序重命名通道（录制 CSV 表头同步生效）。"""
        mode = config.get("mode", "serial")
        jf = extra.get("justfloat")
        rtt_svc = extra.get("rtt")
        if jf is None:
            return {"ok": False,
                    "message": "justfloat 服务未加载（需 rtt_vofa / serial_vofa 波形模式）"}

        def tx_send_text(text: str):
            transmitter.send(text, False, "UTF-8", True, "CRLF", source="ai")

        return jf.apply_names_from_query(tx_send_text, rtt_svc, mode, float(timeout))

    # ---------------- RTT 工具（shell 响应式 + log 拉取式） ----------------
    rtt = extra.get("rtt")
    if rtt is not None:

        @mcp.tool()
        def rtt_connect() -> dict:
            """连接 J-Link 并启动 RTT（shell ch0 / 波形 ch1 / 日志 ch2）。"""
            ok, err = rtt.connect()
            return {"ok": ok, "message": err or "已连接"}

        @mcp.tool()
        def rtt_disconnect() -> dict:
            """断开 J-Link RTT 连接。"""
            rtt.disconnect()
            return {"ok": True, "message": "已断开"}

        @mcp.tool()
        def rtt_status() -> dict:
            """查询 RTT 连接状态。"""
            return rtt.status()

        @mcp.tool()
        def shell_exec(command: str, timeout: float = 3.0) -> dict:
            """响应式执行 RTT shell 命令（发送后等待提示符返回，返回去 ANSI 输出）。"""
            return rtt.exec_shell(command, timeout)

        @mcp.tool()
        def read_log(count: int = 500, grep: str = "", level: str = "") -> dict:
            """按需拉取 RTT 设备日志（ch2）。level 可选 DEBUG/INFO/WARN/ERROR；grep 为子串过滤。"""
            return rtt.read_log(count, grep, level)

    return mcp


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _push_payload(payload: dict, config) -> None:
    """把 payload 推送到 DSH 桥（fire-and-forget，失败静默）。"""
    if not config.get("ai_push_enabled", False):
        return
    url = config.get("ai_push_url", "")
    if not url:
        return

    def _post():
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass  # 推送失败不影响主流程

    threading.Thread(target=_post, daemon=True).start()


def _push_received(data: bytes, config) -> None:
    """把接收到的串口数据推送到 DSH 桥。"""
    _push_payload({
        "text": bytes_to_text(data, config.get("rx_encoding", "UTF-8")),
        "hex": bytes_to_hex(data),
        "mode": config.get("ai_push_mode", "chat"),
    }, config)


def _push_text(text: str, config) -> None:
    """把纯文本（shell 输出等）推送到 DSH 桥。"""
    _push_payload({
        "text": text,
        "hex": "",
        "mode": config.get("ai_push_mode", "chat"),
    }, config)


class ReplyHttpServer:
    """轻量 HTTP 服务：接收 DSH ai-embedded-bridge 回写的 AI 回复，从串口发出。

    端点：POST /  {text}  →  transmitter.send(text, source="ai")
    双向联动闭环：串口收到数据 → 桥注入 Agent → AI 回复 → 桥 POST 到这里 → 串口发出。
    """

    def __init__(self, transmitter, log):
        self._transmitter = transmitter
        self._log = log
        self._server = None
        self._thread = None

    def start(self, host: str, port: int) -> bool:
        try:
            server = http.server.ThreadingHTTPServer(
                (host, port), self._make_handler()
            )
        except Exception as e:
            self._log(f"AI 回复服务启动失败：{e}")
            return False
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    def _make_handler(self):
        transmitter = self._transmitter
        log = self._log

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler API)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length > 0 else b""
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    return self._reply(400, {"ok": False, "error": "bad json"})
                text = body.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    return self._reply(400, {"ok": False, "error": "empty text"})
                ok, err = transmitter.send(
                    text, False, "UTF-8", True, "CRLF", source="ai"
                )
                return self._reply(200, {"ok": ok, "message": err or "已发送"})

            def do_GET(self):  # noqa: N802
                return self._reply(200, {"ok": True, "service": "ai-embedded-bridge-reply"})

            def _reply(self, code: int, payload: dict):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except Exception:
                    pass

            def log_message(self, fmt, *args):  # 静默访问日志
                try:
                    log(f"AI 回复服务：{fmt % args}")
                except Exception:
                    pass

        return _Handler


_MODES = ("serial", "serial_vofa", "rtt_shell", "rtt_vofa")


async def _switch_plugins_for_mode(ctx, mode: str) -> None:
    """无头模式热切换（等价 GUI 的 ui/controller.SerialController._switch_mode，
    去掉 UI 面板部分）：按 mode 装卸插件并重启 ai_server，使 MCP 工具集随新模式刷新。"""
    if mode not in _MODES:
        return
    from plugins.float_recorder import Plugin as FloatRecorderPlugin
    from plugins.justfloat import Plugin as JustFloatPlugin
    from plugins.rtt import Plugin as RttPlugin
    from plugins.tcp_forward import Plugin as TcpForwardPlugin

    cordis = getattr(ctx, "host", None)
    if cordis is None:
        return
    want_wave = mode in ("serial_vofa", "rtt_vofa")
    want_rtt = mode in ("rtt_shell", "rtt_vofa")

    for name in ("float_recorder", "justfloat", "tcp_forward"):
        if not want_wave and ctx.get(name, strict=False) is not None:
            await cordis.unload_plugin_async(name)
    if not want_rtt and ctx.get("rtt", strict=False) is not None:
        await cordis.unload_plugin_async("rtt")

    if want_rtt and ctx.get("rtt", strict=False) is None:
        await cordis.load_plugin_async(RttPlugin())
    if want_wave:
        if ctx.get("tcp_forward", strict=False) is None:
            await cordis.load_plugin_async(TcpForwardPlugin())
        if ctx.get("justfloat", strict=False) is None:
            await cordis.load_plugin_async(JustFloatPlugin())
        if ctx.get("float_recorder", strict=False) is None:
            await cordis.load_plugin_async(FloatRecorderPlugin())

    # 重启 AI 接口：按新模式的可用服务重新注册 MCP 工具（等旧端口释放）
    if ctx.get("ai_server", strict=False) is not None:
        await cordis.unload_plugin_async("ai_server")
        await asyncio.sleep(0.5)
    await cordis.load_plugin_async(Plugin())
    try:
        await ctx.emit("mode_changed", {"mode": mode})
    except Exception:
        pass


class Plugin:
    name = "ai_server"
    inject = ["serial", "transmitter", "config", "log"]

    def apply(self, ctx):
        config = ctx.get("config")
        log = ctx.get("log")

        if not config.get("ai_server_enabled", True):
            log("AI 接口已禁用（ai_server_enabled=false）")
            return

        serial = ctx.get("serial")
        transmitter = ctx.get("transmitter")
        rxbuf = RxBuffer()

        # 懒获取调试模式服务（正常模式未加载 → None，不注册对应工具）
        extra = {}
        for name in ("tcp_forward", "justfloat", "float_recorder", "rtt"):
            svc = ctx.get(name, strict=False)
            if svc is not None:
                extra[name] = svc
        mode = config.get("mode", "serial")
        rtt_svc = extra.get("rtt")

        async def on_rx(data):
            # 仅串口交互模式注入串口数据到 DSH；波形模式不注入（走录制 CSV）
            if mode == "serial":
                rxbuf.push(data)
                _push_received(data, config)

        ctx.on("serial_data_received", on_rx)

        async def on_rtt_shell_rx(data):
            # RTT 模式把 shell 输出（去 ANSI）注入 DSH；AI 自己命令的响应不注入
            if mode in ("rtt_shell", "rtt_vofa"):
                if rtt_svc is not None and rtt_svc.current_source() == "ai":
                    return
                text = _ANSI_RE.sub("", bytes(data).decode("utf-8", "ignore"))
                _push_text(text, config)

        ctx.on("rtt_shell_rx", on_rtt_shell_rx)

        # 在现有后台 asyncio 循环里作为任务运行（比另开线程更可靠）
        loop = asyncio.get_running_loop()

        def request_mode_switch(mode: str) -> None:
            """MCP 改 mode 后的无头热切换：稍延迟执行，先让本次响应发出，再重启 AI 接口。"""
            def _run():
                time.sleep(1.0)
                try:
                    asyncio.run_coroutine_threadsafe(
                        _switch_plugins_for_mode(ctx, mode), loop).result(timeout=30)
                except Exception as e:
                    log(f"AI 接口模式热切换失败：{e}")

            threading.Thread(target=_run, daemon=True).start()

        server = build_server(serial, transmitter, config, log, rxbuf, extra,
                              switcher=request_mode_switch)
        ctx.provide("ai_server", server)

        host = config.get("ai_server_host", "127.0.0.1")
        port = int(config.get("ai_server_port", 8765))

        # 持有 uvicorn.Server 运行 Streamable HTTP（等价 FastMCP.run_streamable_http_async）。
        # 注意：不能靠 task.cancel() 停 uvicorn——serve() 中途被取消不会执行 shutdown()，
        # 监听 socket 不释放，热切换重启时新实例绑定 8765 会报 10048。要用 should_exit 优雅停机。
        uv_box: list = []

        async def _run_server():
            """包装运行：端口冲突时 uvicorn 抛 SystemExit，必须拦下，
            否则会杀死整个后台事件循环（热切换重启 AI 接口时尤其关键）。"""
            try:
                app = server.streamable_http_app()
                cfg = uvicorn.Config(app, host=host, port=port, log_level="warning")
                uv = uvicorn.Server(cfg)
                uv_box.append(uv)
                await uv.serve()
            except SystemExit:
                log("AI 接口退出（端口冲突或主动停止，不影响后台循环）")
            except Exception as e:
                log(f"AI 接口异常：{e}")

        task = loop.create_task(_run_server())

        # AI 回复回写服务（DSH 桥 → 串口，仅串口模式；RTT 模式用 shell_exec 工具）
        reply = ReplyHttpServer(transmitter, log)
        reply_ok = False
        if config.get("ai_reply_enabled", False) and mode in ("serial", "serial_vofa"):
            reply_ok = reply.start(
                config.get("ai_reply_host", "127.0.0.1"),
                int(config.get("ai_reply_port", 8766)),
            )
            if reply_ok:
                log(
                    f"AI 回复回写服务已启动：http://{config.get('ai_reply_host', '127.0.0.1')}:{int(config.get('ai_reply_port', 8766))}/"
                )

        def on_done(t):
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                log(f"AI 接口启动失败：{exc}")

        task.add_done_callback(on_done)

        async def teardown():
            """停止 AI 接口：请求 uvicorn 优雅停机（should_exit）并等待退出，
            确保监听端口释放后再重启（避免 10048）；超时则取消兜底。"""
            uv = uv_box[0] if uv_box else None
            if uv is not None:
                uv.should_exit = True
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5)
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, SystemExit):
                        pass
            else:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, SystemExit):
                    pass
            reply.stop()
            log("AI 接口已停止")

        ctx.effect(lambda: None, teardown)

        log(f"AI 接口已启动：http://{host}:{port}/mcp")
        # 通知 UI：AI 接口已就绪（stateless_http 无 session，重启不失效）
        asyncio.run_coroutine_threadsafe(
            ctx.emit("ai_server_ready", {"host": host, "port": port}), loop
        )
