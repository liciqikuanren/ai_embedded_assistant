"""串口指令库测试：FireWater 指令构造/解析/校验（docs/串口指令文档.md）。

运行：python tests/test_commands.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from plugins import commands  # noqa: E402


def test_build_command():
    cmd = commands.build_command({"temp_sw": 1, "temp_tar": 37})
    assert cmd == "temp_sw:1;temp_tar:37;", cmd
    # 可只发需要修改的参数
    assert commands.build_command({"temp_kd": 2.0}) == "temp_kd:2;"
    # 数值格式化
    assert commands.build_command({"temp_kp": 20.0}) == "temp_kp:20;"


def test_build_command_errors():
    try:
        commands.build_command({"unknown_param": 1})
        assert False, "应拒绝未知参数"
    except ValueError as e:
        assert "未知参数" in str(e)
    try:
        commands.build_command({"temp_sw": 2})  # 超出 0/1
        assert False, "应拒绝越界"
    except ValueError as e:
        assert "上限" in str(e) or "下限" in str(e)
    try:
        commands.build_command({"temp_tar": 101})  # 超出 0~100
        assert False, "应拒绝越界"
    except ValueError as e:
        assert "上限" in str(e)
    try:
        commands.build_command({"temp_kp": -1})  # 负值
        assert False, "应拒绝负值"
    except ValueError as e:
        assert "下限" in str(e)
    try:
        commands.build_command({})
        assert False, "空参数应报错"
    except ValueError:
        pass


def test_parse_command():
    d = commands.parse_command("temp_sw:1;temp_tar:37;")
    assert d == {"temp_sw": 1.0, "temp_tar": 37.0}, d
    # 容忍空白/多余分号
    d2 = commands.parse_command(" temp_kp:40 ; temp_ki:0.5; ")
    assert d2 == {"temp_kp": 40.0, "temp_ki": 0.5}, d2


def test_parse_command_errors():
    for bad in ("hello", "temp_sw", "temp_sw=1;", "foo:1;", "temp_tar:abc;"):
        try:
            commands.parse_command(bad)
            assert False, f"应拒绝非法指令：{bad}"
        except ValueError:
            pass
    # 范围校验（与 build_command 一致）：temp_sw 仅 0/1、temp_tar 0~100
    for bad in ("temp_sw:2;", "temp_sw:-1;", "temp_tar:101;", "temp_kp:-0.5;"):
        try:
            commands.parse_command(bad)
            assert False, f"应拒绝越界指令：{bad}"
        except ValueError as e:
            assert "上限" in str(e) or "下限" in str(e), str(e)


def test_presets():
    info = commands.describe_commands()
    presets = info["presets"]
    assert "闭环控制默认" in presets and "切回开环" in presets
    assert presets["切回开环"]["command"] == "temp_sw:0;"
    assert presets["闭环控制默认"]["command"] == "temp_sw:1;temp_tar:37;"


def test_describe():
    info = commands.describe_commands()
    assert set(info["params"]) == {"temp_kp", "temp_ki", "temp_kd", "temp_sw", "temp_tar"}
    assert len(info["channels"]) == 8  # JustFloat 帧内 8 通道
    assert info["channels"][7]["name"] == "timestamp"
    assert "temp_sw:1;temp_tar:37;" in info["example"]
    assert info["scenario"].startswith("温度 PID")


def main() -> int:
    test_build_command()
    print("  ok 指令构造（可只发修改项）")
    test_build_command_errors()
    print("  ok 未知参数/越界/负值校验")
    test_parse_command()
    print("  ok 指令解析")
    test_parse_command_errors()
    print("  ok 非法格式/越界拒绝")
    test_presets()
    print("  ok 场景预设（闭环/比例/阻尼/开环）")
    test_describe()
    print("  ok 指令说明（5 参数 + 8 通道）")
    print("PASS: 串口指令库（FireWater）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
