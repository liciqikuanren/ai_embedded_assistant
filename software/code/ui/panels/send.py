"""发送面板：文本/HEX 输入、编码、换行、定时循环、发送按钮。"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ui.constants import ENCODINGS, NEWLINES


class SendPanel(QWidget):
    send_clicked = pyqtSignal()
    cycle_toggled = pyqtSignal(bool)
    cycle_interval_changed = pyqtSignal(int)
    payload_changed = pyqtSignal()
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._applying = False
        self._build()

    def _build(self):
        group = QGroupBox("数据发送")
        v = QVBoxLayout(group)

        self.tx_text = QPlainTextEdit()
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed.setPointSize(10)
        self.tx_text.setFont(fixed)
        self.tx_text.setMaximumHeight(110)

        # 第一行：格式与编码
        row1 = QHBoxLayout()
        self.tx_hex_check = QCheckBox("HEX 发送")
        self.tx_encoding_combo = QComboBox()
        self.tx_encoding_combo.addItems(ENCODINGS)
        self.newline_check = QCheckBox("发送新行")
        self.newline_combo = QComboBox()
        for kind, label in NEWLINES.items():
            self.newline_combo.addItem(label, kind)

        self.cycle_check = QCheckBox("定时循环发送")
        self.cycle_spin = QSpinBox()
        self.cycle_spin.setRange(10, 3600000)
        self.cycle_spin.setValue(1000)
        self.cycle_spin.setSuffix(" ms")

        row1.addWidget(self.tx_hex_check)
        row1.addWidget(QLabel("编码"))
        row1.addWidget(self.tx_encoding_combo)
        row1.addWidget(self.newline_check)
        row1.addWidget(self.newline_combo)
        row1.addSpacing(12)
        row1.addWidget(self.cycle_check)
        row1.addWidget(self.cycle_spin)
        row1.addStretch(1)

        # 第二行：仅发送按钮（右对齐）
        row2 = QHBoxLayout()
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("SendButton")
        self.send_btn.setMinimumWidth(96)
        row2.addStretch(1)
        row2.addWidget(self.send_btn)

        v.addWidget(self.tx_text)
        v.addLayout(row1)
        v.addLayout(row2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(group)

        # 信号接线
        self.send_btn.clicked.connect(self.send_clicked.emit)
        self.cycle_check.toggled.connect(self._on_cycle_toggled)
        self.cycle_spin.valueChanged.connect(self._on_cycle_interval_changed)

        # 发送相关输入变化 → 通知控制器（循环发送时同步 payload）+ 持久化
        self.tx_text.textChanged.connect(self._on_send_input_changed)
        self.tx_hex_check.toggled.connect(self._on_send_input_changed)
        self.tx_encoding_combo.currentTextChanged.connect(self._on_send_input_changed)
        self.newline_check.toggled.connect(self._on_send_input_changed)
        self.newline_combo.currentTextChanged.connect(self._on_send_input_changed)

    def _on_cycle_toggled(self, checked: bool):
        if self._applying:
            return
        self.cycle_toggled.emit(checked)
        self.settings_changed.emit()

    def _on_cycle_interval_changed(self, value: int):
        if self._applying:
            return
        self.cycle_interval_changed.emit(value)
        self.settings_changed.emit()

    def _on_send_input_changed(self, *args):
        if self._applying:
            return
        self.payload_changed.emit()
        self.settings_changed.emit()

    # ---------------- 对外接口 ----------------
    def send_text(self) -> str:
        return self.tx_text.toPlainText()

    def tx_hex(self) -> bool:
        return self.tx_hex_check.isChecked()

    def tx_encoding(self) -> str:
        return self.tx_encoding_combo.currentText()

    def send_newline(self) -> bool:
        return self.newline_check.isChecked()

    def newline_kind(self) -> str:
        return self.newline_combo.currentData() or "CRLF"

    def cycle_enabled(self) -> bool:
        return self.cycle_check.isChecked()

    def cycle_interval_ms(self) -> int:
        return self.cycle_spin.value()

    def apply_config(self, cfg: dict) -> None:
        self._applying = True
        try:
            self.tx_hex_check.setChecked(bool(cfg.get("tx_hex", False)))
            enc = cfg.get("tx_encoding", "UTF-8")
            idx = self.tx_encoding_combo.findText(enc)
            if idx >= 0:
                self.tx_encoding_combo.setCurrentIndex(idx)
            self.newline_check.setChecked(bool(cfg.get("send_newline", False)))
            kind = cfg.get("newline", "CRLF")
            k_idx = self.newline_combo.findData(kind)
            if k_idx >= 0:
                self.newline_combo.setCurrentIndex(k_idx)
            self.cycle_spin.setValue(int(cfg.get("cycle_interval_ms", 1000)))
            # 循环发送不自动恢复为开启，避免启动即发送
            self.cycle_check.setChecked(False)
        finally:
            self._applying = False

    def settings_dict(self) -> dict:
        return {
            "tx_hex": self.tx_hex(),
            "tx_encoding": self.tx_encoding(),
            "send_newline": self.send_newline(),
            "newline": self.newline_kind(),
            "cycle_send": self.cycle_enabled(),
            "cycle_interval_ms": self.cycle_interval_ms(),
        }
