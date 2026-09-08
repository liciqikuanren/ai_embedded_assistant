"""接收显示面板：HEX/文本切换、时间戳、自动换行、计数、保存/加载。"""

from datetime import datetime

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFontDatabase, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from core.codec import bytes_to_hex, bytes_to_text
from core.theme import active_palette
from ui.constants import ENCODINGS

MAX_ENTRIES = 5000


class ReceivePanel(QWidget):
    clear_clicked = pyqtSignal()
    save_clicked = pyqtSignal()
    load_clicked = pyqtSignal()
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._applying = False
        self._entries: list[tuple[str, datetime, bytes, str]] = []
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._build()

    def _build(self):
        group = QGroupBox("数据接收")
        v = QVBoxLayout(group)

        # ---- 第 1 行：显示格式（HEX/编码/时间戳/换行/滚动） ----
        fmt_bar = QHBoxLayout()
        self.rx_hex_check = QCheckBox("HEX 显示")
        self.rx_encoding_combo = QComboBox()
        self.rx_encoding_combo.addItems(ENCODINGS)
        self.timestamp_check = QCheckBox("时间戳")
        self.wrap_check = QCheckBox("自动换行")
        self.wrap_check.setChecked(True)
        self.scroll_check = QCheckBox("自动滚动")
        self.scroll_check.setChecked(True)

        fmt_bar.addWidget(self.rx_hex_check)
        fmt_bar.addWidget(QLabel("编码"))
        fmt_bar.addWidget(self.rx_encoding_combo)
        fmt_bar.addWidget(self.timestamp_check)
        fmt_bar.addWidget(self.wrap_check)
        fmt_bar.addWidget(self.scroll_check)
        fmt_bar.addStretch(1)

        # ---- 第 2 行：可视开关 + 计数 + 操作按钮 ----
        action_bar = QHBoxLayout()
        # 可视开关：分别控制接收/发送数据显示
        self.rx_show_check = QCheckBox("显示接收")
        self.rx_show_check.setChecked(True)
        self.tx_show_check = QCheckBox("显示发送")
        self.tx_show_check.setChecked(True)
        self.counter_label = QLabel("RX: 0 字节   TX: 0 字节")
        self.counter_label.setStyleSheet("color: #9aa3ad;")

        self.clear_btn = QPushButton("清空")
        self.save_btn = QPushButton("保存")
        self.load_btn = QPushButton("打开历史")

        action_bar.addWidget(self.rx_show_check)
        action_bar.addWidget(self.tx_show_check)
        action_bar.addStretch(1)
        action_bar.addWidget(self.counter_label)
        action_bar.addSpacing(8)
        action_bar.addWidget(self.clear_btn)
        action_bar.addWidget(self.save_btn)
        action_bar.addWidget(self.load_btn)

        self.rx_text = QTextEdit()
        self.rx_text.setReadOnly(True)
        self.rx_text.setAcceptRichText(False)
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed.setPointSize(10)
        self.rx_text.setFont(fixed)

        v.addLayout(fmt_bar)
        v.addLayout(action_bar)
        v.addWidget(self.rx_text, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(group)

        # 信号接线
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        self.save_btn.clicked.connect(self.save_clicked.emit)
        self.load_btn.clicked.connect(self.load_clicked.emit)
        self.rx_hex_check.toggled.connect(self._on_display_changed)
        self.rx_encoding_combo.currentTextChanged.connect(self._on_display_changed)
        self.timestamp_check.toggled.connect(self._on_display_changed)
        self.wrap_check.toggled.connect(self._on_display_changed)
        self.scroll_check.toggled.connect(self._on_scroll_changed)
        self.rx_show_check.toggled.connect(self._on_display_changed)
        self.tx_show_check.toggled.connect(self._on_display_changed)

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
    def append_rx(self, data: bytes) -> None:
        self._append("RX", bytes(data), "")

    def append_tx(self, data: bytes, source: str = "human") -> None:
        self._append("TX", bytes(data), source)

    def _append(self, direction: str, data: bytes, source: str) -> None:
        ts = datetime.now()
        self._entries.append((direction, ts, data, source))
        if len(self._entries) > MAX_ENTRIES:
            del self._entries[: len(self._entries) - MAX_ENTRIES]
        if direction == "RX":
            self._rx_bytes += len(data)
        else:
            self._tx_bytes += len(data)
        self._update_counters()
        # 可视开关：显示接收/发送数据（计数与历史记录不受影响）
        show = (direction == "RX" and self.rx_show_check.isChecked()) or \
               (direction == "TX" and self.tx_show_check.isChecked())
        if show:
            self._insert_chunk(self.rx_text.textCursor(), direction, data, ts, source)
            self._autoscroll()

    def clear(self) -> None:
        self._entries.clear()
        self._rx_bytes = 0
        self._tx_bytes = 0
        self.rx_text.clear()
        self._update_counters()

    # ---------------- 渲染 ----------------
    def _format_data(self, data: bytes) -> str:
        if self.rx_hex_check.isChecked():
            return bytes_to_hex(data)
        return bytes_to_text(data, self.rx_encoding_combo.currentText())

    def _insert_chunk(self, cursor: QTextCursor, direction: str, data: bytes,
                      ts: datetime, source: str = "") -> None:
        cursor.movePosition(QTextCursor.MoveOperation.End)
        pal = active_palette()
        fmt_ts = QTextCharFormat()
        fmt_ts.setForeground(pal.ts)
        fmt_data = QTextCharFormat()

        if direction == "TX":
            if source == "ai":
                fmt_data.setForeground(pal.ai)
                tag = "→[AI] "
            elif source == "tcp":
                fmt_data.setForeground(pal.tcp)
                tag = "→[TCP] "
            else:
                fmt_data.setForeground(pal.tx)
                tag = "→[人] "
        else:
            fmt_data.setForeground(pal.rx)
            tag = "← "

        if self.timestamp_check.isChecked():
            cursor.insertText(f"[{ts.strftime('%H:%M:%S.%f')[:-3]}] ", fmt_ts)
        cursor.insertText(tag, fmt_data)
        cursor.insertText(self._format_data(data), fmt_data)
        if self.wrap_check.isChecked() or self.timestamp_check.isChecked():
            cursor.insertText("\n", fmt_data)

    def _re_render(self) -> None:
        self.rx_text.setUpdatesEnabled(False)
        self.rx_text.clear()
        cursor = self.rx_text.textCursor()
        for direction, ts, data, source in self._entries:
            if direction == "RX" and not self.rx_show_check.isChecked():
                continue
            if direction == "TX" and not self.tx_show_check.isChecked():
                continue
            self._insert_chunk(cursor, direction, data, ts, source)
        self.rx_text.setUpdatesEnabled(True)
        self._autoscroll()

    def _autoscroll(self) -> None:
        if self.scroll_check.isChecked():
            self.rx_text.moveCursor(QTextCursor.MoveOperation.End)
            self.rx_text.ensureCursorVisible()

    def _update_counters(self) -> None:
        self.counter_label.setText(f"RX: {self._rx_bytes} 字节   TX: {self._tx_bytes} 字节")

    # ---------------- 对外接口 ----------------
    def receive_text(self) -> str:
        return self.rx_text.toPlainText()

    def raw_entries(self) -> list[tuple[str, datetime, bytes, str]]:
        return list(self._entries)

    def append_analysis(self, text: str) -> None:
        """把加载的历史文件文本作为一个可读块追加显示（带分隔头）。"""
        pal = active_palette()
        cursor = self.rx_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(pal.ts)
        cursor.insertText("\n────────── 加载的历史数据 ──────────\n", fmt)
        body_fmt = QTextCharFormat()
        body_fmt.setForeground(pal.rx)
        cursor.insertText(text, body_fmt)
        cursor.insertText("\n────────────── 结束 ──────────────\n", fmt)
        self._autoscroll()

    def refresh_display(self) -> None:
        """主题/显示选项变化后按当前调色板整体重绘（历史记录换色）。"""
        self._re_render()

    def apply_config(self, cfg: dict) -> None:
        self._applying = True
        try:
            self.rx_hex_check.setChecked(bool(cfg.get("rx_hex", False)))
            enc = cfg.get("rx_encoding", "UTF-8")
            idx = self.rx_encoding_combo.findText(enc)
            if idx >= 0:
                self.rx_encoding_combo.setCurrentIndex(idx)
            self.timestamp_check.setChecked(bool(cfg.get("timestamp", False)))
            self.wrap_check.setChecked(bool(cfg.get("auto_wrap", True)))
            self.scroll_check.setChecked(bool(cfg.get("auto_scroll", True)))
            self.rx_show_check.setChecked(bool(cfg.get("rx_show", True)))
            self.tx_show_check.setChecked(bool(cfg.get("tx_show", True)))
        finally:
            self._applying = False
        self._re_render()

    def settings_dict(self) -> dict:
        return {
            "rx_hex": self.rx_hex_check.isChecked(),
            "rx_encoding": self.rx_encoding_combo.currentText(),
            "timestamp": self.timestamp_check.isChecked(),
            "auto_wrap": self.wrap_check.isChecked(),
            "auto_scroll": self.scroll_check.isChecked(),
            "rx_show": self.rx_show_check.isChecked(),
            "tx_show": self.tx_show_check.isChecked(),
        }
