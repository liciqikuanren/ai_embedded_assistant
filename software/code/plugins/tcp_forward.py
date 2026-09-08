"""TCP 转发插件：提供 tcp_forward 服务（串口 ↔ TCP 客户端 双向透明桥接，自身为服务端）。

- 串口收到数据 → 原样广播给所有已连接客户端；
- 客户端收到数据 → 原样写入串口（source="tcp"，接收区打 →[TCP] 标签）；
- 服务器以 asyncio 任务运行在后台业务循环上（与 ai_server 同模式），不新增线程；
- 状态通过事件 tcp_forward_status 广播给 UI（运行中/端口/客户端数/错误）。
"""

import asyncio
from typing import Any, Callable


class TcpForwardService:
    """TCP 服务端桥接：管理监听、客户端集合与双向转发。

    控制方法 start()/stop() 为同步包装（UI 线程可调用，内部经
    run_coroutine_threadsafe 调度到后台循环）；事件处理与客户端读写都在循环内执行。
    """

    def __init__(self, ctx: Any, loop: asyncio.AbstractEventLoop,
                 serial, log: Callable[[str], None]):
        self._ctx = ctx
        self._loop = loop
        self._serial = serial
        self._log = log
        self._server: asyncio.Server | None = None
        self._clients: dict = {}  # StreamWriter -> peer 地址
        self._host = ""
        self._port = 0

    # ---------------- 状态 ----------------
    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def status(self) -> dict:
        return {
            "running": self.is_running,
            "host": self._host,
            "port": self._port,
            "clients": self.client_count,
            "error": None,
        }

    # ---------------- 对外控制（UI 线程调用，即发即忘） ----------------
    def start(self, host: str, port: int) -> None:
        """启动监听（异步绑定，结果经 tcp_forward_status 事件回传）。"""
        try:
            asyncio.run_coroutine_threadsafe(self._start(host, port), self._loop)
        except RuntimeError:
            pass

    def stop(self) -> None:
        """停止监听并断开所有客户端。"""
        try:
            asyncio.run_coroutine_threadsafe(self._stop(), self._loop)
        except RuntimeError:
            pass

    # ---------------- 内部实现 ----------------
    async def _start(self, host: str, port: int) -> None:
        self._host = (host or "").strip() or "127.0.0.1"
        try:
            self._port = int(port)
        except (TypeError, ValueError):
            self._port = 0
        if self._server is not None:
            return  # 已在运行，幂等
        if not (1 <= self._port <= 65535):
            self._log(f"TCP 转发端口无效：{self._port}")
            self._emit_status(error=f"端口无效：{self._port}")
            return
        try:
            server = await asyncio.start_server(
                self._handle_client, self._host, self._port
            )
        except Exception as e:
            self._log(f"TCP 转发启动失败：{e}")
            self._emit_status(error=str(e))
            return
        self._server = server
        self._log(f"TCP 转发已启动：{self._host}:{self._port}")
        self._emit_status()

    async def _stop(self) -> None:
        for writer in list(self._clients):
            try:
                writer.close()
            except Exception:
                pass
        self._clients.clear()
        server, self._server = self._server, None
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass
            self._log("TCP 转发已停止")
        self._emit_status()

    def _route_to_target(self, data: bytes) -> None:
        """客户端数据路由：RTT 已连接 → shell；否则 → 串口。"""
        rtt = self._ctx.get("rtt", strict=False)
        if rtt is not None and rtt.is_connected:
            text = data.decode("utf-8", "ignore").strip()
            if text:
                rtt.send_shell(text, source="tcp")
        elif self._serial.is_open:
            self._serial.write(data, source="tcp")
        # 两者都未连接时静默丢弃（避免 serial_error 弹窗刷屏）

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        addr = writer.get_extra_info("peername")
        self._clients[writer] = addr
        self._log(f"TCP 客户端已连接：{addr}")
        self._emit_status()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                self._route_to_target(bytes(data))
        except asyncio.CancelledError:
            pass
        except (ConnectionError, OSError):
            pass
        except Exception as e:
            self._log(f"TCP 客户端读取异常：{e}")
        finally:
            self._clients.pop(writer, None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self._log(f"TCP 客户端已断开：{addr}")
            self._emit_status()

    async def broadcast(self, data: bytes) -> None:
        """把串口收到的数据原样广播给所有客户端；慢/坏客户端移除。"""
        if not self._clients:
            return
        dead = []
        for writer in list(self._clients):
            try:
                writer.write(data)
                await asyncio.wait_for(writer.drain(), timeout=2)
            except (asyncio.TimeoutError, ConnectionError, RuntimeError, OSError):
                dead.append(writer)
        for writer in dead:
            self._clients.pop(writer, None)
            try:
                writer.close()
            except Exception:
                pass
        if dead:
            self._emit_status()

    def _emit_status(self, error: str | None = None) -> None:
        status = {
            "running": self._server is not None,
            "host": self._host,
            "port": self._port,
            "clients": len(self._clients),
            "error": error,
        }
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._ctx.emit("tcp_forward_status", status))


class Plugin:
    name = "tcp_forward"
    inject = ["serial", "log"]

    def apply(self, ctx):
        log = ctx.get("log")
        loop = asyncio.get_running_loop()
        svc = TcpForwardService(ctx, loop, serial=ctx.get("serial"), log=log)
        ctx.provide("tcp_forward", svc)

        async def on_serial_rx(data):
            await svc.broadcast(bytes(data))

        ctx.on("serial_data_received", on_serial_rx)

        # RTT 波形（ch1）→ 同样广播给 VoFA 客户端（模式4）
        async def on_rtt_wave(data):
            await svc.broadcast(bytes(data))

        ctx.on("rtt_wave", on_rtt_wave)

        # RTT shell 输出（ch0）→ 广播给客户端（远程 shell 终端）
        async def on_rtt_shell_rx(data):
            await svc.broadcast(bytes(data))

        ctx.on("rtt_shell_rx", on_rtt_shell_rx)
        ctx.effect(lambda: None, svc.stop)
        log("TCP 转发服务已就绪（未启动）")
