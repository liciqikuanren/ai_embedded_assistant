"""设置面板容器：按功能拆成三个独立分组（避免把无关内容堆进「串口设置」）。

- 「串口连接」：端口/波特率/高级参数/打开串口（仅带串口的模式显示）；
- 「AI 联动」：数据推送到 DSH + MCP 服务状态（串口与 RTT 模式均显示）；
- 「RTT / 调试探针设置」：探针(J-Link/DAPLink)/接口/速度/序列号/控制块地址（仅 RTT 模式显示）。

运行模式选择移到窗口顶部工具条；配置文件加载/保存移到顶部「设置」菜单。
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ui.constants import (
    BAUD_RATES, DATA_BITS, FLOW_CONTROL, PARITY, PARITY_BY_VALUE, STOP_BITS,
)

MODES = [
    ("串口交互", "serial"),
    ("串口 + TCP VoFA 转发", "serial_vofa"),
    ("RTT Shell 交互", "rtt_shell"),
    ("RTT Shell + 波形 + TCP VoFA", "rtt_vofa"),
]


class SettingsPanel(QWidget):
    open_clicked = pyqtSignal()
    refresh_clicked = pyqtSignal()
    auto_reconnect_toggled = pyqtSignal(bool)
    params_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._applying = False
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # ================= 「串口连接」（仅带串口模式） =================
        serial_group = QGroupBox("串口连接")
        grid = QGridLayout(serial_group)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self.port_combo = QComboBox()  # 下拉选择，自动扫描
        self.port_combo.setMinimumWidth(170)
        self.refresh_btn = QPushButton("刷新")

        self.baud_combo = QComboBox()  # 可编辑，支持自定义波特率
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(BAUD_RATES)
        self.baud_combo.setMinimumWidth(110)

        self.advanced_btn = QPushButton("高级设置 ▾")
        self.advanced_btn.setCheckable(True)
        self.advanced_btn.toggled.connect(self._on_advanced_toggled)

        self.open_btn = QPushButton("打开串口")
        self.open_btn.setObjectName("OpenButton")
        self.open_btn.setProperty("connected", False)
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_clicked.emit)
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self.refresh_clicked.emit)

        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.refresh_btn)

        baud_row = QHBoxLayout()
        baud_row.setSpacing(8)
        baud_row.addWidget(QLabel("波特率"))
        baud_row.addWidget(self.baud_combo)
        baud_row.addWidget(self.advanced_btn)
        baud_row.addWidget(self.open_btn)
        baud_row.addStretch(1)

        # 第 1 行：端口号（含刷新）；第 2 行：波特率/高级/打开（避免一行放不下）
        grid.addWidget(QLabel("端口号"), 0, 0)
        grid.addLayout(port_row, 0, 1, 1, 5)
        grid.addLayout(baud_row, 1, 0, 1, 6)
        grid.setColumnStretch(1, 1)

        # 高级项（默认折叠）
        self.data_combo = QComboBox()
        self.data_combo.addItems(DATA_BITS)
        self.stop_combo = QComboBox()
        self.stop_combo.addItems(STOP_BITS)
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(list(PARITY.keys()))
        self.flow_combo = QComboBox()
        self.flow_combo.addItems(FLOW_CONTROL)
        self.reconnect_check = QCheckBox("断线自动重连")

        self.advanced_widget = QWidget()
        adv = QHBoxLayout(self.advanced_widget)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setSpacing(8)
        adv.addWidget(QLabel("数据位"))
        adv.addWidget(self.data_combo)
        adv.addWidget(QLabel("停止位"))
        adv.addWidget(self.stop_combo)
        adv.addWidget(QLabel("校验位"))
        adv.addWidget(self.parity_combo)
        adv.addWidget(QLabel("流控"))
        adv.addWidget(self.flow_combo)
        adv.addWidget(self.reconnect_check)
        adv.addStretch(1)
        self.advanced_widget.setVisible(False)
        grid.addWidget(self.advanced_widget, 2, 0, 1, 6)

        # ================= 「AI 联动」（所有模式通用） =================
        ai_group = QGroupBox("AI 联动")
        ai_row = QHBoxLayout(ai_group)
        ai_row.setSpacing(8)
        self.ai_push_check = QCheckBox("推送数据到 DSH")
        self.ai_push_check.setToolTip(
            "前置：DSH（含 ai-embedded-bridge 插件）与本软件内置 MCP(8765) 需在线。"
            "串口模式：推送串口接收数据；RTT 模式：推送 Shell 输出"
            "（AI 自己命令的响应不推送）。"
        )
        self.ai_push_mode_combo = QComboBox()
        self.ai_push_mode_combo.addItem("聊天", "chat")
        self.ai_push_mode_combo.addItem("监听", "monitor")
        self.ai_push_mode_combo.setEnabled(False)
        self.ai_push_check.toggled.connect(self.ai_push_mode_combo.setEnabled)
        ai_row.addWidget(self.ai_push_check)
        ai_row.addWidget(QLabel("模式"))
        ai_row.addWidget(self.ai_push_mode_combo)
        self.ai_status_label = QLabel("AI嵌入式工具：启动中...")
        self.ai_status_label.setStyleSheet("color: #888;")
        ai_row.addSpacing(12)
        ai_row.addWidget(self.ai_status_label)
        ai_row.addStretch(1)

        # ================= 「RTT / 调试探针设置」（仅 RTT 模式） =================
        rtt_group = QGroupBox("RTT / 调试探针设置")
        rtt_grid = QGridLayout(rtt_group)
        rtt_grid.setContentsMargins(10, 8, 8, 8)
        rtt_grid.setHorizontalSpacing(6)
        rtt_grid.setVerticalSpacing(6)

        self.rtt_probe_combo = QComboBox()
        self.rtt_probe_combo.addItem("J-Link (pylink)", "jlink")
        self.rtt_probe_combo.addItem("DAPLink / CMSIS-DAP (pyOCD)", "dap")
        self.rtt_probe_combo.setToolTip(
            "RTT 与探针无关（控制块在目标 RAM），两种探针都能用：\n"
            "J-Link 走 SEGGER pylink；DAPLink/任意 CMSIS-DAP 走 pyOCD。"
        )

        self.rtt_iface_combo = QComboBox()
        self.rtt_iface_combo.addItems(["SWD", "JTAG"])

        self.rtt_chip_edit = QLineEdit("STM32H743XI")
        self.rtt_chip_edit.setPlaceholderText("J-Link 芯片型号，如 STM32H743XI")
        self.rtt_speed_edit = QLineEdit("4000")
        self.rtt_speed_edit.setPlaceholderText("接口速度 kHz")
        self.rtt_serial_edit = QLineEdit("")
        self.rtt_serial_edit.setPlaceholderText("序列号 / DAPLink UID（留空=自动）")
        self.rtt_cb_edit = QLineEdit("")
        self.rtt_cb_edit.setPlaceholderText("RTT 控制块地址（可空，如 0x24006ef8）")

        self.rtt_dll_edit = QLineEdit("")
        self.rtt_dll_edit.setPlaceholderText("JLink_x64.dll（留空=自动搜索 EIDE/SEGGER）")
        self.rtt_dap_target_edit = QLineEdit("stm32h743xx")
        self.rtt_dap_target_edit.setPlaceholderText("pyOCD 目标名，如 stm32h743xx")
        self.rtt_dap_elf_edit = QLineEdit("")
        self.rtt_dap_elf_edit.setPlaceholderText("固件 .axf/.elf（解析 _SEGGER_RTT，留空=扫描 RAM）")

        self._rtt_chip_label = QLabel("芯片")
        self._rtt_dll_label = QLabel("J-Link DLL")
        self._rtt_dap_target_label = QLabel("pyOCD目标")
        self._rtt_dap_elf_label = QLabel("固件 ELF")

        rtt_grid.addWidget(QLabel("探针"), 0, 0)
        rtt_grid.addWidget(self.rtt_probe_combo, 0, 1)
        rtt_grid.addWidget(QLabel("接口"), 0, 2)
        rtt_grid.addWidget(self.rtt_iface_combo, 0, 3)
        rtt_grid.addWidget(self._rtt_chip_label, 1, 0)
        rtt_grid.addWidget(self.rtt_chip_edit, 1, 1)
        rtt_grid.addWidget(QLabel("速度(kHz)"), 1, 2)
        rtt_grid.addWidget(self.rtt_speed_edit, 1, 3)
        rtt_grid.addWidget(QLabel("序列号"), 2, 0)
        rtt_grid.addWidget(self.rtt_serial_edit, 2, 1)
        rtt_grid.addWidget(QLabel("控制块地址"), 2, 2)
        rtt_grid.addWidget(self.rtt_cb_edit, 2, 3)
        rtt_grid.addWidget(self._rtt_dll_label, 3, 0)
        rtt_grid.addWidget(self.rtt_dll_edit, 3, 1, 1, 3)
        rtt_grid.addWidget(self._rtt_dap_target_label, 3, 0)
        rtt_grid.addWidget(self.rtt_dap_target_edit, 3, 1)
        rtt_grid.addWidget(self._rtt_dap_elf_label, 3, 2)
        rtt_grid.addWidget(self.rtt_dap_elf_edit, 3, 3)
        rtt_grid.setColumnStretch(1, 1)
        rtt_grid.setColumnStretch(3, 1)

        outer.addWidget(serial_group)
        outer.addWidget(ai_group)
        outer.addWidget(rtt_group)
        outer.addStretch(1)

        # ---- 信号接线 ----
        self.reconnect_check.toggled.connect(self._on_reconnect_toggled)
        for combo in (self.baud_combo, self.data_combo, self.stop_combo,
                      self.parity_combo, self.flow_combo):
            combo.currentTextChanged.connect(self._on_param_changed)
        self.port_combo.currentTextChanged.connect(self._on_param_changed)
        self.ai_push_check.toggled.connect(self._on_ai_push_changed)
        self.ai_push_mode_combo.currentTextChanged.connect(self._on_ai_push_changed)
        self.rtt_chip_edit.textChanged.connect(self._on_param_changed)
        self.rtt_iface_combo.currentTextChanged.connect(self._on_param_changed)
        self.rtt_speed_edit.textChanged.connect(self._on_param_changed)
        self.rtt_serial_edit.textChanged.connect(self._on_param_changed)
        self.rtt_cb_edit.textChanged.connect(self._on_param_changed)
        self.rtt_dll_edit.textChanged.connect(self._on_param_changed)
        self.rtt_dap_target_edit.textChanged.connect(self._on_param_changed)
        self.rtt_dap_elf_edit.textChanged.connect(self._on_param_changed)
        self.rtt_probe_combo.currentIndexChanged.connect(self._on_probe_changed)

        self._serial_group = serial_group
        self._ai_group = ai_group
        self._rtt_group = rtt_group
        self._update_probe_ui()   # 初始按默认探针显示字段

    def _on_advanced_toggled(self, checked: bool):
        self.advanced_widget.setVisible(checked)
        self.advanced_btn.setText("高级设置 ▴" if checked else "高级设置 ▾")

    def _on_reconnect_toggled(self, checked: bool):
        if not self._applying:
            self.auto_reconnect_toggled.emit(checked)
            self.params_changed.emit()

    def _on_param_changed(self, *args):
        if not self._applying:
            self.params_changed.emit()

    def _on_ai_push_changed(self, *args):
        if not self._applying:
            self.params_changed.emit()

    def _on_probe_changed(self, *args):
        self._update_probe_ui()
        self._on_param_changed()

    def _update_probe_ui(self) -> None:
        """按所选探针显隐专属字段（J-Link：芯片/DLL；DAPLink：pyOCD 目标/固件 ELF）。"""
        is_dap = (self.rtt_probe_combo.currentData() or "jlink") == "dap"
        for w in (self._rtt_chip_label, self.rtt_chip_edit):
            w.setVisible(not is_dap)
        for w in (self._rtt_dll_label, self.rtt_dll_edit):
            w.setVisible(not is_dap)
        for w in (self._rtt_dap_target_label, self.rtt_dap_target_edit,
                  self._rtt_dap_elf_label, self.rtt_dap_elf_edit):
            w.setVisible(is_dap)

    # ---------------- 按运行模式显隐分组 ----------------
    def set_mode_ui(self, mode: str) -> None:
        """串口连接仅带串口模式显示；RTT / J-Link 设置仅 RTT 模式显示；AI 联动全模式可见。"""
        is_rtt = mode in ("rtt_shell", "rtt_vofa")
        self._serial_group.setVisible(not is_rtt)
        self._ai_group.setVisible(True)
        self._rtt_group.setVisible(is_rtt)

    # ---------------- 对外接口 ----------------
    def serial_params(self) -> dict:
        port = self.port_combo.currentData() or ""
        try:
            baudrate = int(self.baud_combo.currentText().strip() or "115200")
        except ValueError:
            baudrate = 115200
        flow = self.flow_combo.currentText()
        return {
            "port": port,
            "baudrate": baudrate,
            "bytesize": int(self.data_combo.currentText()),
            "stopbits": float(self.stop_combo.currentText()),
            "parity": PARITY[self.parity_combo.currentText()],
            "flow": flow,
            "rtscts": flow == "RTS/CTS",
            "xonxoff": flow == "XON/XOFF",
        }

    def reconnect_enabled(self) -> bool:
        return self.reconnect_check.isChecked()

    def set_ports(self, ports: list[dict]) -> None:
        current = self.port_combo.currentData()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        if not ports:
            self.port_combo.addItem("（未检测到串口，请点击刷新）", None)
        else:
            for p in ports:
                desc = p.get("description", "").strip()
                label = f"{p['device']} - {desc}".strip(" -")
                self.port_combo.addItem(label, p["device"])
            if current:
                idx = self.port_combo.findData(current)
                if idx >= 0:
                    self.port_combo.setCurrentIndex(idx)
        self.port_combo.blockSignals(False)

    def set_connected(self, connected: bool, info: str = "") -> None:
        self.open_btn.setText("关闭串口" if connected else "打开串口")
        self.open_btn.setProperty("connected", connected)
        self.open_btn.style().unpolish(self.open_btn)
        self.open_btn.style().polish(self.open_btn)

    def set_enabled(self, enabled: bool) -> None:
        self.open_btn.setEnabled(enabled)
        self.refresh_btn.setEnabled(enabled)

    def apply_serial_params(self, params: dict) -> None:
        """AI 打开端口后，同步设置栏显示当前端口/参数。"""
        self._applying = True
        try:
            port = params.get("port", "")
            if port:
                idx = self.port_combo.findData(port)
                if idx < 0:
                    self.port_combo.addItem(port, port)
                    idx = self.port_combo.findData(port)
                if idx >= 0:
                    self.port_combo.setCurrentIndex(idx)
            self._set_combo_text(self.baud_combo, str(params.get("baudrate", 115200)))
            self._set_combo_index(self.data_combo, str(params.get("bytesize", 8)))
            stop = float(params.get("stopbits", 1))
            stop_str = "1" if stop == 1 else ("1.5" if stop == 1.5 else "2")
            self._set_combo_index(self.stop_combo, stop_str)
            parity_key = PARITY_BY_VALUE.get(params.get("parity", "N"), "None")
            self._set_combo_index(self.parity_combo, parity_key)
            flow = params.get("flow", "None")
            self._set_combo_index(self.flow_combo, flow if flow in FLOW_CONTROL else "None")
        finally:
            self._applying = False

    def apply_config(self, cfg: dict) -> None:
        self._applying = True
        try:
            # 端口号：配置里保存的设备号优先选中；未在扫描列表中则按设备号列出
            port = cfg.get("port", "")
            if port:
                idx = self.port_combo.findData(port)
                if idx < 0:
                    self.port_combo.addItem(port, port)
                    idx = self.port_combo.findData(port)
                if idx >= 0:
                    self.port_combo.setCurrentIndex(idx)
            self._set_combo_text(self.baud_combo, str(cfg.get("baudrate", 115200)))
            self._set_combo_index(self.data_combo, str(cfg.get("bytesize", 8)))
            self._set_combo_index(self.stop_combo, str(cfg.get("stopbits", 1)))
            parity_key = PARITY_BY_VALUE.get(cfg.get("parity", "N"), "None")
            self._set_combo_index(self.parity_combo, parity_key)
            flow = cfg.get("flow", "None")
            self._set_combo_index(self.flow_combo, flow if flow in FLOW_CONTROL else "None")
            self.reconnect_check.setChecked(bool(cfg.get("auto_reconnect", False)))
            self.ai_push_check.setChecked(bool(cfg.get("ai_push_enabled", False)))
            mode = cfg.get("ai_push_mode", "chat")
            idx = self.ai_push_mode_combo.findData(mode)
            if idx >= 0:
                self.ai_push_mode_combo.setCurrentIndex(idx)
            self.rtt_chip_edit.setText(str(cfg.get("rtt_chip", "STM32H743XI")))
            iface = str(cfg.get("rtt_interface", "SWD"))
            idx = self.rtt_iface_combo.findText(iface)
            if idx >= 0:
                self.rtt_iface_combo.setCurrentIndex(idx)
            self.rtt_speed_edit.setText(str(cfg.get("rtt_speed", 4000)))
            self.rtt_serial_edit.setText(str(cfg.get("rtt_serial_no", "")))
            self.rtt_cb_edit.setText(str(cfg.get("rtt_control_block_addr", "")))
            self.rtt_dll_edit.setText(str(cfg.get("rtt_jlink_dll", "")))
            self.rtt_dap_target_edit.setText(str(cfg.get("rtt_dap_target", "stm32h743xx")))
            self.rtt_dap_elf_edit.setText(str(cfg.get("rtt_dap_elf", "")))
            idx = self.rtt_probe_combo.findData(str(cfg.get("rtt_probe", "jlink")))
            if idx >= 0:
                self.rtt_probe_combo.setCurrentIndex(idx)
            self._update_probe_ui()
        finally:
            self._applying = False

    def set_ai_status(self, ready: bool, host: str = "", port: int = 0) -> None:
        """显示内置 MCP（AI嵌入式工具）服务状态。"""
        if ready:
            self.ai_status_label.setText(
                f"服务运行中 http://{host}:{port}/mcp"
            )
            self.ai_status_label.setStyleSheet("color: #4caf50;")
        else:
            self.ai_status_label.setText("服务已停止")
            self.ai_status_label.setStyleSheet("color: #888;")

    def set_ai_push_enabled(self, enabled: bool, reason: str = "") -> None:
        """启用/禁用 AI 联动推送控件（如串口+VoFA 模式禁用：
        justfloat 数据流不得直接注入 DSH 上下文，只能通过录制 CSV 查看）。"""
        self.ai_push_check.setEnabled(enabled)
        self.ai_push_mode_combo.setEnabled(enabled and self.ai_push_check.isChecked())
        self.ai_push_check.setToolTip(
            reason or "前置：需 DSH（含 ai-embedded-bridge 插件）与本软件 MCP(8765) 在线。开启后按模式推送数据（串口：接收数据；RTT：Shell 输出）"
        )

    def settings_dict(self) -> dict:
        p = self.serial_params()
        return {
            "port": p["port"],
            "baudrate": p["baudrate"],
            "bytesize": p["bytesize"],
            "stopbits": p["stopbits"],
            "parity": p["parity"],
            "flow": p["flow"],
            "auto_reconnect": self.reconnect_enabled(),
            "ai_push_enabled": self.ai_push_check.isChecked(),
            "ai_push_mode": self.ai_push_mode_combo.currentData() or "chat",
            "rtt_probe": self.rtt_probe_combo.currentData() or "jlink",
            "rtt_chip": self.rtt_chip_edit.text().strip() or "STM32H743XI",
            "rtt_interface": self.rtt_iface_combo.currentText(),
            "rtt_speed": self.rtt_speed_edit.text().strip() or "4000",
            "rtt_serial_no": self.rtt_serial_edit.text().strip(),
            "rtt_control_block_addr": self.rtt_cb_edit.text().strip(),
            "rtt_jlink_dll": self.rtt_dll_edit.text().strip(),
            "rtt_dap_target": self.rtt_dap_target_edit.text().strip() or "stm32h743xx",
            "rtt_dap_elf": self.rtt_dap_elf_edit.text().strip(),
        }

    @staticmethod
    def _set_combo_text(combo: QComboBox, text: str) -> None:
        idx = combo.findText(text)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentText(text)

    @staticmethod
    def _set_combo_index(combo: QComboBox, text: str) -> None:
        idx = combo.findText(text)
        if idx >= 0:
            combo.setCurrentIndex(idx)
