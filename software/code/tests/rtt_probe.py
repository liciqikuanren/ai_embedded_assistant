"""RTT 联机探测脚本（独立于 GUI，用于验证 J-Link RTT + shell 收发）。

用法：
    python tests/rtt_probe.py

前提：
    1. 已安装 pylink-square（pip install pylink-square）；
    2. J-Link 已连接目标板，且目标板已烧录带 RTT shell 的固件。

流程：连接 J-Link → 启动 RTT → 读 shell 初始输出 → 发 show 命令 → 收响应 → 断开。
"""

import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import pylink
    from pylink.enums import JLinkInterfaces
    from pylink.library import Library
except ImportError:
    print("pylink 未安装：pip install pylink-square")
    sys.exit(1)

CHIP = "STM32H743XI"
SHELL_CH = 0
PROMPT = "HC_dqj@root:"


def _find_dll() -> str | None:
    from pathlib import Path
    home = Path.home()
    for c in (
        home / ".eide" / "tools" / "jlink" / "JLink_x64.dll",
        home / ".eide" / "tools" / "jlink" / "JLinkARM.dll",
        Path("C:/Program Files/SEGGER/JLink/JLink_x64.dll"),
        Path("C:/Program Files (x86)/SEGGER/JLink/JLink_x64.dll"),
        Path("C:/Program Files/SEGGER/JLink/JLinkARM.dll"),
        Path("C:/Program Files (x86)/SEGGER/JLink/JLinkARM.dll"),
    ):
        if c.is_file():
            return str(c)
    return None


def main() -> int:
    dll = _find_dll()
    jlink = pylink.JLink(lib=Library(dllpath=dll)) if dll else pylink.JLink()
    if dll:
        print(f"[OK] 使用 DLL: {dll}")
    jlink.open()
    jlink.set_tif(JLinkInterfaces.SWD)
    jlink.connect(CHIP, speed=4000)
    print(f"[OK] 已连接 {CHIP} @ SWD 4000kHz")

    jlink.rtt_start()
    print("[OK] RTT 已启动")

    # 轮询等待 RTT 控制块被找到（SEGGER 的搜索是异步的）
    found = False
    n_up = n_dn = 0
    for _ in range(25):  # 最多约 5 秒
        try:
            n_up = jlink.rtt_get_num_up_buffers()
            n_dn = jlink.rtt_get_num_down_buffers()
            found = True
            break
        except Exception:
            time.sleep(0.2)
    if not found:
        print("[FAIL] RTT 控制块未找到 —— 请确认固件已烧录并运行 RTT（shell 已接入）")
        jlink.rtt_stop()
        jlink.close()
        return 1
    print(f"[OK] up 缓冲={n_up} down 缓冲={n_dn}")

    # 读 shell 初始输出（logo + 提示符）
    time.sleep(0.5)
    data = jlink.rtt_read(SHELL_CH, 512)
    if data:
        print("[shell 初始输出]\n" + bytes(data).decode("utf-8", "ignore"))

    # 发送 show 命令（\r 结束）
    cmd = "show\r"
    jlink.rtt_write(SHELL_CH, cmd.encode("utf-8"))
    print("[send] show")

    # 收集响应
    time.sleep(0.8)
    data = jlink.rtt_read(SHELL_CH, 1024)
    if data:
        print("[recv]\n" + bytes(data).decode("utf-8", "ignore"))
    else:
        print("[recv] （无响应，请确认固件已烧录并运行 shell）")

    jlink.rtt_stop()
    jlink.close()
    print("[OK] 已断开")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
