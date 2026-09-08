"""日志插件：提供 log 服务（控制台输出 + 广播 log 事件，无依赖）。"""

import asyncio


class Plugin:
    name = "logger"
    inject = []

    def apply(self, ctx):
        loop = asyncio.get_running_loop()

        def log(msg: str):
            print(f"[LOG] {msg}")
            try:
                asyncio.run_coroutine_threadsafe(ctx.emit("log", msg), loop)
            except RuntimeError:
                pass

        ctx.provide("log", log)
