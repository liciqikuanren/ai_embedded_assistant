"""TCP 转发面板：服务端监听配置 + 启停开关 + 运行状态/客户端数显示。

勾选「启用转发」即启动监听（取消勾选即停止）；地址/端口变更仅持久化，
需重新勾选才生效。
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
    QVBoxLayout, QWidget,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000


class TcpForwardPanel(QWidget):
    toggled = pyqtSignal(bool)          # 启用开关（勾选=启动，取消=停止）
    settings_changed = pyqtSignal()     # 地址/端口变更 → 持久化

    def __init__(self, parent=None):
        super().__init__(parent)
        self._applying = False
        self._build()

    def _build(self):
        group = QGroupBox("TCP 转发（服务端）")
        row = QHBoxLayout(group)
        row.setSpacing(8)

        self.enable_check = QCheckBox("启用转发")
        self.host_edit = QLineEdit(DEFAULT_HOST)
        self.host_edit.setPlaceholderText("监听地址")
        self.host_edit.setMinimumWidth(120)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_PORT)
        self.port_spin.setMinimumWidth(90)

        self.status_label = QLabel("未启动")
        self.status_label.setStyleSheet("color: #888;")
        self.client_label = QLabel("客户端: 0")
        self.client_label.setStyleSheet("color: #9aa3ad;")

        row.addWidget(self.enable_check)
        row.addWidget(QLabel("地址"))
        row.addWidget(self.host_edit, 1)
        row.addWidget(QLabel("端口"))
        row.addWidget(self.port_spin)
        row.addSpacing(12)
        row.addWidget(self.status_label)
        row.addWidget(self.client_label)
        row.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(group)

        self.enable_check.toggled.connect(self._on_toggled)
        self.host_edit.textChanged.connect(self._on_param_changed)
        self.port_spin.valueChanged.connect(self._on_param_changed)

    def _on_toggled(self, checked: bool):
        if not self._applying:
            self.toggled.emit(checked)

    def _on_param_changed(self, *args):
        if not self._applying:
            self.settings_changed.emit()

    # ---------------- 对外接口 ----------------
    def tcp_params(self) -> dict:
        return {
            "host": self.host_edit.text().strip() or DEFAULT_HOST,
            "port": self.port_spin.value(),
        }

    def apply_config(self, cfg: dict) -> None:
        """恢复地址/端口；启动时默认不自动开启监听（由用户勾选启用）。"""
        self._applying = True
        try:
            self.host_edit.setText(str(cfg.get("tcp_host", DEFAULT_HOST)))
            self.port_spin.setValue(int(cfg.get("tcp_port", DEFAULT_PORT)))
            self.enable_check.setChecked(False)
        finally:
            self._applying = False

    def settings_dict(self) -> dict:
        p = self.tcp_params()
        return {"tcp_host": p["host"], "tcp_port": p["port"]}

    def set_status(self, status: dict) -> None:
        """刷新状态/客户端数标签，并同步开关勾选（启动失败时自动复位）。"""
        status = status or {}
        running = bool(status.get("running"))
        host = status.get("host", "") or DEFAULT_HOST
        port = int(status.get("port", 0))
        clients = int(status.get("clients", 0))
        if running:
            self.status_label.setText(f"运行中 {host}:{port}")
            self.status_label.setStyleSheet("color: #4caf50;")
        else:
            self.status_label.setText("未启动")
            self.status_label.setStyleSheet("color: #888;")
        self.client_label.setText(f"客户端: {clients}")
        self._applying = True
        try:
            self.enable_check.setChecked(running)
        finally:
            self._applying = False
