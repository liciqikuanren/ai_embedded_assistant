"""配置存储测试：YAML/JSON 读写、BOM 兼容、按需加载/保存、旧 JSON 回退。

运行：python tests/test_config.py
"""

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from plugins.config import ConfigStore  # noqa: E402


def tmp(name: str) -> Path:
    d = HERE / f"_tmp_cfg_{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_yaml_roundtrip():
    td = tmp("yaml")
    try:
        p = td / "cfg.yaml"
        s1 = ConfigStore(p)
        s1.set("baudrate", 9600)
        s1.set("debug_mode", True)
        s1.set("justfloat_channel_names", {"0": "PumpRPM"})
        assert p.exists(), "应写出 YAML 文件"
        s2 = ConfigStore(p)
        assert s2.get("baudrate") == 9600
        assert s2.get("debug_mode") is True
        assert s2.get("justfloat_channel_names") == {"0": "PumpRPM"}
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_json_compat():
    td = tmp("json")
    try:
        p = td / "cfg.json"
        p.write_text(json.dumps({"baudrate": 19200, "debug_mode": True}), encoding="utf-8")
        s = ConfigStore(p)
        assert s.get("baudrate") == 19200
        assert s.get("debug_mode") is True
        # 未知键被过滤
        p.write_text(json.dumps({"baudrate": 2400, "unknown_key": 1}), encoding="utf-8")
        s2 = ConfigStore(p)
        assert s2.get("baudrate") == 2400
        assert "unknown_key" not in s2.as_dict()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_bom_tolerant():
    td = tmp("bom")
    try:
        # 带 BOM 的 JSON（Windows 记事本保存风格）—— 此前会导致 json.loads 失败
        pj = td / "cfg.json"
        pj.write_bytes(b"\xef\xbb\xbf" + json.dumps({"baudrate": 57600}).encode("utf-8"))
        s = ConfigStore(pj)
        assert s.get("baudrate") == 57600, s.get("baudrate")
        # 带 BOM 的 YAML
        py = td / "cfg.yaml"
        py.write_bytes(b"\xef\xbb\xbf" + b"baudrate: 115200\n")
        s2 = ConfigStore(py)
        assert s2.get("baudrate") == 115200
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_load_from_save_to():
    td = tmp("loadsave")
    try:
        s = ConfigStore(td / "default.yaml")
        s.set("port", "COM1")
        # 按需保存到 json
        jp = td / "export.json"
        s.save_to(jp)
        assert jp.exists()
        # 按需从 json 加载
        s2 = ConfigStore(td / "other.yaml")
        s2.load_from(jp)
        assert s2.get("port") == "COM1"
        # 保存到 yaml 再加载
        yp = td / "export.yaml"
        s2.save_to(yp)
        assert yp.exists()
        s3 = ConfigStore(yp)
        assert s3.get("port") == "COM1"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_default_path_fallback():
    """默认路径 ~/.ai_embedded_assistant：YAML 优先；缺 YAML 时回退旧 config.json。"""
    td = tmp("home")
    old = os.environ.get("USERPROFILE")
    os.environ["USERPROFILE"] = str(td)
    try:
        cfg_dir = Path.home() / ".ai_embedded_assistant"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        # 只有旧 json → 回退读取
        (cfg_dir / "config.json").write_text(
            json.dumps({"baudrate": 38400, "debug_mode": True}), encoding="utf-8"
        )
        s = ConfigStore()
        assert s.get("baudrate") == 38400
        assert s.get("debug_mode") is True
        # 有 yaml → yaml 优先
        (cfg_dir / "config.yaml").write_text("baudrate: 76800\n", encoding="utf-8")
        s2 = ConfigStore()
        assert s2.get("baudrate") == 76800
    finally:
        if old is not None:
            os.environ["USERPROFILE"] = old
        shutil.rmtree(td, ignore_errors=True)


def main() -> int:
    test_yaml_roundtrip()
    print("  ok YAML 读写往返")
    test_json_compat()
    print("  ok JSON 兼容 + 未知键过滤")
    test_bom_tolerant()
    print("  ok BOM 兼容（json/yaml）")
    test_load_from_save_to()
    print("  ok 按需加载/保存（load_from/save_to）")
    test_default_path_fallback()
    print("  ok 默认路径 yaml 优先 + 旧 json 回退")
    print("PASS: 配置存储（YAML）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
