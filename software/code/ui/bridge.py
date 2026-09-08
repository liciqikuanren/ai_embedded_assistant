"""UI 桥：后台线程事件 → Qt 信号（跨线程排队到主线程）。"""

from PyQt6.QtCore import QObject, pyqtSignal


class UIBridge(QObject):
    """把 Cordis 事件总线上的数据桥接到 Qt 信号。"""

    rx = pyqtSignal(object)          # 接收到的字节 bytes
    tx = pyqtSignal(object, str)     # 已发送字节 bytes + 来源 "human"/"ai"
    state = pyqtSignal(str, object)  # 状态事件名 + 负载
    ready = pyqtSignal()             # 业务插件加载完成
    log = pyqtSignal(str)            # 日志消息
    ai_ready = pyqtSignal(object)    # AI 接口就绪信息 {host, port}
    tcp_status = pyqtSignal(object)  # TCP 转发状态 {running, host, port, clients, error}
    justfloat_status = pyqtSignal(object)   # justfloat 解析统计
    justfloat_frame = pyqtSignal(object)    # 解析出的浮点帧 {channels, count}
    float_recorder_status = pyqtSignal(object)  # 录制状态 {state, path, rows, ...}
    debug_mode_changed = pyqtSignal(object)     # 调试模式动态切换完成 {debug_mode}
    mode_changed = pyqtSignal(object)           # 四态模式切换完成 {mode}
    rtt_shell_rx = pyqtSignal(object)           # RTT shell 输出 bytes
    rtt_log = pyqtSignal(object)                # RTT 设备日志 bytes
    rtt_status = pyqtSignal(object)             # RTT 连接状态 {connected, chip, interface}
    rtt_shell_tx = pyqtSignal(str, str)         # 发送的 shell 命令 (command, source)
