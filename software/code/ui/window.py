"""主窗口外壳：顶部工具条 + 左右分栏 + 状态栏。

顶部工具条：☰ 配置折叠开关 | 运行模式选择 ……… 「设置」菜单（页面风格 / 配置文件）。
左列：参数设置分组（串口连接 / AI 联动 / RTT·J-Link / TCP 转发 / JustFloat / 浮点录制，按模式显隐）；
右列：数据接收 / 发送（串口模式）或 RTT 控制台（RTT 模式）。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from ui.panels.float_recorder import FloatRecorderPanel
from ui.panels.justfloat import JustFloatPanel
from ui.panels.receive import ReceivePanel
from ui.panels.rtt import RttPanel
from ui.panels.send import SendPanel
from ui.panels.settings import MODES, SettingsPanel
from ui.panels.tcp_forward import TcpForwardPanel


class MainWindow(QMainWindow):
    theme_changed = pyqtSignal(str)       # 顶部「设置」→ 页面风格（dark/light）
    mode_selected = pyqtSignal(str)       # 顶部运行模式下拉选择
    load_config_clicked = pyqtSignal()    # 顶部「设置」→ 加载配置…
    save_config_clicked = pyqtSignal()    # 顶部「设置」→ 保存配置…

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI嵌入式助手")
        self._mode_syncing = False

        self.settings = SettingsPanel()
        self.tcp = TcpForwardPanel()
        self.justfloat = JustFloatPanel()
        self.recorder = FloatRecorderPanel()
        self.receive = ReceivePanel()
        self.send = SendPanel()
        self.rtt = RttPanel()

        # ---- 左列：参数设置（可折叠） ----
        left = QWidget()
        self.config_panel = left
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(8)
        left_box.addWidget(self.settings)
        left_box.addWidget(self.tcp)
        left_box.addWidget(self.justfloat)
        left_box.addWidget(self.recorder)
        left_box.addStretch(1)

        # ---- 右列：接收（上）+ 发送（下） / RTT 控制台 ----
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(8)
        right_box.addWidget(self.receive, stretch=1)
        right_box.addWidget(self.send)
        right_box.addWidget(self.rtt, stretch=1)

        # ---- 左右分栏 ----
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([440, 720])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ---------------- 顶部工具条 ----------------
        top_bar = QHBoxLayout()
        self.config_toggle = QPushButton("☰ 配置")
        self.config_toggle.setCheckable(True)
        self.config_toggle.setChecked(False)
        self.config_toggle.setToolTip("点击展开/收起左侧参数栏")
        self.config_toggle.toggled.connect(self._toggle_config)
        top_bar.addWidget(self.config_toggle)
        top_bar.addSpacing(6)

        # 运行模式：全局级切换，放顶部而非设置面板
        top_bar.addWidget(QLabel("运行模式"))
        self.mode_combo = QComboBox()
        for label, value in MODES:
            self.mode_combo.addItem(label, value)
        self.mode_combo.setToolTip(
            "切换后立即生效（动态加载/卸载 RTT 与调试插件）"
        )
        self.mode_combo.setMinimumWidth(170)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)
        top_bar.addWidget(self.mode_combo)
        top_bar.addStretch(1)

        # 「设置」菜单：页面风格 + 配置文件
        self._menu = QMenu(self)
        self._style_submenu = self._menu.addMenu("页面风格")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, object] = {}
        for key, label in (("dark", "深色主题"), ("light", "浅色主题（白底橙黑）")):
            act = self._style_submenu.addAction(label)
            act.setCheckable(True)
            act.setData(key)
            act.triggered.connect(self._on_theme_action)
            self._theme_group.addAction(act)
            self._theme_actions[key] = act
        self._theme_actions["dark"].setChecked(True)

        self._menu.addSeparator()
        self._load_cfg_action = self._menu.addAction("加载配置…")
        self._load_cfg_action.triggered.connect(self.load_config_clicked.emit)
        self._save_cfg_action = self._menu.addAction("保存配置…")
        self._save_cfg_action.triggered.connect(self.save_config_clicked.emit)
        self._menu.addSeparator()
        self._cfg_path_action = self._menu.addAction("当前配置文件：-")
        self._cfg_path_action.setEnabled(False)

        self.settings_btn = QPushButton("设置 ▾")
        self.settings_btn.setMenu(self._menu)
        self.settings_btn.setToolTip(
            "页面风格 / 加载与保存配置文件"
        )
        top_bar.addWidget(self.settings_btn)
        top_bar.addSpacing(4)
        layout.addLayout(top_bar)

        layout.addWidget(self.splitter)
        self.setCentralWidget(central)

        # 默认收起参数栏（右列占满）
        self.config_panel.setVisible(False)

        self.state_label = QLabel("未连接")
        self.statusBar().addWidget(self.state_label, 1)

        self.set_mode("serial")
        self.resize(980, 650)
        # 最小宽度随参数栏显隐切换：收起时可压得更窄（展开时自动加宽避免挤压）
        self._min_w_open = 940
        self._min_w_closed = 760
        self.setMinimumSize(self._min_w_closed, 540)

    # ---------------- 页面风格 ----------------
    def _on_theme_action(self):
        act = self.sender()
        if act is not None and act.isChecked():
            self.theme_changed.emit(str(act.data() or "dark"))

    def set_theme(self, name: str) -> None:
        """同步菜单选中态（外部按配置/切换结果调用）。"""
        act = self._theme_actions.get(name)
        if act is not None:
            act.setChecked(True)

    def set_config_path(self, path: str) -> None:
        """「设置」菜单中展示当前配置文件路径（只读提示）。"""
        if path:
            self._cfg_path_action.setText(f"当前配置文件：{path}")
            self._cfg_path_action.setToolTip(path)

    # ---------------- 运行模式 ----------------
    def _on_mode_combo_changed(self, index: int):
        if not self._mode_syncing:
            self.mode_selected.emit(self.mode_combo.currentData() or "serial")

    def _sync_mode_combo(self, mode: str) -> None:
        """按当前生效模式同步下拉框（不发信号，避免回环）。"""
        self._mode_syncing = True
        try:
            idx = self.mode_combo.findData(mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
        finally:
            self._mode_syncing = False

    def _toggle_config(self, checked: bool) -> None:
        self.config_panel.setVisible(checked)
        self.setMinimumWidth(self._min_w_open if checked else self._min_w_closed)
        if checked and self.width() < self._min_w_open:
            self.resize(self._min_w_open, self.height())

    # ---------------- 模式显隐 ----------------
    def set_mode(self, mode: str) -> None:
        """按四态模式切换界面。

        serial/serial_vofa → 串口控制台（接收+发送 + 串口连接/AI 联动设置）；
        rtt_shell/rtt_vofa  → RTT 控制台（RTT / J-Link 设置）；
        serial_vofa/rtt_vofa → 波形面板（TCP 转发 / justfloat / 录制）。
        """
        self._sync_mode_combo(mode)
        self.settings.set_mode_ui(mode)
        is_rtt = mode in ("rtt_shell", "rtt_vofa")
        is_wave = mode in ("serial_vofa", "rtt_vofa")
        self.receive.setVisible(not is_rtt)
        self.send.setVisible(not is_rtt)
        self.rtt.setVisible(is_rtt)
        self.tcp.setVisible(is_wave)
        self.justfloat.setVisible(is_wave)
        self.recorder.setVisible(is_wave)

    def set_state(self, text: str) -> None:
        self.state_label.setText(text)

    def show_error(self, text: str) -> None:
        self.statusBar().showMessage(text, 5000)
        QMessageBox.warning(self, "提示", text)

    def show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 4000)
