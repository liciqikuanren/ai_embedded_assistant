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
import struct
import threading
import time
from collections import deque

TAIL = b"\x00\x00\x80\x7f"          # 帧尾（小端 0x7F800000 = +inf）
IMAGE_FRAME_BYTES = 7 * 4           # 图片前导帧固定 28 字节
MAX_BUFFER = 64 * 1024              # 无帧尾时缓冲上限（防爆）
MAX_AI_FRAMES = 500                 # AI 可读取的帧缓冲上限
STATUS_THROTTLE = 0.25              # 状态广播节流（秒）


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
            if not self._enabled or not data:
                return []
            return self._parser.feed(data)

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
