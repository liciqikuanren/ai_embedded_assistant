"""浮点录制插件测试（无 GUI）：CSV 格式、暂停/恢复、通道变化、时长自动停止。

运行：python tests/test_float_recorder.py
"""

import asyncio
import csv
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from core.mini_cordis import Cordis  # noqa: E402
from plugins.config import ConfigStore  # noqa: E402
from plugins.float_recorder import Plugin as FloatRecorderPlugin  # noqa: E402
from plugins.justfloat import Plugin as JustFloatPlugin  # noqa: E402
from plugins.logger import Plugin as LoggerPlugin  # noqa: E402


def frame(*values) -> dict:
    return {"channels": list(values), "count": len(values)}


def tmp_workspace(name: str) -> Path:
    """工作区内的临时目录（沙箱下系统 Temp 不可写）。"""
    d = HERE / f"_tmp_{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup(d: Path) -> None:
    shutil.rmtree(d, ignore_errors=True)


async def setup(tmp_dir: Path, duration: float = 300.0, hz: float = 1.0):
    """用临时目录构造 Cordis + 录制插件（config 指向临时文件，不污染真实配置）。"""
    cordis = Cordis()
    store = ConfigStore(tmp_dir / "cfg.json")
    cordis.ctx.provide("config", store)
    cordis.load_all([
        LoggerPlugin(), JustFloatPlugin(), FloatRecorderPlugin(),
    ])
    fr = cordis.ctx.get("float_recorder")
    fr.set_dir(str(tmp_dir))
    fr.set_duration(duration)
    fr.set_sample_hz(hz)
    return cordis, fr


async def emit_frame(cordis, *values) -> None:
    await cordis.ctx.emit("justfloat_frame", frame(*values))


async def wait_until(pred, timeout: float = 3.0) -> bool:
    step = 0.02
    waited = 0.0
    while waited < timeout:
        if pred():
            return True
        await asyncio.sleep(step)
        waited += step
    return pred()


async def test_csv_format():
    td = tmp_workspace("csv")
    try:
        cordis, fr = await setup(td, duration=0, hz=10.0)  # 不限时 10Hz
        r = fr.start(duration=0)
        assert r["ok"], r
        await emit_frame(cordis, 1.5, 2.5)
        assert await wait_until(lambda: fr.status()["rows"] >= 2), fr.status()
        st = fr.status()
        assert st["state"] == "recording", st
        assert fr.stop()["ok"]
        path = Path(st["path"])
        assert path.exists()
        rows = list(csv.reader(path.open(encoding="utf-8-sig")))
        assert rows[0] == ["Time(s)", "Ch0", "Ch1"], rows[0]
        for row in rows[1:]:
            assert len(row) == 3, row
            float(row[0])
            float(row[1])
            float(row[2])
            assert len(row[1].split(".")[1]) == 6  # 值 6 位小数
        assert float(rows[1][0]) == 0.0  # 首行 t=0
        cordis.unload_all()
    finally:
        cleanup(td)


async def test_pause_resume_stop():
    td = tmp_workspace("pause")
    try:
        cordis, fr = await setup(td, duration=0, hz=10.0)
        fr.start(duration=0)
        await emit_frame(cordis, 1.0, 2.0)
        await asyncio.sleep(0.25)
        rows_at_pause = fr.status()["rows"]
        assert rows_at_pause >= 2, fr.status()
        assert fr.pause()["ok"]
        await asyncio.sleep(0.25)
        assert fr.status()["rows"] == rows_at_pause  # 暂停不写行
        assert fr.status()["state"] == "paused"
        assert fr.resume()["ok"]
        await asyncio.sleep(0.3)
        assert fr.status()["rows"] > rows_at_pause  # 恢复续写
        r = fr.stop()
        assert r["ok"] and r["rows"] > rows_at_pause
        cordis.unload_all()
    finally:
        cleanup(td)


async def test_channel_change_skipped():
    td = tmp_workspace("ch")
    try:
        cordis, fr = await setup(td, duration=0, hz=10.0)
        fr.start(duration=0)
        await emit_frame(cordis, 1.0, 2.0, 3.0)
        await asyncio.sleep(0.15)
        await emit_frame(cordis, 9.0)  # 通道数变化 → 跳过该帧
        await asyncio.sleep(0.15)
        st = fr.status()
        assert st["skipped"] >= 1, st
        fr.stop()
        rows = list(csv.reader(Path(st["path"]).open(encoding="utf-8-sig")))
        for row in rows[1:]:
            assert len(row) == 4  # 保持 3 通道结构
        cordis.unload_all()
    finally:
        cleanup(td)


async def test_duration_auto_stop():
    td = tmp_workspace("dur")
    try:
        cordis, fr = await setup(td, duration=300, hz=20.0)
        assert fr.start(duration=0.3)["ok"]  # 0.3 秒后自动停止
        await emit_frame(cordis, 1.0, 2.0)
        assert await wait_until(lambda: fr.status()["state"] == "idle"), fr.status()
        st = fr.status()
        assert st["reason"] == "timeout", st
        assert st["rows"] >= 1, st
        assert Path(st["path"]).exists()
        # 结束后可再次开始
        assert fr.start(duration=0)["ok"]
        fr.stop()
        cordis.unload_all()
    finally:
        cleanup(td)


async def test_inherit_renamed_channels():
    """录制表头继承 justfloat 重命名后的通道名。"""
    from plugins.justfloat import Plugin as JustFloatPlugin

    td = tmp_workspace("rename")
    try:
        cordis = Cordis()
        store = ConfigStore(td / "cfg.json")
        cordis.ctx.provide("config", store)
        cordis.load_all([
            LoggerPlugin(), JustFloatPlugin(), FloatRecorderPlugin(),
        ])
        jf = cordis.ctx.get("justfloat")
        fr = cordis.ctx.get("float_recorder")
        assert fr._jf is not None, "float_recorder 应持有 justfloat 引用"
        fr.set_dir(str(td))
        fr.set_duration(0)
        fr.set_sample_hz(10.0)
        jf.rename({"0": "PumpRPM", "1": "Flow_L_min"})
        fr.start(duration=0)
        await emit_frame(cordis, 1.5, 2.5)
        await asyncio.sleep(0.25)
        st = fr.status()
        assert st["channels"] == 2, st
        fr.stop()
        rows = list(csv.reader(Path(st["path"]).open(encoding="utf-8-sig")))
        assert rows[0] == ["Time(s)", "PumpRPM", "Flow_L_min"], rows[0]
        assert float(rows[1][1]) == 1.5  # 通道数据作为录制数据源
        cordis.unload_all()
    finally:
        cleanup(td)


async def main() -> int:
    await test_csv_format()
    print("  ok CSV 格式（表头 Time(s),Ch0.. / 6 位小数 / 首行 t=0）")
    await test_pause_resume_stop()
    print("  ok 暂停不写行 / 恢复续写 / 停止落盘")
    await test_channel_change_skipped()
    print("  ok 通道数变化跳过")
    await test_duration_auto_stop()
    print("  ok 时长到点自动停止（reason=timeout）")
    await test_inherit_renamed_channels()
    print("  ok 录制继承 justfloat 重命名通道名")
    print("PASS: 浮点录制插件")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
