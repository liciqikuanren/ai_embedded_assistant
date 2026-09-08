"""RTT 控制台面板：Shell 交互 + 设备日志显示 + J-Link 连接控制。

- 顶部：连接/断开 J-Link 按钮 + 状态标签；
- 中部：统一输出框（Shell 与 Log 混排，可分别勾选显示，ANSI 已剥离）；
- 底部：Shell 输入行（回车发送 \\r，↑↓ 命令历史）。
"""

import re
from datetime import datetime

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontDatabase, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from core.theme import active_palette, is_light

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# 设备日志行前缀的级别标签，如 [INFO][UNKNOWN] (app.c:func:line) msg
_LOG_LEVEL_RE = re.compile(r"^\[(DEBUG|INFO|WARN|ERROR|FATAL)\]")

SHELL_COLOR = QColor("#4fc3f7")   # shell 输出（深色主题）
LOG_COLOR = QColor("#a5d6a7")     # 设备日志默认（深色主题）
SHELL_COLOR_LIGHT = QColor("#0e7490")
LOG_COLOR_LIGHT = QColor("#15803d")

# 日志级别配色：与下位机 system/log/log_config.h 定义一致
# （DEBUG=蓝 INFO=绿 WARN=黄 ERROR=红 FATAL=品红），浅色主题用加深的变体保证可读
_LOG_LEVEL_COLORS_DARK = {
    "DEBUG": QColor("#5ac8fa"),
    "INFO": QColor("#4ade80"),
    "WARN": QColor("#fbbf24"),
    "ERROR": QColor("#ff6b6b"),
    "FATAL": QColor("#e879f9"),
}
_LOG_LEVEL_COLORS_LIGHT = {
    "DEBUG": QColor("#0b74c2"),
    "INFO": QColor("#15803d"),
    "WARN": QColor("#b45309"),
    "ERROR": QColor("#dc2626"),
    "FATAL": QColor("#a21caf"),
}


def shell_log_colors():
    if is_light():
        return SHELL_COLOR_LIGHT, LOG_COLOR_LIGHT
    return SHELL_COLOR, LOG_COLOR


def log_level_colors():
    return _LOG_LEVEL_COLORS_LIGHT if is_light() else _LOG_LEVEL_COLORS_DARK


def log_level_of(text: str):
    """识别设备日志行首的级别标签，返回 (级别, 是否 FATAL)。"""
    m = _LOG_LEVEL_RE.match(text or "")
    if m:
        return m.group(1), m.group(1) == "FATAL"
    return None, False

MAX_ENTRIES = 5000
MAX_HISTORY = 100


def strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列（nr_micro_shell 全 ANSI 输出）。"""
    return _ANSI_RE.sub("", text)


class RttPanel(QWidget):
    connect_clicked = pyqtSignal()
    send_shell = pyqtSignal(str)
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._applying = False
        self._entries: list[tuple[str, datetime, str]] = []
        self._shell_history: list[str] = []
        self._hist_idx = -1
        self._hist_pending = ""
        self._build()

    def _build(self):
        group = QGroupBox("RTT 控制台（Shell / Log）")
        v = QVBoxLayout(group)

        # ---- 顶部：连接控制 ----
        top = QHBoxLayout()
        self.connect_btn = QPushButton("连接 J-Link")
        self.connect_btn.setObjectName("OpenButton")
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("color: #888;")
        top.addWidget(self.connect_btn)
        top.addWidget(self.status_label)
        top.addStretch(1)
        v.addLayout(top)

        # ---- 显示选项 ----
        bar = QHBoxLayout()
        self.shell_show_check = QCheckBox("显示 Shell")
        self.shell_show_check.setChecked(True)
        self.log_show_check = QCheckBox("显示 Log")
        self.log_show_check.setChecked(True)
        self.wrap_check = QCheckBox("自动换行")
        self.wrap_check.setChecked(True)
        self.scroll_check = QCheckBox("自动滚动")
        self.scroll_check.setChecked(True)
        self.clear_btn = QPushButton("清空")
        bar.addWidget(self.shell_show_check)
        bar.addWidget(self.log_show_check)
        bar.addWidget(self.wrap_check)
        bar.addWidget(self.scroll_check)
        bar.addStretch(1)
        bar.addWidget(self.clear_btn)
        v.addLayout(bar)

        # ---- 输出框 ----
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setAcceptRichText(False)
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed.setPointSize(10)
        self.output.setFont(fixed)
        v.addWidget(self.output, 1)

        # ---- Shell 输入行 ----
        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("输入 shell 命令，回车发送（↑↓ 历史）")
        self.input_edit.returnPressed.connect(self._on_send)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self.send_btn)
        v.addLayout(input_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(group)

        # 信号接线
        self.connect_btn.clicked.connect(self.connect_clicked.emit)
        self.clear_btn.clicked.connect(self.clear)
        self.shell_show_check.toggled.connect(self._on_display_changed)
        self.log_show_check.toggled.connect(self._on_display_changed)
        self.wrap_check.toggled.connect(self._on_display_changed)
        self.scroll_check.toggled.connect(self._on_scroll_changed)
        self.input_edit.installEventFilter(self)

    # ---------------- 交互 ----------------
    def _on_send(self):
        cmd = self.input_edit.text()
        if not cmd:
            return
        if not self._shell_history or self._shell_history[-1] != cmd:
            self._shell_history.append(cmd)
            if len(self._shell_history) > MAX_HISTORY:
                del self._shell_history[: len(self._shell_history) - MAX_HISTORY]
        self._hist_idx = -1
        self.input_edit.clear()
        self.send_shell.emit(cmd)

    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                self._history(-1)
                return True
            if event.key() == Qt.Key.Key_Down:
                self._history(1)
                return True
        return super().eventFilter(obj, event)

    def _history(self, direction: int):
        if not self._shell_history:
            return
        if self._hist_idx == -1:
            self._hist_pending = self.input_edit.text()
            self._hist_idx = len(self._shell_history) if direction < 0 else 0
        self._hist_idx += direction
        self._hist_idx = max(0, min(self._hist_idx, len(self._shell_history)))
        if self._hist_idx == len(self._shell_history):
            self.input_edit.setText(self._hist_pending)
            self._hist_idx = -1
        else:
            self.input_edit.setText(self._shell_history[self._hist_idx])

    def _on_display_changed(self, *args):
        if self._applying:
            return
        self._re_render()
        self.settings_changed.emit()

    def _on_scroll_changed(self, *args):
        if self._applying:
            return
        self._autoscroll()
        self.settings_changed.emit()

    # ---------------- 数据接入 ----------------
    def append_shell(self, data: bytes) -> None:
        self._append("shell", bytes(data))

    def append_log(self, data: bytes) -> None:
        self._append("log", bytes(data))

    def append_command(self, cmd: str, source: str = "human") -> None:
        """本地回显一条 shell 命令，带来源标签（→[人]/→[AI]/→[TCP]）。"""
        ts = datetime.now()
        pal = active_palette()
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt_ts = QTextCharFormat()
        fmt_ts.setForeground(pal.ts)
        fmt = QTextCharFormat()
        if source == "ai":
            fmt.setForeground(pal.ai)
            tag = "→[AI] "
        elif source == "tcp":
            fmt.setForeground(pal.tcp)
            tag = "→[TCP] "
        else:
            fmt.setForeground(pal.tx)
            tag = "→[人] "
        cursor.insertText(f"[{ts.strftime('%H:%M:%S.%f')[:-3]}] ", fmt_ts)
        cursor.insertText(tag, fmt)
        cursor.insertText(cmd + "\n", fmt)
        self._autoscroll()

    def _append(self, kind: str, data: bytes) -> None:
        text = strip_ansi(data.decode("utf-8", errors="replace"))
        if not text:
            return
        ts = datetime.now()
        self._entries.append((kind, ts, text))
        if len(self._entries) > MAX_ENTRIES:
            del self._entries[: len(self._entries) - MAX_ENTRIES]
        show = (kind == "shell" and self.shell_show_check.isChecked()) or \
               (kind == "log" and self.log_show_check.isChecked())
        if show:
            self._insert(kind, ts, text)
            self._autoscroll()

    def clear(self) -> None:
        self._entries.clear()
        self.output.clear()

    # ---------------- 渲染 ----------------
    def _insert(self, kind: str, ts: datetime, text: str) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        pal = active_palette()
        shell_color, log_color = shell_log_colors()
        fmt_ts = QTextCharFormat()
        fmt_ts.setForeground(pal.ts)
        fmt = QTextCharFormat()

        if kind == "shell":
            fmt.setForeground(shell_color)
            tag = "$ "
        else:
            # 设备日志：按行首级别标签着色（DEBUG/INFO/WARN/ERROR/FATAL），
            # 无标签的行用默认日志色；FATAL 加粗
            level, fatal = log_level_of(text)
            color = log_level_colors().get(level) if level else None
            fmt.setForeground(color or log_color)
            if fatal:
                from PyQt6.QtGui import QFont
                fmt.setFontWeight(QFont.Weight.Bold)
            tag = "[LOG] "
        cursor.insertText(f"[{ts.strftime('%H:%M:%S.%f')[:-3]}] ", fmt_ts)
        cursor.insertText(tag, fmt)
        cursor.insertText(text, fmt)
        if self.wrap_check.isChecked() or not text.endswith("\n"):
            cursor.insertText("\n", fmt)

    def _re_render(self) -> None:
        self.output.setUpdatesEnabled(False)
        self.output.clear()
        for kind, ts, text in self._entries:
            if kind == "shell" and not self.shell_show_check.isChecked():
                continue
            if kind == "log" and not self.log_show_check.isChecked():
                continue
            self._insert(kind, ts, text)
        self.output.setUpdatesEnabled(True)
        self._autoscroll()

    def _autoscroll(self) -> None:
        if self.scroll_check.isChecked():
            self.output.moveCursor(QTextCursor.MoveOperation.End)
            self.output.ensureCursorVisible()

    def refresh_display(self) -> None:
        """主题切换后按当前调色板整体重绘已有内容。"""
        self._re_render()

    # ---------------- 状态 ----------------
    def set_connected(self, connected: bool, info: str = "") -> None:
        self.connect_btn.setText("断开 J-Link" if connected else "连接 J-Link")
        self.status_label.setText(info or ("已连接" if connected else "未连接"))
        self.status_label.setStyleSheet(
            "color: #4caf50;" if connected else "color: #888;"
        )

    def set_status(self, status: dict) -> None:
        status = status or {}
        connected = bool(status.get("connected"))
        chip = status.get("chip", "")
        self.set_connected(connected, f"已连接 {chip}" if connected and chip else "")

    # ---------------- 配置 ----------------
    def apply_config(self, cfg: dict) -> None:
        self._applying = True
        try:
            self.shell_show_check.setChecked(bool(cfg.get("rtt_shell_show", True)))
            self.log_show_check.setChecked(bool(cfg.get("rtt_log_show", True)))
            self.wrap_check.setChecked(bool(cfg.get("rtt_wrap", True)))
            self.scroll_check.setChecked(bool(cfg.get("rtt_scroll", True)))
        finally:
            self._applying = False
        self._re_render()

    def settings_dict(self) -> dict:
        return {
            "rtt_shell_show": self.shell_show_check.isChecked(),
            "rtt_log_show": self.log_show_check.isChecked(),
            "rtt_wrap": self.wrap_check.isChecked(),
            "rtt_scroll": self.scroll_check.isChecked(),
        }
