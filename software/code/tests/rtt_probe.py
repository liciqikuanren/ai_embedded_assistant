"""RTT 联机探测脚本（独立于 GUI，验证 J-Link 或 DAPLink 的 RTT + shell 收发）。

用法：
    # J-Link（默认）
    python tests/rtt_probe.py
    python tests/rtt_probe.py --probe jlink --chip STM32H743XI

    # DAPLink / CMSIS-DAP（pyOCD）
    python tests/rtt_probe.py --probe dap --elf build/H7_Template/H7_Template.axf
    python tests/rtt_probe.py --probe dap --addr 0x24006ef8

前提：
    1. jlink 模式：pip install pylink-square，且 J-Link 已连目标板；
       dap 模式：pip install pyocd，且 DAPLink/CMSIS-DAP 已连目标板（Windows 需 WinUSB 驱动）；
    2. 目标板已烧录带 RTT shell 的固件。

流程：连接探针 → 建立 RTT → 读三通道初始输出 → 发命令 → 收响应 → 断开。
脚本直接复用 plugins/rtt.py 的探针实现，等价于 GUI 的“连接 RTT”。
"""

import argparse
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.rtt import _DapProbe, _JLinkProbe, _strip_ansi  # noqa: E402

PROMPT = "HC_dqj@root:"


def _make_config(args) -> dict:
    return {
        "rtt_chip": args.chip,
        "rtt_serial_no": args.serial,
        "rtt_interface": args.iface,
        "rtt_speed": args.speed,
        "rtt_control_block_addr": args.addr,
        "rtt_jlink_dll": args.dll,
        "rtt_dap_target": args.target,
        "rtt_dap_elf": args.elf,
        "rtt_cb_search_addr": args.search_addr,
        "rtt_cb_search_size": args.search_size,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="RTT 联机探测（J-Link / DAPLink）")
    p.add_argument("--probe", choices=("jlink", "dap"), default="jlink", help="探针类型")
    p.add_argument("--chip", default="STM32H743XI", help="J-Link 芯片型号")
    p.add_argument("--target", default="stm32h743xx", help="pyOCD 目标名（dap）")
    p.add_argument("--iface", default="SWD", help="接口：SWD / JTAG")
    p.add_argument("--speed", default="4000", help="接口速度 kHz")
    p.add_argument("--serial", default="", help="序列号 / DAPLink UID（留空=自动）")
    p.add_argument("--dll", default="", help="JLink_x64.dll 路径（留空=自动搜索）")
    p.add_argument("--elf", default="", help="固件 .axf/.elf（dap：解析 _SEGGER_RTT）")
    p.add_argument("--addr", default="", help="RTT 控制块精确地址（如 0x24006ef8）")
    p.add_argument("--search-addr", dest="search_addr", default="", help="控制块扫描起始地址")
    p.add_argument("--search-size", dest="search_size", default="", help="控制块扫描长度")
    p.add_argument("--cmd", default="show", help="连接后发送的 shell 命令")
    args = p.parse_args()

    logs: list[str] = []
    probe = _DapProbe(_make_config(args), logs.append) if args.probe == "dap" else _JLinkProbe(_make_config(args), logs.append)

    print(f"[1] 打开探针：{probe.label}")
    try:
        probe.open()
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 连接失败：{e}")
        return 1
    for m in logs:
        print(f"    {m}")
    print(f"[OK] 已连接：{probe.label} {probe.detail}")

    try:
        time.sleep(0.5)
        for ch, name in ((0, "shell"), (1, "wave"), (2, "log")):
            data = probe.read(ch, 4096)
            print(f"[ch{ch} {name}] {len(data)}B: {data[:160]!r}")

        print(f"[2] 发送命令：{args.cmd!r}")
        probe.write(0, (args.cmd + "\r").encode("utf-8"))

        deadline = time.time() + 2.0
        out = b""
        while time.time() < deadline:
            d = probe.read(0, 4096)
            if d:
                out += d
                if PROMPT.encode() in out:
                    break
            time.sleep(0.05)
        print("[recv]\n" + _strip_ansi(out.decode("utf-8", "ignore")))
    finally:
        probe.close()
        print("[OK] 已断开")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
