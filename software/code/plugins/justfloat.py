"""justfloat 协议解析插件：提供 justfloat 服务（把串口字节流解析为浮点通道帧）。

JustFloat 协议（小端浮点数组字节流）：
  - 采样数据帧：float fdata[CH_COUNT]（小端） + 帧尾 tail = {0x00, 0x00, 0x80, 0x7f}
  - 图片前导帧：7 个 int32（id/size/width/height/format/0x7F800000/0x7F800000），
    结尾 8 字节恰为连续两个 tail。

通道管理：
  - 解析出的通道默认名为 Ch0..ChN-1，可通过 rename() 重命名（持久化到配置
    justfloat_channel_names），重命名后的名称可作为浮点录制（float_recorder）的表头与数据源。

事件：
  - justfloat_frame        {"channels": [...], "count": N}        采样帧
  - justfloat_image_frame  {"id","size","width","height","format"} 图片前导帧
  - justfloat_status       解析统计（enabled/frames/names/latest/...），状态广播有节流
"""

import asyncio
import re
import struct
import threading
import time
from collections import deque

TAIL = b"\x00\x00\x80\x7f"          # 帧尾（小端 0x7F800000 = +inf）
IMAGE_FRAME_BYTES = 7 * 4           # 图片前导帧固定 28 字节
MAX_BUFFER = 64 * 1024              # 无帧尾时缓冲上限（防爆）
MAX_AI_FRAMES = 500                 # AI 可读取的帧缓冲上限
STATUS_THROTTLE = 0.25              # 状态广播节流（秒）

# 固件 wave_names 回复行：<通道数> <名0> <名1> ...（可混在前导二进制残留之后，用正则定位）
_CHANNEL_LINE_RE = re.compile(
    r"(?:^|[^\d])(\d{1,2})((?:\s+[A-Za-z_][A-Za-z0-9_]*){1,20})\s*$",
    re.MULTILINE,
)


def parse_channel_names(text: str):
    """在任意捕获文本中查找“通道数 + 空格分隔名字”行；找不到返回 None。"""
    m = _CHANNEL_LINE_RE.search(text or "")
    if not m:
        return None
    count = int(m.group(1))
    names = m.group(2).split()
    if count <= 0 or len(names) != count:
        return None
    return count, names


class JustFloatParser:
    """纯解析逻辑：feed 字节流，产出采样帧/图片前导帧（可单测）。"""

    def __init__(self):
        self._buffer = bytearray()
        self.frames = 0
        self.image_frames = 0
        self.dropped_bytes = 0
        self.malformed = 0
        self.ai_frames: deque = deque(maxlen=MAX_AI_FRAMES)
        self.last_frame: list[float] | None = None

    def feed(self, data: bytes) -> list[dict]:
        """喂入一段字节，返回本次解析出的帧列表（dict 描述，供上层广播）。"""
        out: list[dict] = []
        self._buffer.extend(data)
        while True:
            # 1) 图片前导帧：28 字节，结尾 8 字节为连续两个 tail
            if (
                len(self._buffer) >= IMAGE_FRAME_BYTES
                and self._buffer[20:28] == TAIL + TAIL
            ):
                frame = bytes(self._buffer[:28])
                del self._buffer[:28]
                vals = struct.unpack("<7i", frame)
                info = {
                    "kind": "image",
                    "id": vals[0],
                    "size": vals[1],
                    "width": vals[2],
                    "height": vals[3],
                    "format": vals[4],
                }
                self.image_frames += 1
                out.append(info)
                continue
            # 2) 采样数据帧：以 tail 结尾
            idx = self._buffer.find(TAIL)
            if idx < 0:
                break
            frame = bytes(self._buffer[: idx + 4])
            del self._buffer[: idx + 4]
            payload = frame[:-4]
            n = len(payload) // 4
            if n >= 1 and len(payload) % 4 == 0:
                channels = list(struct.unpack(f"<{n}f", payload))
                self.frames += 1
                self.last_frame = channels
                self.ai_frames.append(channels)
                out.append({"kind": "frame", "channels": channels, "count": n})
            else:
                self.malformed += 1
        # 3) 防爆：无帧尾数据超过上限，丢弃最旧部分
        overflow = len(self._buffer) - MAX_BUFFER
        if overflow > 0:
            del self._buffer[:overflow]
            self.dropped_bytes += overflow
        return out

    def stats(self) -> dict:
        return {
            "frames": self.frames,
            "image_frames": self.image_frames,
            "dropped_bytes": self.dropped_bytes,
            "malformed": self.malformed,
            "buffered_frames": len(self.ai_frames),
        }

    def read_frames(self, limit: int = 100, clear: bool = True) -> list[list[float]]:
        limit = max(0, int(limit))
        if clear:
            if limit <= 0:
                return [list(f) for f in self.ai_frames]
            got = [list(self.ai_frames.popleft()) for _ in range(min(limit, len(self.ai_frames)))]
            return got
        if limit > 0:
            return [list(f) for f in list(self.ai_frames)[-limit:]]
        return [list(f) for f in self.ai_frames]

    def reset(self) -> None:
        self._buffer.clear()
        self.ai_frames.clear()
        self.frames = 0
        self.image_frames = 0
        self.dropped_bytes = 0
        self.malformed = 0
        self.last_frame = None


class JustFloatService:
    """线程安全的 justfloat 服务（解析开关 + 统计 + 通道命名 + AI 帧读取）。"""

    def __init__(self, config, log, loop=None, emit_cb=None):
        self._config = config
        self._log = log
        self._loop = loop
        self._emit_cb = emit_cb
        self._lock = threading.RLock()
        self._enabled = True
        self._parser = JustFloatParser()
        self._names: dict[int, str] = {}
        self._query_mode = False        # 通道名查询捕获中（串口：文本行暂存，不喂解析器）
        self._query_buf = bytearray()   # 查询回复文本缓冲
        saved = (config.get("justfloat_channel_names") or {}) if config else {}
        if isinstance(saved, dict):
            for k, v in saved.items():
                try:
                    self._names[int(k)] = str(v)
                except (TypeError, ValueError):
                    pass

    def _schedule_status(self) -> None:
        """状态变化后广播（从任意线程调度到后台循环）。"""
        cb = self._emit_cb
        if cb is None or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(cb(self.stats()), self._loop)
        except RuntimeError:
            pass

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
        self._log(f"justfloat 解析已{'启用' if self._enabled else '停用'}")
        self._schedule_status()

    def feed(self, data: bytes) -> list[dict]:
        with self._lock:
            if not data:
                return []
            if self._query_mode:
                # 通道名查询捕获：文本行不进 JustFloat 解析器，取到换行后由 query_poll 交付
                self._query_buf.extend(bytes(data))
                return []
            if not self._enabled:
                return []
            return self._parser.feed(data)

    # ---------------- 固件通道名查询捕获（wave_names） ----------------
    def query_begin(self) -> None:
        """进入查询捕获模式：后续接收字节先存文本缓冲，不喂解析器。"""
        with self._lock:
            self._query_buf.clear()
            self._query_mode = True

    def query_snapshot(self) -> str:
        """当前已捕获的全部文本（不结束捕获，供正则定位名字行）。"""
        with self._lock:
            return bytes(self._query_buf).decode("utf-8", "replace")

    def query_stop(self) -> None:
        """结束查询捕获：清缓冲并恢复正常解析。"""
        with self._lock:
            self._query_mode = False
            self._query_buf.clear()

    def query_abort(self) -> None:
        self.query_stop()

    def apply_names_from_query(self, tx_send, rtt_svc, mode: str,
                               timeout: float = 2.0) -> dict:
        """查询固件通道名并自动重命名（需固件支持 wave_names）。

        tx_send: 串口文本发送 callable(text)（发送一行 "wave_names"）；mode='serial_vofa' 时用
        rtt_svc: rtt 服务（mode rtt_shell/rtt_vofa 时用 shell 查询）
        解析采用正则在整个回复文本中定位“通道数 + 空格分隔名字”行，
        容忍查询窗口前混入的 JustFloat 二进制残留。
        返回 {"ok", "count", "names"} 或错误。
        """
        parsed: tuple[int, list[str]] | None = None
        last_text = ""
        try:
            if mode in ("rtt_shell", "rtt_vofa"):
                if rtt_svc is None:
                    return {"ok": False, "message": "RTT 服务未就绪"}
                resp = rtt_svc.exec_shell("wave_names", timeout)
                if not (resp or {}).get("ok"):
                    return {"ok": False,
                            "message": (resp or {}).get("message") or "RTT 查询失败（固件支持 wave_names？）"}
                last_text = str((resp or {}).get("output") or "")
                parsed = parse_channel_names(last_text)
            elif mode == "serial_vofa":
                if tx_send is None:
                    return {"ok": False, "message": "串口发送服务未就绪"}
                self.query_begin()
                try:
                    tx_send("wave_names")
                except Exception as e:  # noqa: BLE001
                    self.query_stop()
                    return {"ok": False, "message": f"查询发送失败：{e}"}
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    last_text = self.query_snapshot()
                    parsed = parse_channel_names(last_text)
                    if parsed is not None:
                        break
                    time.sleep(0.05)
                self.query_stop()
                if parsed is None:
                    return {"ok": False,
                            "message": f"查询超时/无法解析通道名：{last_text!r}"}
            else:
                return {"ok": False,
                        "message": f"当前模式 {mode} 不支持波形通道名查询（需 rtt_* 或 serial_vofa）"}
        except Exception as e:  # noqa: BLE001
            self.query_abort()
            return {"ok": False, "message": f"查询失败：{e}"}

        if parsed is None:
            return {"ok": False, "message": f"通道名回复无法解析：{last_text!r}"}
        count, names = parsed

        # 清掉查询窗口可能混入的残留字节，再按固件通道名重命名
        self.reset()
        self.rename({str(i): n for i, n in enumerate(names)})
        self._log(f"已按固件自动命名 {count} 个通道：{' '.join(names)}")
        return {"ok": True, "count": count, "names": names,
                "message": "已按固件自动命名通道"}

    def stats(self) -> dict:
        with self._lock:
            s = self._parser.stats()
            names = self._channel_names_locked(s.get("buffered_frames") > 0
                                               or self._parser.last_frame is not None)
        s["enabled"] = self.enabled
        s["names"] = names
        s["latest"] = self._latest_locked()
        return s

    def _channel_names_locked(self, use_last: bool) -> list[str]:
        """当前通道名列表：优先按最近一帧通道数，其次按已命名数量。"""
        n = len(self._parser.last_frame) if use_last and self._parser.last_frame else 0
        if n <= 0:
            n = max((i for i in self._names), default=-1) + 1
        if n <= 0:
            return []
        return [self._names.get(i, f"Ch{i}") for i in range(n)]

    def _latest_locked(self) -> dict:
        values = list(self._parser.last_frame) if self._parser.last_frame else []
        names = [self._names.get(i, f"Ch{i}") for i in range(len(values))]
        return {"names": names, "values": values, "count": len(values)}

    def channel_names(self, count: int | None = None) -> list[str]:
        """通道名列表（缺省 count 时按最近一帧/已命名数量推断）。"""
        with self._lock:
            if count is None:
                return self._channel_names_locked(True)
            return [self._names.get(i, f"Ch{i}") for i in range(int(count))]

    def latest(self) -> dict:
        """最近一帧：{names, values, count}。"""
        with self._lock:
            return self._latest_locked()

    def rename(self, mapping: dict) -> dict:
        """重命名通道。mapping 支持 {"0": "名"} / {"Ch0": "名"} / {0: "名"} 混合。"""
        with self._lock:
            updated = {}
            for key, value in mapping.items():
                idx = self._parse_index(key)
                if idx < 0:
                    continue
                name = str(value).strip()
                if not name:
                    continue
                self._names[idx] = name
                updated[idx] = name
            if updated and self._config is not None:
                self._config.set(
                    "justfloat_channel_names",
                    {str(k): v for k, v in sorted(self._names.items())},
                )
        if updated:
            self._log(f"通道重命名：{', '.join(f'Ch{k}→{v}' for k, v in updated.items())}")
        self._schedule_status()
        return {"ok": bool(updated), "names": self.channel_names()}

    def reset_names(self) -> dict:
        """清除全部重命名（恢复 Ch0..）。"""
        with self._lock:
            self._names.clear()
            if self._config is not None:
                self._config.set("justfloat_channel_names", {})
        self._log("通道命名已重置")
        self._schedule_status()
        return {"ok": True, "names": self.channel_names()}

    @staticmethod
    def _parse_index(key) -> int:
        s = str(key).strip()
        if s.lower().startswith("ch"):
            s = s[2:]
        try:
            return int(s)
        except ValueError:
            return -1

    def read_frames(self, limit: int = 100, clear: bool = True) -> list[list[float]]:
        with self._lock:
            return self._parser.read_frames(limit, clear)

    def reset(self) -> None:
        with self._lock:
            self._parser.reset()
        self._log("justfloat 解析器已重置")

    def stop(self) -> None:
        with self._lock:
            self._parser.reset()


class Plugin:
    name = "justfloat"
    inject = ["config", "log"]

    def apply(self, ctx):
        config = ctx.get("config")
        log = ctx.get("log")
        loop = asyncio.get_running_loop()

        svc = JustFloatService(
            config, log, loop,
            emit_cb=lambda stats: ctx.emit("justfloat_status", stats),
        )
        svc.set_enabled(bool(config.get("justfloat_enabled", True)))
        ctx.provide("justfloat", svc)

        last_status_ts = [0.0]

        async def on_wave(data):
            frames = svc.feed(bytes(data))
            for f in frames:
                if f["kind"] == "image":
                    await ctx.emit("justfloat_image_frame", f)
                else:
                    await ctx.emit("justfloat_frame", f)
            # 状态广播节流（避免高帧率刷屏 UI）
            now = time.monotonic()
            if now - last_status_ts[0] >= STATUS_THROTTLE:
                last_status_ts[0] = now
                await ctx.emit("justfloat_status", svc.stats())

        # 串口 justfloat（模式2）与 RTT 波形（模式4）共用同一解析入口
        ctx.on("serial_data_received", on_wave)
        ctx.on("rtt_wave", on_wave)

        def teardown():
            svc.stop()

        ctx.effect(lambda: None, teardown)
        log("justfloat 协议解析服务已就绪")
