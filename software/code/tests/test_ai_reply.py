"""AI 回复回写服务（ReplyHttpServer）冒烟测试：DSH 桥 POST 的 AI 回复从串口发出。

运行：python tests/test_ai_reply.py

验证链路：
  POST http://127.0.0.1:{port}/ {"text": "hello"} → transmitter.send(source="ai")
  → 串口写入（loop:// 回环即读）→ 接收区/缓冲区收到 b"hello\r\n"。
"""

import asyncio
import json
import socket
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from core.mini_cordis import Cordis  # noqa: E402
from plugins.ai_server import ReplyHttpServer  # noqa: E402
from plugins.codec import Plugin as CodecPlugin  # noqa: E402
from plugins.config import Plugin as ConfigPlugin  # noqa: E402
from plugins.history import Plugin as HistoryPlugin  # noqa: E402
from plugins.logger import Plugin as LoggerPlugin  # noqa: E402
from plugins.serial_port import Plugin as SerialPlugin  # noqa: E402
from plugins.transmitter import Plugin as TransmitterPlugin  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def main() -> int:
    cordis = Cordis()
    cordis.load_all([
        ConfigPlugin(), LoggerPlugin(), SerialPlugin(),
        CodecPlugin(), HistoryPlugin(), TransmitterPlugin(),
    ])
    serial = cordis.ctx.get("serial")
    ok, err = serial.open({
        "port": "loop://", "baudrate": 115200, "bytesize": 8,
        "parity": "N", "stopbits": 1, "flow": "None",
        "rtscts": False, "xonxoff": False,
    })
    if not ok:
        print(f"FAIL: 串口打开失败：{err}")
        return 1

    transmitter = cordis.ctx.get("transmitter")
    if transmitter is None:
        print("SKIP: transmitter 服务未加载（正常模式必需）")
        return 0

    port = free_port()
    reply = ReplyHttpServer(transmitter, print)
    if not reply.start("127.0.0.1", port):
        print("FAIL: 回复服务启动失败")
        return 1
    await asyncio.sleep(0.2)

    try:
        # 订阅接收事件（loop:// 回环：发出即读回）
        received: list[bytes] = []
        cordis.ctx.on("serial_data_received", lambda data: received.append(bytes(data)))

        url = f"http://127.0.0.1:{port}/"
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": "hello ai reply"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body.get("ok") is True, f"响应 ok 应为 True：{body}"

        # loop:// 回环：等待读回（带 CRLF 换行）
        for _ in range(50):
            if any(b"hello ai reply" in chunk for chunk in received):
                break
            await asyncio.sleep(0.05)
        text = b"".join(received).decode("utf-8", errors="replace")
        assert "hello ai reply" in text, f"串口未收到回复内容：{text!r}"
        print(f"OK: 串口收到 AI 回复：{text!r}")
        return 0
    finally:
        reply.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
