"""justfloat 协议解析面板：解析开关 + 统计 + 通道表格（名称可编辑重命名 + 最新值预览）。"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

_READONLY = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled


class JustFloatPanel(QWidget):
    toggled = pyqtSignal(bool)       # 解析开关
    reset_clicked = pyqtSignal()     # 重置解析器
    renamed = pyqtSignal(dict)       # 通道重命名 {索引: 名称}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._applying = False
        self._loading = False
        self._build()

    def _build(self):
        group = QGroupBox("JustFloat 协议解析")
        v = QVBoxLayout(group)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.enable_check = QCheckBox("启用解析")
        self.enable_check.setChecked(True)
        self.stats_label = QLabel("帧: 0 | 图片帧: 0 | 丢弃: 0 B")
        self.stats_label.setStyleSheet("color: #9aa3ad;")
        self.reset_btn = QPushButton("重置解析器")
        self.reset_btn.setEnabled(False)
        row.addWidget(self.enable_check)
        row.addWidget(self.stats_label)
        row.addStretch(1)
        row.addWidget(self.reset_btn)
        v.addLayout(row)

        # 通道表：通道编号 | 名称（可编辑，重命名）| 最新值
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["通道", "名称", "最新值"])
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(140)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.table)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(group)

        self.enable_check.toggled.connect(self._on_toggled)
        self.reset_btn.clicked.connect(self.reset_clicked.emit)
        self.table.itemChanged.connect(self._on_item_changed)

    def _on_toggled(self, checked: bool):
        if not self._applying:
            self.toggled.emit(checked)

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() != 1:
            return
        text = item.text().strip()
        if text:
            self.renamed.emit({item.row(): text})

    # ---------------- 对外接口 ----------------
    def set_enabled(self, enabled: bool) -> None:
        self._applying = True
        try:
            self.enable_check.setChecked(enabled)
        finally:
            self._applying = False
        self.reset_btn.setEnabled(enabled)

    def set_stats(self, stats: dict) -> None:
        stats = stats or {}
        self.stats_label.setText(
            f"帧: {stats.get('frames', 0)} | 图片帧: {stats.get('image_frames', 0)}"
            f" | 丢弃: {stats.get('dropped_bytes', 0)} B"
            f" | 异常: {stats.get('malformed', 0)}"
        )
        self.reset_btn.setEnabled(bool(stats.get("enabled", True)))
        latest = stats.get("latest") or {}
        names = latest.get("names") or []
        values = latest.get("values") or []
        self._update_table(names, values)

    def _update_table(self, names: list, values: list) -> None:
        n = max(len(names), len(values))
        self._loading = True
        try:
            if self.table.rowCount() != n:
                self.table.setRowCount(n)
            for i in range(n):
                name_item = self.table.item(i, 0)
                if name_item is None:
                    name_item = QTableWidgetItem(f"Ch{i}")
                    name_item.setFlags(_READONLY)  # 编号列不可编辑
                    self.table.setItem(i, 0, name_item)
                else:
                    name_item.setText(f"Ch{i}")
                label = names[i] if i < len(names) else f"Ch{i}"
                label_item = self.table.item(i, 1)
                if label_item is None:
                    label_item = QTableWidgetItem(label)
                    self.table.setItem(i, 1, label_item)
                elif label_item.text() != label:
                    label_item.setText(label)
                val = values[i] if i < len(values) else None
                val_text = f"{val:.4g}" if val is not None else ""
                val_item = self.table.item(i, 2)
                if val_item is None:
                    val_item = QTableWidgetItem(val_text)
                    val_item.setFlags(_READONLY)  # 数值列不可编辑
                    self.table.setItem(i, 2, val_item)
                elif val_item.text() != val_text:
                    val_item.setText(val_text)
        finally:
            self._loading = False

    def apply_config(self, cfg: dict) -> None:
        self._applying = True
        try:
            self.enable_check.setChecked(bool(cfg.get("justfloat_enabled", True)))
        finally:
            self._applying = False
        self.reset_btn.setEnabled(bool(cfg.get("justfloat_enabled", True)))
