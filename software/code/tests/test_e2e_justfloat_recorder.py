"""端到端测试：loop:// 回环串口发送 JustFloat 帧 → justfloat 解析 → float_recorder 录制 CSV。

运行：python tests/test_e2e_justfloat_recorder.py
"""

import asyncio
import csv
import shutil
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from core.mini_cordis import Cordis  # noqa: E402
from plugins.config import ConfigStore  # noqa: E402
from plugins.float_recorder import Plugin as FloatRecorderPlugin  # noqa: E402
from plugins.justfloat import TAIL  # noqa: E402
from plugins.justfloat import Plugin as JustFloatPlugin  # noqa: E402
from plugins.logger import Plugin as LoggerPlugin  # noqa: E402
from plugins.serial_port import Plugin as SerialPlugin  # noqa: E402


def pack_frame(*values: float) -> bytes:
    return struct.pack(f"<{len(values)}f", *values) + TAIL


async def main() -> int:
    td = HERE / "_tmp_e2e"
    td.mkdir(parents=True, exist_ok=True)
    try:
        cordis = Cordis()
        store = ConfigStore(td / "cfg.json")
        cordis.ctx.provide("config", store)
        cordis.load_all([
            LoggerPlugin(), SerialPlugin(), JustFloatPlugin(), FloatRecorderPlugin(),
        ])
        serial = cordis.ctx.get("serial")
        jf = cordis.ctx.get("justfloat")
        fr = cordis.ctx.get("float_recorder")
        fr.set_dir(str(td))
        fr.set_duration(0)
        fr.set_sample_hz(10.0)

        ok, err = serial.open({
            "port": "loop://", "baudrate": 115200, "bytesize": 8,
            "parity": "N", "stopbits": 1, "flow": "None",
            "rtscts": False, "xonxoff": False,
        })
        assert ok, f"打开 loop:// 失败：{err}"

        assert fr.start(duration=0)["ok"]
        for i in range(5):  # 发送 5 帧 2 通道数据
            serial.write(pack_frame(float(i), float(i) * 2.0))
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.35)  # 等快照采样

        st = fr.status()
        assert st["state"] == "recording", st
        assert jf.stats()["frames"] == 5, jf.stats()
        assert st["rows"] >= 1, st
        assert fr.stop()["ok"]

        rows = list(csv.reader(Path(st["path"]).open(encoding="utf-8-sig")))
        assert rows[0] == ["Time(s)", "Ch0", "Ch1"], rows[0]
        for row in rows[1:]:
            assert len(row) == 3, row
            float(row[0])
            float(row[1])
            float(row[2])
        print(f"  ok 端到端：5 帧解析，录制 {len(rows) - 1} 行 CSV")

        serial.close()
        cordis.unload_all()
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS: 端到端 justfloat 解析 → 浮点录制")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
