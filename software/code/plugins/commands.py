"""串口指令库：基于 docs/串口指令文档.md 的 FireWater 参数指令（纯数据 + 构造/校验）。

- 指令格式：`关键字:数值;` 成对出现，一行一帧（FireWater 文本模式），只发需要修改的参数；
- 参数表 PARAMS：可下发的 5 个温度控制参数（含单位/范围/默认值/说明）；
- CHANNELS：JustFloat 帧内 8 通道（含单位），可用于通道重命名建议。
"""

PARAMS = {
    "temp_kp": {
        "unit": "A/°C", "min": 0.0, "max": None, "default": 20.0,
        "desc": "温度环比例系数（误差越大输出电流越大）",
    },
    "temp_ki": {
        "unit": "A/(°C·s)", "min": 0.0, "max": None, "default": 0.5,
        "desc": "温度环积分系数（消除稳态误差，过大易震荡）",
    },
    "temp_kd": {
        "unit": "无单位", "min": 0.0, "max": None, "default": 0.5,
        "desc": "电流响应阻尼系数（越大电流响应越钝，抑制温度过冲）",
    },
    "temp_sw": {
        "unit": "0/1", "min": 0.0, "max": 1.0, "default": 1.0,
        "desc": "温度控制开关：1=闭环自动控制，0=开环（电流为 0，温度回落）",
    },
    "temp_tar": {
        "unit": "°C", "min": 0.0, "max": 100.0, "default": 37.0,
        "desc": "目标温度（闭环时温度爬升并稳定到该值）",
    },
}

# JustFloat 帧内通道（按帧内顺序，含单位）
CHANNELS = [
    ("temp_kp", "A/°C"),
    ("temp_ki", "A/(°C·s)"),
    ("temp_kd", "无单位"),
    ("temp_sw", "0/1"),
    ("temp_tar", "°C"),
    ("temp_value", "°C"),
    ("temp_current", "A"),
    ("timestamp", "s"),
]

EXAMPLE = "temp_kp:20;temp_ki:0.5;temp_kd:0.5;temp_sw:1;temp_tar:37;"

# 温度 PID 调节场景的典型操作预设（对应 docs/串口指令文档.md 3.3）
PRESETS = {
    "闭环控制默认": {
        "desc": "开启闭环自动控制，目标 37°C（温度从 25°C 爬升至 37°C 并稳定）",
        "params": {"temp_sw": 1.0, "temp_tar": 37.0},
    },
    "调大比例系数": {
        "desc": "temp_kp 增大到 40 加快响应（保持积分系数）",
        "params": {"temp_kp": 40.0, "temp_ki": 0.5},
    },
    "增大阻尼": {
        "desc": "temp_kd 增大到 2.0 抑制温度过冲",
        "params": {"temp_kd": 2.0},
    },
    "切回开环": {
        "desc": "temp_sw=0 开环：电流归零，温度指数回落",
        "params": {"temp_sw": 0.0},
    },
}


def build_command(params: dict) -> str:
    """按 {关键字: 数值} 构造 FireWater 指令文本（校验参数名与取值范围）。

    Raises:
        ValueError: 未知关键字或数值越界。
    """
    parts = []
    for key, value in params.items():
        if key not in PARAMS:
            raise ValueError(f"未知参数「{key}」，可用参数：{', '.join(sorted(PARAMS))}")
        v = float(value)
        meta = PARAMS[key]
        if meta["min"] is not None and v < meta["min"]:
            raise ValueError(f"{key} 取值 {v:g} 低于下限 {meta['min']:g}")
        if meta["max"] is not None and v > meta["max"]:
            raise ValueError(f"{key} 取值 {v:g} 高于上限 {meta['max']:g}")
        parts.append(f"{key}:{v:g};")
    if not parts:
        raise ValueError("未提供任何参数")
    return "".join(parts)


def parse_command(text: str) -> dict:
    """解析 FireWater 指令文本（如 "temp_sw:1;temp_tar:37;"）为 {关键字: 数值}，
    并校验取值范围（与 build_command 一致）。

    Raises:
        ValueError: 格式非法、含未知关键字或数值越界。
    """
    result = {}
    text = text.strip()
    if not text:
        raise ValueError("指令为空")
    for pair in text.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(f"指令段「{pair}」缺少冒号（应为 关键字:数值;）")
        key, _, raw = pair.partition(":")
        key = key.strip()
        if key not in PARAMS:
            raise ValueError(f"未知参数「{key}」，可用参数：{', '.join(sorted(PARAMS))}")
        try:
            v = float(raw.strip())
        except ValueError:
            raise ValueError(f"参数 {key} 的数值「{raw.strip()}」非法") from None
        meta = PARAMS[key]
        if meta["min"] is not None and v < meta["min"]:
            raise ValueError(f"{key} 取值 {v:g} 低于下限 {meta['min']:g}")
        if meta["max"] is not None and v > meta["max"]:
            raise ValueError(f"{key} 取值 {v:g} 高于上限 {meta['max']:g}")
        result[key] = v
    if not result:
        raise ValueError("指令为空")
    return result


def describe_commands() -> dict:
    """指令说明（供 list_commands 工具使用）。"""
    return {
        "scenario": "温度 PID 调节（temp_kp/temp_ki/temp_kd/temp_sw/temp_tar）",
        "format": "FireWater 文本：关键字:数值; 成对出现，一行一帧，可只发需要修改的参数",
        "example": EXAMPLE,
        "params": PARAMS,
        "presets": {
            name: {"desc": p["desc"], "command": build_command(p["params"])}
            for name, p in PRESETS.items()
        },
        "channels": [{"name": n, "unit": u} for n, u in CHANNELS],
    }
