"""配置插件：提供 config 服务（带 YAML/JSON 持久化的键值配置存储，无依赖）。

- 默认配置：`~/.ai_embedded_assistant/config.yaml`（YAML）；旧版 `config.json` 自动兼容读取；
- 读取统一用 utf-8-sig（兼容记事本等保存的 BOM），写入为无 BOM UTF-8；
- 支持按需加载/保存：load_from(path) / save_to(path)，yaml 与 json 按扩展名自动识别。
"""

import json
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

DEFAULTS = {
    "port": "",
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "flow": "None",
    # 页面风格：dark=深色（默认）/ light=浅色（白底橙黑）
    "theme": "dark",
    # 接收显示
    "rx_hex": False,
    "rx_encoding": "UTF-8",
    "timestamp": False,
    "auto_wrap": True,
    "auto_scroll": True,
    "rx_show": True,  # 接收区可视开关：显示/隐藏接收数据
    "tx_show": True,  # 接收区可视开关：显示/隐藏发送数据
    # 发送
    "tx_hex": False,
    "tx_encoding": "UTF-8",
    "send_newline": False,
    "newline": "CRLF",
    "cycle_send": False,
    "cycle_interval_ms": 1000,
    # 连接
    "auto_reconnect": False,
    "reconnect_delay": 2.0,
    # AI 接口（MCP）
    "ai_server_enabled": True,
    "ai_server_host": "127.0.0.1",
    "ai_server_port": 8765,
    # AI 联动推送（串口数据 → DSH Agent 会话）
    "ai_push_enabled": False,
    "ai_push_mode": "chat",
    "ai_push_url": "http://127.0.0.1:3080/plugins/ai-embedded-bridge/incoming",
    # AI 回复回写（DSH 桥 → 串口）：默认关闭（RTT 时代改用 shell_exec 工具下发命令）
    "ai_reply_enabled": False,
    "ai_reply_host": "127.0.0.1",
    "ai_reply_port": 8766,
    # TCP 转发（服务端：串口 ↔ TCP 客户端）
    "tcp_host": "127.0.0.1",
    "tcp_port": 9000,
    # 调试模式（已由 mode 四态取代，保留兼容旧配置）
    "debug_mode": False,
    # 运行模式（四态）：serial=串口交互 / serial_vofa=串口+TCP VoFA 转发 / rtt_shell=RTT Shell / rtt_vofa=RTT Shell+波形+TCP VoFA
    "mode": "serial",
    # RTT（探针可切 J-Link / DAPLink）
    "rtt_chip": "STM32H743XI",
    "rtt_serial_no": "",
    "rtt_interface": "SWD",
    "rtt_speed": 4000,
    "rtt_control_block_addr": "",
    "rtt_shell_channel": 0,
    "rtt_wave_channel": 1,
    "rtt_log_channel": 2,
    # 兼容保留：RTT ch1 现按标准 JustFloat（帧尾 {00 00 80 7F}）解析，通道数随帧自动变化，
    # 不再按固定通道数切帧（旧值仅对旧版固件/旧行为兼容）。
    "rtt_wave_channels": 8,
    "rtt_shell_prompt": "HC_dqj@root:",
    # RTT 探针后端：jlink=SEGGER J-Link（pylink）/ dap=DAPLink 或任意 CMSIS-DAP（pyOCD）
    "rtt_probe": "jlink",
    # JLinkARM.dll 显式路径（留空 = 自动搜索 EIDE 自带 / SEGGER 标准安装）
    "rtt_jlink_dll": "",
    # ---- DAPLink / CMSIS-DAP（pyOCD）----
    # pyOCD 目标名（内置/包目标，如 stm32h743xx；留空则用 rtt_chip 小写）
    "rtt_dap_target": "stm32h743xx",
    # 固件 ELF(.axf)：解析 _SEGGER_RTT 符号作为 RTT 控制块地址（推荐；留空则按下面范围扫描）
    "rtt_dap_elf": "",
    # 可选：CMSIS pack 目标包路径（pyOCD 找不到内置目标时用）
    "rtt_dap_pack": "",
    # 控制块扫描范围（仅当未填精确地址、也没给 ELF 时用；留空 = 扫 STM32H7 常用 RAM 区）
    "rtt_cb_search_addr": "",
    "rtt_cb_search_size": "",
    # justfloat 协议解析（调试模式）
    "justfloat_enabled": True,
    "justfloat_channel_names": {},  # 通道重命名：{"0": "PumpRPM", "1": "Flow_L_min", ...}
    # 连接后自动查询固件通道名（wave_names）并命名；false = 始终用配置/默认 Ch0..
    "justfloat_autoname_on_connect": True,
    # 浮点录制（调试模式）：目录（空 → 项目根/csv_floder）、默认时长（秒，默认 5 分钟）、采样率（Hz）
    "csv_dir": "",
    "csv_duration_s": 300,
    "csv_sample_hz": 1.0,
}

YAML_SUFFIXES = (".yaml", ".yml")


class ConfigStore:
    """带 YAML/JSON 持久化的键值配置存储（默认 YAML，兼容旧 JSON，BOM 健壮）。"""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else self._default_path()
        self._data = dict(DEFAULTS)
        self.load()

    @staticmethod
    def _default_path() -> Path:
        return Path.home() / ".ai_embedded_assistant" / "config.yaml"

    @staticmethod
    def _legacy_json_path() -> Path:
        return Path.home() / ".ai_embedded_assistant" / "config.json"

    # ---------------- 加载 / 保存 ----------------
    def load(self, path: Path | str | None = None) -> dict:
        """从指定路径加载（缺省用默认路径；默认路径不存在时回退旧 config.json）。"""
        p = Path(path) if path else self._path
        raw: dict = {}
        if p.exists():
            raw = self._read_file(p)
        elif path is None and self._legacy_json_path().exists():
            raw = self._read_file(self._legacy_json_path())
        if isinstance(raw, dict):
            self._data.update({k: v for k, v in raw.items() if k in DEFAULTS})
            # 旧配置迁移：debug_mode=True 且未显式设置 mode → serial_vofa
            if "mode" not in raw and raw.get("debug_mode"):
                self._data["mode"] = "serial_vofa"
        return dict(self._data)

    def load_from(self, path: Path | str) -> dict:
        """按需加载：从指定配置文件（yaml/json 按扩展名）读取并更新当前配置。"""
        return self.load(path)

    def save(self, path: Path | str | None = None) -> None:
        """保存到指定路径（缺省用默认路径，YAML）。"""
        p = Path(path) if path else self._path
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            text = self._dump_text(p)
            p.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def save_to(self, path: Path | str) -> str:
        """按需保存：把当前全部配置写入指定文件（yaml/json 按扩展名），返回路径。"""
        p = Path(path)
        self.save(p)
        return str(p)

    @staticmethod
    def _read_file(p: Path) -> dict:
        try:
            text = p.read_text(encoding="utf-8-sig")  # 兼容 BOM
        except Exception:
            return {}
        if p.suffix.lower() in YAML_SUFFIXES:
            if yaml is None:
                return {}
            try:
                out = yaml.safe_load(text)
            except Exception:
                return {}
            return out if isinstance(out, dict) else {}
        try:
            out = json.loads(text)
        except Exception:
            return {}
        return out if isinstance(out, dict) else {}

    def _dump_text(self, p: Path) -> str:
        if p.suffix.lower() in YAML_SUFFIXES:
            if yaml is not None:
                return yaml.safe_dump(self._data, allow_unicode=True, sort_keys=False)
            return json.dumps(self._data, ensure_ascii=False, indent=2)
        return json.dumps(self._data, ensure_ascii=False, indent=2)

    # ---------------- 键值访问 ----------------
    @property
    def path(self) -> str:
        return str(self._path)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value) -> None:
        if key in DEFAULTS:
            self._data[key] = value
            self.save()

    def update(self, mapping: dict) -> None:
        self._data.update({k: v for k, v in mapping.items() if k in DEFAULTS})
        self.save()

    def as_dict(self) -> dict:
        return dict(self._data)


class Plugin:
    name = "config"
    inject = []

    def apply(self, ctx):
        ctx.provide("config", ConfigStore())
