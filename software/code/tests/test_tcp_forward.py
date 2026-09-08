"""TCP 转发端到端冒烟测试（无 GUI）：loop:// 回环串口 + 真实 TCP 客户端。

运行：python tests/test_tcp_forward.py

验证链路：
  客户端A 发送 b"aaa" → 串口写入（loop:// 回环即读）→ 广播 → 客户端A/B 都收到；
  反向同样验证（客户端B 发 b"bbb"）。
"""

import asyncio
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from core.mini_cordis import Cordis  # noqa: E402
from plugins.config import Plugin as ConfigPlugin  # noqa: E402
from plugins.logger import Plugin as LoggerPlugin  # noqa: E402
from plugins.serial_port import Plugin as SerialPlugin  # noqa: E402
from plugins.tcp_forward import Plugin as TcpForwardPlugin  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def wait_until(pred, timeout: float = 3.0) -> bool:
    step = 0.02
    waited = 0.0
    while waited < timeout:
        if pred():
            return True
        await asyncio.sleep(step)
        waited += step
    return pred()


async def main() -> int:
    cordis = Cordis()
    cordis.load_all([
        ConfigPlugin(), LoggerPlugin(), SerialPlugin(), TcpForwardPlugin(),
    ])
    serial = cordis.ctx.get("serial")
    tcp = cordis.ctx.get("tcp_forward")

    # 1. 打开 loop:// 回环串口（写入的数据会被立即读到）
    ok, err = serial.open({
        "port": "loop://", "baudrate": 115200, "bytesize": 8,
        "parity": "N", "stopbits": 1, "flow": "None",
        "rtscts": False, "xonxoff": False,
    })
    assert ok, f"打开 loop:// 串口失败：{err}"

    # 2. 启动 TCP 服务
    port = free_port()
    tcp.start("127.0.0.1", port)
    assert await wait_until(lambda: tcp.is_running), "TCP 服务未在 3s 内启动"
    assert tcp.client_count == 0

    # 3. 两个客户端连接
    r1, w1 = await asyncio.open_connection("127.0.0.1", port)
    r2, w2 = await asyncio.open_connection("127.0.0.1", port)
    assert await wait_until(lambda: tcp.client_count == 2), "客户端数未变为 2"

    # 4. 客户端A 发 b"aaa" → 串口（回环）→ 广播给两个客户端
    w1.write(b"aaa")
    await w1.drain()
    got1 = await asyncio.wait_for(r1.read(3), timeout=3)
    got2 = await asyncio.wait_for(r2.read(3), timeout=3)
    assert got1 == b"aaa", f"客户端A 期望收到 aaa，实际 {got1!r}"
    assert got2 == b"aaa", f"客户端B 期望收到 aaa，实际 {got2!r}"
    print("  ok  客户端A→串口→广播：两个客户端都收到 aaa")

    # 5. 客户端B 发 b"bbb"，反向链路同样生效
    w2.write(b"bbb")
    await w2.drain()
    got3 = await asyncio.wait_for(r1.read(3), timeout=3)
    got4 = await asyncio.wait_for(r2.read(3), timeout=3)
    assert got3 == b"bbb", f"客户端A 期望收到 bbb，实际 {got3!r}"
    assert got4 == b"bbb", f"客户端B 期望收到 bbb，实际 {got4!r}"
    print("  ok  客户端B→串口→广播：两个客户端都收到 bbb")

    # 6. 停止服务 → 客户端断开、计数清零
    tcp.stop()
    assert await wait_until(lambda: not tcp.is_running), "TCP 服务未停止"
    assert tcp.client_count == 0
    print("  ok  停止服务后运行状态复位、客户端清零")

    # 7. 清理：关闭串口、卸载插件
    serial.close()
    cordis.unload_all()
    print("PASS: TCP 转发全链路（客户端→串口→客户端广播）验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
