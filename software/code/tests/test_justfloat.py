"""justfloat 协议解析测试（无 GUI）：解析器单测 + Cordis 事件链路集成。

运行：python tests/test_justfloat.py
"""

import asyncio
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from core.mini_cordis import Cordis  # noqa: E402
from plugins.config import Plugin as ConfigPlugin  # noqa: E402
from plugins.justfloat import TAIL, JustFloatParser  # noqa: E402
from plugins.justfloat import Plugin as JustFloatPlugin  # noqa: E402
from plugins.logger import Plugin as LoggerPlugin  # noqa: E402


def pack_frame(*values: float) -> bytes:
    return struct.pack(f"<{len(values)}f", *values) + TAIL


def test_parser_basic():
    p = JustFloatParser()
    vals = (1.5, -2.25, 3.0, 100.0)  # 均可用 float32 精确表示
    out = p.feed(pack_frame(*vals))
    assert len(out) == 1, out
    f = out[0]
    assert f["kind"] == "frame"
    assert f["count"] == 4
    assert f["channels"] == list(vals)


def test_doc_example():
    # 协议文档示例：4 通道采样帧 + 帧尾
    data = bytes.fromhex("bf 10 59 3f b1 02 95 3e 57 a6 16 be 7b 4d 7f bf 00 00 80 7f")
    p = JustFloatParser()
    out = p.feed(data)
    assert len(out) == 1, out
    assert out[0]["count"] == 4
    expected = list(struct.unpack("<4f", data[:16]))
    assert out[0]["channels"] == expected


def test_chunked_feed():
    p = JustFloatParser()
    frame = pack_frame(1.0, 2.0, 3.0, 4.0, 5.0)
    got = []
    for i in range(len(frame)):  # 逐字节喂入（极端分包）
        got.extend(p.feed(frame[i:i + 1]))
    assert len(got) == 1, got
    assert got[0]["channels"] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_multi_frame_one_packet():
    p = JustFloatParser()
    out = p.feed(pack_frame(1.0, 2.0) + pack_frame(3.0, 4.0, 5.0))
    assert len(out) == 2, out
    assert out[0]["channels"] == [1.0, 2.0]
    assert out[1]["channels"] == [3.0, 4.0, 5.0]


def test_cross_packet_frame():
    p = JustFloatParser()
    frame = pack_frame(1.5, 2.5, 3.5)
    assert p.feed(frame[:7]) == []
    out = p.feed(frame[7:])
    assert len(out) == 1, out
    assert out[0]["channels"] == [1.5, 2.5, 3.5]


def test_image_frame():
    p = JustFloatParser()
    pre = struct.pack("<7i", 3, 4096, 64, 32, 1, 0x7F800000, 0x7F800000)
    out = p.feed(pre)
    assert len(out) == 1, out
    img = out[0]
    assert img["kind"] == "image"
    assert img["id"] == 3 and img["size"] == 4096
    assert img["width"] == 64 and img["height"] == 32 and img["format"] == 1
    # 图片帧后可继续解析采样帧
    out2 = p.feed(pack_frame(9.0))
    assert len(out2) == 1 and out2[0]["kind"] == "frame"


def test_overflow_drop():
    p = JustFloatParser()
    p.feed(b"\x01" * (64 * 1024 + 100))  # 无帧尾 → 超上限丢弃
    stats = p.stats()
    assert stats["dropped_bytes"] >= 100, stats
    assert stats["frames"] == 0


def test_rename_and_latest():
    import shutil

    from plugins.config import ConfigStore
    from plugins.justfloat import JustFloatService

    td = HERE / "_tmp_jf"
    td.mkdir(exist_ok=True)
    try:
        store = ConfigStore(td / "cfg.json")
        svc = JustFloatService(store, lambda m: None)
        svc.feed(pack_frame(1.0, 2.0, 3.0))
        latest = svc.latest()
        assert latest["names"] == ["Ch0", "Ch1", "Ch2"], latest
        assert latest["values"] == [1.0, 2.0, 3.0], latest
        # 混合格式重命名（索引 / Ch 前缀）
        r = svc.rename({"0": "PumpRPM", "Ch2": "Temp1_C"})
        assert r["ok"], r
        assert svc.latest()["names"] == ["PumpRPM", "Ch1", "Temp1_C"]
        assert svc.channel_names(3) == ["PumpRPM", "Ch1", "Temp1_C"]
        # 持久化到配置
        assert store.get("justfloat_channel_names") == {"0": "PumpRPM", "2": "Temp1_C"}
        # 新服务实例从配置恢复命名
        svc2 = JustFloatService(store, lambda m: None)
        svc2.feed(pack_frame(9.0, 9.0, 9.0))
        assert svc2.latest()["names"] == ["PumpRPM", "Ch1", "Temp1_C"]
        # 重置命名
        svc2.reset_names()
        assert svc2.latest()["names"] == ["Ch0", "Ch1", "Ch2"]
    finally:
        shutil.rmtree(td, ignore_errors=True)


async def test_integration():
    cordis = Cordis()
    cordis.load_all([ConfigPlugin(), LoggerPlugin(), JustFloatPlugin()])
    jf = cordis.ctx.get("justfloat")
    got = []

    async def on_frame(data):
        got.append(data)

    cordis.ctx.on("justfloat_frame", on_frame)
    await cordis.ctx.emit("serial_data_received", pack_frame(6.0, 7.0))
    await asyncio.sleep(0.05)
    assert len(got) == 1, got
    assert got[0]["channels"] == [6.0, 7.0]
    assert jf.stats()["frames"] == 1
    # 停用后不再解析
    jf.set_enabled(False)
    await cordis.ctx.emit("serial_data_received", pack_frame(8.0))
    await asyncio.sleep(0.05)
    assert jf.stats()["frames"] == 1
    cordis.unload_all()


def main() -> int:
    test_parser_basic()
    print("  ok 基本帧解析")
    test_doc_example()
    print("  ok 文档示例字节")
    test_chunked_feed()
    print("  ok 逐字节分包")
    test_multi_frame_one_packet()
    print("  ok 一包多帧")
    test_cross_packet_frame()
    print("  ok 跨包帧")
    test_image_frame()
    print("  ok 图片前导帧")
    test_overflow_drop()
    print("  ok 无帧尾防爆")
    test_rename_and_latest()
    print("  ok 通道重命名 / 最新值 / 持久化恢复")
    asyncio.run(test_integration())
    print("  ok Cordis 集成（serial_data_received → justfloat_frame + 停用）")
    print("PASS: justfloat 协议解析")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
