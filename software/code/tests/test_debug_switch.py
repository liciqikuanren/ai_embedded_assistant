"""调试模式动态切换测试（无需重启）：勾选 → 加载调试插件 + 刷新 MCP 工具；取消 → 卸载。

运行：python tests/test_debug_switch.py
"""

import asyncio
import json
import shutil
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from mcp.types import TextContent  # noqa: E402

from core.mini_cordis import Cordis  # noqa: E402
from plugins.ai_server import Plugin as AiServerPlugin  # noqa: E402
from plugins.codec import Plugin as CodecPlugin  # noqa: E402
from plugins.config import ConfigStore  # noqa: E402
from plugins.float_recorder import Plugin as FloatRecorderPlugin  # noqa: E402
from plugins.history import Plugin as HistoryPlugin  # noqa: E402
from plugins.justfloat import Plugin as JustFloatPlugin  # noqa: E402
from plugins.logger import Plugin as LoggerPlugin  # noqa: E402
from plugins.serial_port import Plugin as SerialPlugin  # noqa: E402
from plugins.tcp_forward import Plugin as TcpForwardPlugin  # noqa: E402
from plugins.transmitter import Plugin as TransmitterPlugin  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def tool_names(server) -> list[str]:
    if server is None:
        return []
    return sorted(t.name for t in await server.list_tools())


async def call_tool(server, name: str, args: dict | None = None) -> dict:
    content = await server.call_tool(name, args or {})
    text = "".join(c.text for c in content if isinstance(c, TextContent))
    return json.loads(text)


async def main() -> int:
    td = HERE / "_tmp_switch"
    td.mkdir(parents=True, exist_ok=True)
    try:
        cordis = Cordis()
        store = ConfigStore(td / "cfg.json")
        store.set("ai_server_port", free_port())
        cordis.ctx.provide("config", store)
        # 正常模式：无调试插件
        cordis.load_all([
            LoggerPlugin(), CodecPlugin(), HistoryPlugin(),
            SerialPlugin(), TransmitterPlugin(), AiServerPlugin(),
        ])
        assert cordis.ctx.get("justfloat", strict=False) is None
        assert cordis.ctx.get("float_recorder", strict=False) is None
        names = await tool_names(cordis.ctx.get("ai_server"))
        assert not any(n.startswith(("tcp_forward_", "justfloat_", "float_recorder_"))
                       for n in names), names
        assert "send" in names and "read_received" in names  # 正常模式可任意收发
        assert not any(n.startswith(("send_command", "set_params", "list_commands"))
                       for n in names)
        print(f"  ok 正常模式：{len(names)} 个基础工具（含 send/read_received）")

        # 正常模式：接收数据缓存进 AI 缓冲
        await cordis.ctx.emit("serial_data_received", b"\xaa\xbb\xcc")
        assert (await call_tool(cordis.ctx.get("ai_server"), "get_status"))["pending_rx_bytes"] == 3

        # 模拟勾选调试模式：加载调试插件 + 重启 ai_server
        await cordis.load_plugin_async(TcpForwardPlugin())
        await cordis.load_plugin_async(JustFloatPlugin())
        await cordis.load_plugin_async(FloatRecorderPlugin())
        await cordis.unload_plugin_async("ai_server")
        await cordis.load_plugin_async(AiServerPlugin())
        assert cordis.ctx.get("justfloat", strict=False) is not None
        assert cordis.ctx.get("float_recorder", strict=False) is not None
        names2 = await tool_names(cordis.ctx.get("ai_server"))
        assert {"tcp_forward_start", "justfloat_rename", "float_recorder_start",
                "float_recorder_set_sample_hz", "justfloat_apply_doc_channels"} <= set(names2), names2
        # 调试模式：AI 只能发串口指令，不能通用收发
        assert "send" not in names2 and "read_received" not in names2, names2
        assert {"send_command", "set_params", "list_commands"} <= set(names2), names2
        print(f"  ok 开启调试模式：{len(names2)} 个工具（指令专用，无 send/read_received）")

        # 调试模式：接收数据不缓存、不注入（AI 查看数据走录制 CSV）
        await cordis.ctx.emit("serial_data_received", b"\x01\x02\x03\x04")
        st = await call_tool(cordis.ctx.get("ai_server"), "get_status")
        assert st["pending_rx_bytes"] == 0, st  # 调试模式下原始字节不进入 AI 缓冲

        # 模拟取消调试模式：卸载调试插件 + 重启 ai_server
        await cordis.unload_plugin_async("float_recorder")
        await cordis.unload_plugin_async("justfloat")
        await cordis.unload_plugin_async("tcp_forward")
        await cordis.unload_plugin_async("ai_server")
        await cordis.load_plugin_async(AiServerPlugin())
        assert cordis.ctx.get("justfloat", strict=False) is None
        assert cordis.ctx.get("float_recorder", strict=False) is None
        names3 = await tool_names(cordis.ctx.get("ai_server"))
        assert not any(n.startswith(("tcp_forward_", "justfloat_", "float_recorder_"))
                       for n in names3), names3
        assert "send" in names3 and "read_received" in names3
        assert "send_command" not in names3
        print(f"  ok 关闭调试模式：{len(names3)} 个基础工具")

        cordis.unload_all()
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS: 调试模式动态切换（无需重启）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
