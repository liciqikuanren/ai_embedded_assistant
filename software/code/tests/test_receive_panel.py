"""接收面板可视开关测试（offscreen）：显示/隐藏接收、发送数据，计数不受影响。

运行：python tests/test_receive_panel.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.panels.receive import ReceivePanel  # noqa: E402


def text_of(panel: ReceivePanel) -> str:
    return panel.rx_text.toPlainText()


def test_default_shows_both():
    app = QApplication.instance() or QApplication([])
    p = ReceivePanel()
    p.append_rx(b"hello")
    p.append_tx(b"world", "human")
    t = text_of(p)
    assert "← " in t and "hello" in t, t
    assert "→[人] " in t and "world" in t, t
    assert "RX: 5 字节" in p.counter_label.text()
    assert "TX: 5 字节" in p.counter_label.text()


def test_hide_rx():
    app = QApplication.instance() or QApplication([])
    p = ReceivePanel()
    p.append_rx(b"secret")
    p.append_tx(b"visible", "human")
    p.rx_show_check.setChecked(False)  # 关闭显示接收
    t = text_of(p)
    assert "secret" not in t, "接收数据不应显示"
    assert "visible" in t, "发送数据仍应显示"
    assert "RX: 6 字节" in p.counter_label.text(), "计数不受显示开关影响"


def test_hide_tx():
    app = QApplication.instance() or QApplication([])
    p = ReceivePanel()
    p.append_rx(b"keep")
    p.append_tx(b"hide-me", "human")
    p.tx_show_check.setChecked(False)
    t = text_of(p)
    assert "keep" in t
    assert "hide-me" not in t, "发送数据不应显示"
    assert "TX: 7 字节" in p.counter_label.text()


def test_hide_both_and_rerender():
    app = QApplication.instance() or QApplication([])
    p = ReceivePanel()
    p.append_rx(b"a")
    p.append_tx(b"b", "human")
    p.rx_show_check.setChecked(False)
    p.tx_show_check.setChecked(False)
    assert text_of(p) == "", "全部隐藏后接收区应为空"
    # 历史条目仍在（重新打开开关可恢复显示）
    p.rx_show_check.setChecked(True)
    p.tx_show_check.setChecked(True)
    t = text_of(p)
    assert "a" in t and "b" in t, "重新打开开关后历史数据恢复显示"


def test_persist():
    app = QApplication.instance() or QApplication([])
    p = ReceivePanel()
    p.rx_show_check.setChecked(False)
    p.tx_show_check.setChecked(False)
    d = p.settings_dict()
    assert d["rx_show"] is False and d["tx_show"] is False
    p.apply_config({"rx_show": True, "tx_show": False})
    assert p.rx_show_check.isChecked() is True
    assert p.tx_show_check.isChecked() is False


def main() -> int:
    test_default_shows_both()
    print("  ok 默认同时显示接收/发送")
    test_hide_rx()
    print("  ok 关闭显示接收（发送仍显示，计数不变）")
    test_hide_tx()
    print("  ok 关闭显示发送（接收仍显示，计数不变）")
    test_hide_both_and_rerender()
    print("  ok 全隐藏 + 重渲染过滤 + 恢复显示")
    test_persist()
    print("  ok 配置持久化（rx_show/tx_show）")
    print("PASS: 接收面板可视开关")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
