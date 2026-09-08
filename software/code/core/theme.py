"""应用级主题：QSS + 自定义复选框绘制。内置深色（默认）与浅色（白底橙黑）两套风格。

切换：theme.set_active(name) 后调用 QApplication.setStyleSheet(theme.theme_stylesheet(name))；
文本面板渲染颜色走 active_palette()，随主题即时生效。
"""

from pathlib import Path

from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QProxyStyle, QStyle, QStyleFactory


# ---------------- 调色板（随主题切换；供 QTextEdit 等富文本颜色渲染使用） ----------------
class Palette:
    def __init__(self, rx, tx, ai, tcp, ts, accent, accent_hover):
        self.rx = QColor(rx)          # 接收数据
        self.tx = QColor(tx)          # 人发送
        self.ai = QColor(ai)          # AI 发送
        self.tcp = QColor(tcp)        # TCP 转发来源（客户端 → 串口）
        self.ts = QColor(ts)          # 时间戳/弱化文字
        self.accent = QColor(accent)
        self.accent_hover = QColor(accent_hover)


PALETTES = {
    "dark": Palette("#d8dde5", "#5ee6a8", "#c084fc", "#fbbf24", "#8a8f98",
                    "#4f8ef7", "#6ba0f9"),
    "light": Palette("#1c2026", "#0e9f6e", "#9333ea", "#c2410c", "#6b7280",
                     "#f97316", "#fb923c"),
}
_ACTIVE_THEME = "dark"


def active_theme() -> str:
    return _ACTIVE_THEME


def is_light() -> bool:
    return _ACTIVE_THEME == "light"


def set_active(name: str) -> None:
    """切换当前激活主题（只影响渲染调色板；QSS 需另行 setStyleSheet）。"""
    global _ACTIVE_THEME
    if name in PALETTES:
        _ACTIVE_THEME = name


def active_palette() -> Palette:
    return PALETTES[_ACTIVE_THEME]


class CheckBoxStyle(QProxyStyle):
    """绘制带白色对勾的复选框 + 微调三角（配色随当前主题切换）。"""

    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            self._draw_checkbox(option, painter)
        elif element in (QStyle.PrimitiveElement.PE_IndicatorSpinUp,
                         QStyle.PrimitiveElement.PE_IndicatorSpinDown):
            self._draw_spin_arrow(
                option, painter,
                up=element == QStyle.PrimitiveElement.PE_IndicatorSpinUp,
            )
        else:
            super().drawPrimitive(element, option, painter, widget)

    def _draw_checkbox(self, option, painter):
        rect = option.rect
        size = min(rect.width(), rect.height()) - 2
        box = QRect(rect.center().x() - size // 2,
                    rect.center().y() - size // 2, size, size)

        checked = bool(option.state & QStyle.StateFlag.State_On)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        pal = active_palette()
        light = is_light()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not enabled:
            border = QColor("#c4c9d1") if light else QColor("#33373d")
            fill = QColor("#e9eaec") if light else QColor("#222529")
        elif checked:
            border = pal.accent
            fill = pal.accent
        else:
            border = (pal.accent_hover if hover else QColor("#9aa3ad")) if light \
                else (pal.accent_hover if hover else QColor("#4a4f57"))
            fill = QColor("#ffffff") if light else QColor("#23252a")

        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3, 3)

        if checked:
            pen = QPen(QColor("#ffffff"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            w, h = box.width(), box.height()
            path = QPainterPath()
            path.moveTo(QPointF(box.x() + w * 0.25, box.y() + h * 0.54))
            path.lineTo(QPointF(box.x() + w * 0.44, box.y() + h * 0.72))
            path.lineTo(QPointF(box.x() + w * 0.76, box.y() + h * 0.30))
            painter.drawPath(path)

        painter.restore()

    def _draw_spin_arrow(self, option, painter, up: bool):
        """绘制上下微调三角箭头（颜色随主题：深色主题浅灰，浅色主题深灰）。"""
        rect = option.rect
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        if enabled:
            color = QColor("#33373d") if is_light() else QColor("#d6dae0")
        else:
            color = QColor("#b6bbc3") if is_light() else QColor("#5c626b")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)

        w = 8.0
        h = 5.0
        cx = rect.center().x()
        cy = rect.center().y()
        if up:
            points = [
                QPointF(cx - w / 2, cy + h * 0.6),
                QPointF(cx + w / 2, cy + h * 0.6),
                QPointF(cx, cy - h * 0.4),
            ]
        else:
            points = [
                QPointF(cx - w / 2, cy - h * 0.6),
                QPointF(cx + w / 2, cy - h * 0.6),
                QPointF(cx, cy + h * 0.4),
            ]
        painter.drawPolygon(QPolygonF(points))
        painter.restore()


STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1b1d21;
    color: #d6dae0;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1px solid #2f333a;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    background-color: #222529;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #8ab4ff;
}

QLabel { background: transparent; }

QPushButton {
    background-color: #2c3036;
    border: 1px solid #3a3f47;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e6e9ee;
}
QPushButton:hover { background-color: #343941; border-color: #4a505a; }
QPushButton:pressed { background-color: #262a30; }
QPushButton:disabled { color: #5c626b; background-color: #222529; border-color: #2f333a; }
/* 挂菜单的按钮文字自带 ▾，禁用 Qt 额外补画的 menu-indicator 箭头（避免双箭头） */
QPushButton::menu-indicator { width: 0px; image: none; }

QPushButton#OpenButton[connected="true"] {
    background-color: #1e7a4d;
    border-color: #2ca05f;
    color: #eafff2;
}
QPushButton#OpenButton[connected="true"]:hover { background-color: #24885a; }

QPushButton#SendButton, QPushButton#OpenButton {
    background-color: #2f5da8;
    border-color: #3a6fca;
    color: #f0f5ff;
}
QPushButton#SendButton:hover, QPushButton#OpenButton:hover { background-color: #3768bd; }

QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #282b30;
    border: 1px solid #3a3f47;
    border-radius: 6px;
    padding: 4px 8px;
    color: #e6e9ee;
    min-height: 18px;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #4a505a; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #4f8ef7; }
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: #33383f;
    border: 1px solid #4a505a;
    width: 18px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #3e444d;
    border-color: #5a6270;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
    background-color: #262a30;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #282b30;
    border: 1px solid #3a3f47;
    border-radius: 4px;
    selection-background-color: #2f5da8;
    selection-color: #f0f5ff;
    color: #e6e9ee;
    padding: 2px;
}

QCheckBox { background: transparent; spacing: 7px; color: #d6dae0; }
QCheckBox:disabled { color: #5c626b; }

QPlainTextEdit, QTextEdit {
    background-color: #17191d;
    border: 1px solid #2f333a;
    border-radius: 6px;
    color: #d8dde5;
    selection-background-color: #2f5da8;
    padding: 5px;
}
QPlainTextEdit:focus, QTextEdit:focus { border-color: #4f8ef7; }

QStatusBar {
    background-color: #222529;
    color: #8a8f98;
    border-top: 1px solid #2f333a;
}

QScrollBar:vertical { background: #222529; width: 12px; margin: 0; border: none; }
QScrollBar::handle:vertical { background: #3a3f47; border-radius: 6px; min-height: 26px; }
QScrollBar::handle:vertical:hover { background: #4a505a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #222529; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #3a3f47; border-radius: 6px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QToolTip {
    background-color: #2c3036; color: #e6e9ee;
    border: 1px solid #3a3f47; padding: 4px;
}
"""

# ---------------- 微调/下拉箭头：QSS 覆盖下默认不绘制，用图片显式绘制 ----------------
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _arrow_qss(up_img: str, down_img: str) -> str:
    return f"""
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({up_img}); width: 16px; height: 16px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({down_img}); width: 16px; height: 16px;
}}
QComboBox::down-arrow {{
    image: url({down_img}); width: 16px; height: 16px;
}}
"""

_DARK_UP = (_ASSETS_DIR / "spin_arrow_up.png").as_posix()
_DARK_DOWN = (_ASSETS_DIR / "spin_arrow_down.png").as_posix()
_LIGHT_UP = (_ASSETS_DIR / "spin_arrow_up_dark.png").as_posix()
_LIGHT_DOWN = (_ASSETS_DIR / "spin_arrow_down_dark.png").as_posix()

STYLESHEET += _arrow_qss(_DARK_UP, _DARK_DOWN)

# ==================== 浅色主题（白底 · 橙色点缀 · 黑字） ====================
LIGHT_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #ffffff;
    color: #1c2026;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1px solid #d5d9e0;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    background-color: #fafbfc;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #f97316;
}

QLabel { background: transparent; }

QPushButton {
    background-color: #ffffff;
    border: 1px solid #c9ced6;
    border-radius: 6px;
    padding: 6px 14px;
    color: #1c2026;
}
QPushButton:hover { background-color: #fff4e8; border-color: #f97316; }
QPushButton:pressed { background-color: #ffe8d2; }
QPushButton:disabled { color: #a6adb6; background-color: #f0f1f3; border-color: #dde1e6; }
/* 挂菜单的按钮文字自带 ▾，禁用 Qt 额外补画的 menu-indicator 箭头（避免双箭头） */
QPushButton::menu-indicator { width: 0px; image: none; }

QPushButton#OpenButton[connected="true"] {
    background-color: #f97316;
    border-color: #ea6a00;
    color: #ffffff;
}
QPushButton#OpenButton[connected="true"]:hover { background-color: #fb8500; }

QPushButton#SendButton, QPushButton#OpenButton {
    background-color: #f97316;
    border-color: #ea6a00;
    color: #ffffff;
}
QPushButton#SendButton:hover, QPushButton#OpenButton:hover { background-color: #fb8500; }

QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #ffffff;
    border: 1px solid #c9ced6;
    border-radius: 6px;
    padding: 4px 8px;
    color: #1c2026;
    min-height: 18px;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #f97316; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #f97316; }
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: #eef0f3;
    border: 1px solid #c9ced6;
    width: 18px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #ffedd9;
    border-color: #f97316;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
    background-color: #fde0c4;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #c9ced6;
    border-radius: 4px;
    selection-background-color: #ffedd9;
    selection-color: #1c2026;
    color: #1c2026;
    padding: 2px;
}

QCheckBox { background: transparent; spacing: 7px; color: #1c2026; }
QCheckBox:disabled { color: #a6adb6; }

QPlainTextEdit, QTextEdit {
    background-color: #ffffff;
    border: 1px solid #d5d9e0;
    border-radius: 6px;
    color: #1c2026;
    selection-background-color: #ffd9a8;
    padding: 5px;
}
QPlainTextEdit:focus, QTextEdit:focus { border-color: #f97316; }

QStatusBar {
    background-color: #f3f4f6;
    color: #4b5563;
    border-top: 1px solid #e2e6ea;
}

QScrollBar:vertical { background: #f3f4f6; width: 12px; margin: 0; border: none; }
QScrollBar::handle:vertical { background: #c9ced6; border-radius: 6px; min-height: 26px; }
QScrollBar::handle:vertical:hover { background: #b3bac4; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #f3f4f6; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #c9ced6; border-radius: 6px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QToolTip {
    background-color: #ffffff; color: #1c2026;
    border: 1px solid #f97316; padding: 4px;
}
"""
LIGHT_STYLESHEET += _arrow_qss(_LIGHT_UP, _LIGHT_DOWN)

# ==================== 弹出菜单配色（深/浅各自独立） ====================
STYLESHEET += """
QMenu {
    background-color: #282b30;
    color: #e6e9ee;
    border: 1px solid #3a3f47;
    padding: 4px;
}
QMenu::item { padding: 6px 26px 6px 20px; border-radius: 4px; }
QMenu::item:selected { background-color: #2f5da8; color: #ffffff; }
QMenu::item:disabled { color: #5c626b; }
QMenu::separator { height: 1px; background: #3a3f47; margin: 4px 8px; }
"""

LIGHT_STYLESHEET += """
QMenu {
    background-color: #ffffff;
    color: #1c2026;
    border: 1px solid #c9ced6;
    padding: 4px;
}
QMenu::item { padding: 6px 26px 6px 20px; border-radius: 4px; }
QMenu::item:selected { background-color: #ffedd9; color: #1c2026; }
QMenu::item:disabled { color: #a6adb6; }
QMenu::separator { height: 1px; background: #e2e6ea; margin: 4px 8px; }
"""

THEME_STYLESHEETS = {"dark": STYLESHEET, "light": LIGHT_STYLESHEET}


def theme_stylesheet(name: str) -> str:
    return THEME_STYLESHEETS.get(name, STYLESHEET)
