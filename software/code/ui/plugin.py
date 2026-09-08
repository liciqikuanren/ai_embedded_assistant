"""UI 叶子插件：Qt 视图 + 控制器，订阅串口事件，懒获取各服务。"""

from ui.bridge import UIBridge
from ui.controller import SerialController
from ui.window import MainWindow

# 事件名 -> 控制器期望的短名（controller._on_state 按短名匹配）
STATE_EVENTS = {
    "serial_opened": "opened",
    "serial_closed": "closed",
    "serial_error": "error",
    "serial_disconnected": "disconnected",
    "serial_reconnecting": "reconnecting",
    "serial_reconnect_failed": "reconnect_failed",
}


class Plugin:
    name = "ui"
    inject = []

    def apply(self, ctx):
        bridge = UIBridge()
        window = MainWindow()
        controller = SerialController(ctx, window, bridge)
        window._controller = controller  # 保持引用，防止被回收

        async def on_rx(data):
            bridge.rx.emit(data)

        async def on_tx(data):
            if isinstance(data, dict):
                bridge.tx.emit(data.get("data"), data.get("source", "human"))
            else:
                bridge.tx.emit(data, "human")

        async def on_ready(data):
            bridge.ready.emit()

        async def on_log(data):
            bridge.log.emit(str(data))

        async def on_ai_ready(data):
            bridge.ai_ready.emit(data)

        async def on_tcp_status(data):
            bridge.tcp_status.emit(data)

        async def on_justfloat_status(data):
            bridge.justfloat_status.emit(data)

        async def on_recorder_status(data):
            bridge.float_recorder_status.emit(data)

        async def on_debug_changed(data):
            bridge.debug_mode_changed.emit(data)

        async def on_mode_changed(data):
            bridge.mode_changed.emit(data)

        async def on_rtt_shell_rx(data):
            bridge.rtt_shell_rx.emit(data)

        async def on_rtt_log(data):
            bridge.rtt_log.emit(data)

        async def on_rtt_status(data):
            bridge.rtt_status.emit(data)

        async def on_rtt_shell_tx(data):
            data = data or {}
            bridge.rtt_shell_tx.emit(
                data.get("command", ""), data.get("source", "human")
            )

        ctx.on("serial_data_received", on_rx)
        ctx.on("serial_data_sent", on_tx)
        ctx.on("services_ready", on_ready)
        ctx.on("log", on_log)
        ctx.on("ai_server_ready", on_ai_ready)
        ctx.on("tcp_forward_status", on_tcp_status)
        ctx.on("justfloat_status", on_justfloat_status)
        ctx.on("float_recorder_status", on_recorder_status)
        ctx.on("debug_mode_changed", on_debug_changed)
        ctx.on("mode_changed", on_mode_changed)
        ctx.on("rtt_shell_rx", on_rtt_shell_rx)
        ctx.on("rtt_log", on_rtt_log)
        ctx.on("rtt_status", on_rtt_status)
        ctx.on("rtt_shell_tx", on_rtt_shell_tx)

        for event, short_name in STATE_EVENTS.items():

            def make_handler(name):
                async def handler(data):
                    bridge.state.emit(name, data)
                return handler

            ctx.on(event, make_handler(short_name))

        ctx.effect(lambda: None, window.close)
        window.show()
