"""AI嵌入式助手 Host（外壳，非插件）：启动 Qt + Cordis，加载插件并桥接双线程。

架构：内核 core/mini_cordis + 薄 Host（本文件）+ 服务插件（plugins/）+ UI 叶子插件（ui/）。
线程：主线程跑 Qt/UI，后台线程跑 asyncio 业务插件；Qt 信号桥接跨线程事件。
"""

import asyncio
import os
import sys
import threading
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# PyInstaller windowed（无控制台）下 stdout/stderr 为 None，会让日志/uvicorn 静默失败：
# 兜底重定向到 devnull，保证冻结版里 MCP 等后台服务可正常启动。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from core.mini_cordis import Cordis
from core.theme import CheckBoxStyle, set_active as theme_set_active, theme_stylesheet

PLUGINS_DIR = HERE / "plugins"

# 工程图标（标题栏/任务栏），缺失时静默跳过
APP_ICON = HERE / "img" / "调试按钮.svg"


def run_backend(cordis: Cordis, stop: threading.Event) -> None:
    """后台线程：在 asyncio 循环里加载业务插件并维持运行。

    加载策略：先加载 config（读取四态运行模式 mode），再按模式决定加载哪些插件：
      - serial / rtt_shell：不加载波形/录制插件（tcp_forward/justfloat/float_recorder）；
      - serial / serial_vofa：不加载 RTT；
      - serial_vofa / rtt_vofa：额外加载 TCP 转发、justfloat 解析、浮点录制。
    """

    async def main_async():
        # 1. 配置插件先行（读取四态模式 mode）
        from plugins.config import Plugin as ConfigPlugin

        cordis.load_plugin(ConfigPlugin())
        loaded = ["config"]
        config = cordis.ctx.get("config")
        mode = config.get("mode", "serial")

        # 暴露 Host 与后台循环（UI 控制器据此动态加载/卸载调试插件）
        cordis.ctx.host = cordis
        cordis.ctx.backend_loop = asyncio.get_running_loop()

        # 2. 按四态模式加载其余业务插件（ai_server 单独放最后：
        #    它 apply 时按“已加载服务”注册 MCP 工具，若先加载只能注册基础工具）
        exclude = {"config", "ai_server"}
        if mode in ("serial", "rtt_shell"):
            # 模式1（串口交互）/ 模式3（RTT Shell）：不加载波形/录制插件
            exclude |= {"tcp_forward", "justfloat", "float_recorder"}
        if mode in ("serial", "serial_vofa"):
            # 串口模式：不加载 RTT
            exclude |= {"rtt"}
        loaded += cordis.load_dir(PLUGINS_DIR, exclude=exclude)

        from plugins.ai_server import Plugin as AiServerPlugin
        cordis.load_plugin(AiServerPlugin())
        loaded.append("ai_server")

        await cordis.ctx.emit("services_ready", {"mode": mode})
        try:
            while not stop.is_set():
                await asyncio.sleep(0.1)
        finally:
            # 逆序卸载业务插件（关闭串口、停记录），不动 UI 插件
            for name in reversed(loaded):
                try:
                    cordis.unload_plugin(name)
                except Exception:
                    pass

    asyncio.run(main_async())


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle(CheckBoxStyle())
    # 启动即按配置的主题设置 QSS（避免先深色后切换的闪烁）
    try:
        from plugins.config import ConfigStore
        theme_name = ConfigStore().get("theme", "dark")
    except Exception:
        theme_name = "dark"
    theme_set_active(theme_name)
    app.setStyleSheet(theme_stylesheet(theme_name))
    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))

    cordis = Cordis()

    # 1. 主线程先加载 UI 叶子插件（先订阅事件）
    from ui import Plugin as UIPlugin
    cordis.load_plugin(UIPlugin())

    # 2. 后台线程跑业务插件
    stop = threading.Event()
    backend = threading.Thread(target=run_backend, args=(cordis, stop), daemon=True)
    backend.start()

    # 无头冒烟测试钩子：AUTO_QUIT_MS 可自动退出
    auto_quit = os.environ.get("AUTO_QUIT_MS")
    if auto_quit:
        QTimer.singleShot(int(auto_quit), app.quit)

    # 3. Qt 主循环
    app.aboutToQuit.connect(stop.set)
    code = app.exec()
    backend.join(timeout=3)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
