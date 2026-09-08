"""数据编解码纯函数：ASCII/文本 与 HEX 之间的转换。"""
from __future__ import annotations

import re

_SEP_RE = re.compile(r"[\s,;]+")
_PREFIX_RE = re.compile(r"(?i)0x")

# 换行符类型 → 字节
NEWLINE_BYTES = {"CRLF": b"\r\n", "LF": b"\n", "CR": b"\r"}


def bytes_to_hex(data: bytes, sep: str = " ") -> str:
    """字节流转为空格分隔的大写十六进制字符串。"""
    return sep.join(f"{b:02X}" for b in data)


def bytes_to_text(data: bytes, encoding: str) -> str:
    """字节流按指定编码解码为文本（无法解码的字节用替换符，编码无效回退 UTF-8）。"""
    try:
        return data.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        return data.decode("utf-8", errors="replace")


def text_to_bytes(text: str, encoding: str) -> bytes:
    """文本按指定编码编码为字节流。"""
    return text.encode(encoding, errors="replace")


def hex_to_bytes(text: str) -> bytes:
    """HEX 文本转字节流，支持空格/逗号/分号分隔与 0x 前缀。

    Raises:
        ValueError: 含非法字符或长度为奇数时。
    """
    cleaned = _SEP_RE.sub("", text)
    cleaned = _PREFIX_RE.sub("", cleaned)
    if not cleaned:
        return b""
    if re.search(r"[^0-9a-fA-F]", cleaned):
        raise ValueError("HEX 数据含非法字符（仅支持 0-9、a-f、A-F 与 0x 前缀）")
    if len(cleaned) % 2 != 0:
        raise ValueError("HEX 数据长度必须为偶数（每个字节对应两位十六进制）")
    return bytes.fromhex(cleaned)


def parse_send_payload(text: str, as_hex: bool, encoding: str) -> bytes:
    """按发送格式把输入文本转换为待发送字节流。

    Raises:
        ValueError: HEX 解析失败时。
    """
    if as_hex:
        return hex_to_bytes(text)
    return text_to_bytes(text, encoding)
