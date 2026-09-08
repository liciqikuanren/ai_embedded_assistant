"""float_recorder 录制插件：提供 float_recorder 服务（对 justfloat 解析出的浮点通道定时快照录制 CSV）。

录制模式参考 HC_CPB_Pump_Test 工程的 Storage 模块：
  - 定时快照采样：按采样率（默认 1Hz）周期性抓取最新通道值写一行；
  - 表头 Time(s),Ch0,Ch1,...；数值 6 位小数；文件带 BOM（utf-8-sig，Excel 友好）；
  - 停止时关闭文件落盘，录制直接写入目标目录（默认 项目根/csv_floder，文件名 log_YYYYMMDD_HHMMSS.csv）。

扩展（本需求新增）：
  - set_duration(seconds)：设置录制时长（默认 5 分钟，配置 csv_duration_s），到点自动停止；
  - start(channels=None, duration=None)：channels 可预指定表头通道名（缺省 Ch0..ChN-1，N 由首帧决定）；
  - pause()/resume()：中途暂停/继续（暂停不写行，但总时长照走）；
  - stop()：手动结束。

事件：float_recorder_status（状态变化时广播 {state, path, rows, ...}）。
"""

import asyncio
import csv
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PAUSED = "paused"


def default_csv_dir() -> Path:
    """默认录制目录：打包(frozen)后写到用户目录避免 Program Files 不可写；
    源码运行用项目根/csv_floder（与样例 log_20000103_232730.csv 同目录）。"""
    if getattr(sys, "frozen", False):
        return Path.home() / ".ai_embedded_assistant" / "csv_floder"
    return Path(__file__).resolve().parent.parent / "csv_floder"


class FloatRecorder:
    """浮点通道定时快照录制器（参考 Storage 的 bind + 快照循环模式）。"""

    def __init__(self, config, log, loop, dir_path: Path | str,
                 default_duration: float = 300.0, sample_hz: float = 1.0,
                 emit_cb=None, jf=None):
        self._config = config
        self._log = log
        self._loop = loop
        self._dir = Path(dir_path)
        self._duration = float(default_duration)
        self._sample_hz = float(sample_hz)
        self._emit_cb = emit_cb
        self._jf = jf  # justfloat 服务（可选）：提供通道重命名后的名字
        self._lock = threading.RLock()
        self._state = STATE_IDLE
        self._started_at = 0.0
        self._count = 0
        self._rows = 0
        self._skipped = 0
        self._fh = None
        self._writer = None
        self._path: Path | None = None
        self._latest: list[float] | None = None
        self._channel_names: list[str] = []
        self._header_written = False
        self._last_reason = ""

    # ---------------- 状态 ----------------
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def status(self) -> dict:
        with self._lock:
            duration = self._duration
            remaining = 0.0
            if self._state == STATE_RECORDING and duration > 0:
                remaining = max(0.0, duration - (time.monotonic() - self._started_at))
            n = len(self._channel_names) if self._channel_names else (
                len(self._latest) if self._latest is not None else 0
            )
            return {
                "state": self._state,
                "path": str(self._path) if self._path else "",
                "rows": self._rows,
                "skipped": self._skipped,
                "channels": n,
                "duration": duration,
                "remaining": round(remaining, 3),
                "dir": str(self._dir),
                "sample_hz": self._sample_hz,
                "reason": self._last_reason,
            }

    # ---------------- 时长 / 目录 / 采样率 ----------------
    def set_duration(self, seconds: float) -> None:
        seconds = float(seconds)
        if seconds < 0:
            return
        with self._lock:
            self._duration = seconds
        if self._config is not None:
            self._config.set("csv_duration_s", int(seconds))
        self._log(f"录制时长已设置：{seconds:.0f} 秒")

    def set_dir(self, path: str) -> None:
        p = Path(path or "")
        if not p:
            return
        with self._lock:
            self._dir = p
        if self._config is not None:
            self._config.set("csv_dir", str(p))
        self._log(f"录制目录已设置：{p}")

    def set_sample_hz(self, hz: float) -> None:
        hz = float(hz)
        if hz <= 0:
            return
        with self._lock:
            self._sample_hz = hz
        if self._config is not None:
            self._config.set("csv_sample_hz", hz)
        self._log(f"录制采样率已设置：{hz:g} Hz（下次录制生效）")

    # ---------------- 数据入口（justfloat_frame 事件 → 更新最新值） ----------------
    def update_latest(self, channels: list) -> None:
        with self._lock:
            if self._state != STATE_RECORDING:
                return
            if self._header_written and len(channels) != len(self._channel_names):
                self._skipped += 1  # 通道数变化：跳过该帧，保持结构
                return
            if not self._header_written and not self._channel_names:
                # 首帧：若无显式通道名，继承 justfloat 重命名后的名称
                if self._jf is not None:
                    try:
                        names = self._jf.channel_names(len(channels))
                        if len(names) == len(channels):
                            self._channel_names = list(names)
                    except Exception:
                        pass
            self._latest = [float(v) for v in channels]

    # ---------------- 录制控制 ----------------
    def start(self, channels=None, duration=None) -> dict:
        """开始录制（任意线程可调用：UI 主线程 / AI 后台循环，同步返回结果）。

        channels：可选表头通道名列表（缺省 Ch0..ChN-1，N 由首帧决定）；
        duration：录制时长秒（<=0 不限时，None 用默认时长）。
        """
        with self._lock:
            if self._state != STATE_IDLE:
                return {"ok": False, "message": f"正在{self._state}，请先停止"}
            if duration is not None:
                d = float(duration)
                self._duration = d if d > 0 else 0.0
            self._dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._path = self._dir / f"log_{ts}.csv"
            self._fh = open(self._path, "w", newline="", encoding="utf-8-sig")
            self._writer = csv.writer(self._fh)
            self._state = STATE_RECORDING
            self._started_at = time.monotonic()
            self._count = 0
            self._rows = 0
            self._skipped = 0
            self._latest = None
            self._header_written = False
            self._last_reason = ""
            self._channel_names = [str(c) for c in channels] if channels else []
            interval = 1.0 / self._sample_hz if self._sample_hz > 0 else 1.0
            path = str(self._path)
        # 锁外：把快照循环调度到后台 asyncio 循环（避免 loop 线程内等待 future 死锁）
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self._loop:
            self._task = loop.create_task(self._snapshot_loop(interval))
        else:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._launch(interval), self._loop
                )
                fut.result(timeout=3)
            except Exception as e:
                self._log(f"录制启动调度失败：{e}")
                return {"ok": False, "message": f"调度失败：{e}"}
        self._log(f"录制开始：{path}")
        self._schedule_emit()
        return {"ok": True, "path": path}

    async def _launch(self, interval: float) -> None:
        """在后台循环内创建快照任务（供非 loop 线程调度）。"""
        self._task = asyncio.get_running_loop().create_task(
            self._snapshot_loop(interval)
        )

    def pause(self) -> dict:
        with self._lock:
            if self._state != STATE_RECORDING:
                return {"ok": False, "message": "当前未在录制"}
            self._state = STATE_PAUSED
        self._log("录制已暂停")
        self._schedule_emit()
        return {"ok": True}

    def resume(self) -> dict:
        with self._lock:
            if self._state != STATE_PAUSED:
                return {"ok": False, "message": "当前未暂停"}
            self._state = STATE_RECORDING
        self._log("录制已继续")
        self._schedule_emit()
        return {"ok": True}

    def stop(self) -> dict:
        """手动结束录制（同步：置标志 + 关闭文件；快照循环自然退出）。"""
        with self._lock:
            if self._state == STATE_IDLE:
                return {"ok": False, "message": "当前未在录制"}
            self._state = STATE_IDLE
            self._last_reason = "manual"
            self._close_locked()
            path = str(self._path) if self._path else ""
            rows = self._rows
        self._log(f"录制结束：{path or '无文件'}（{rows} 行）")
        self._schedule_emit()
        return {"ok": True, "path": path, "rows": rows}

    async def _snapshot_loop(self, interval: float) -> None:
        """定时快照循环：等首帧写表头，之后按采样间隔写行；到时长自动停止。"""
        auto_stopped = False
        auto_path = ""
        auto_rows = 0
        while True:
            with self._lock:
                if self._state == STATE_IDLE:
                    break
                if self._state == STATE_PAUSED:
                    wait = 0.1
                    sleeping = True
                else:
                    # 时长到点 → 自动停止
                    if self._duration > 0 and (
                        time.monotonic() - self._started_at >= self._duration
                    ):
                        self._state = STATE_IDLE
                        self._last_reason = "timeout"
                        self._close_locked()
                        auto_path = str(self._path) if self._path else ""
                        auto_rows = self._rows
                        auto_stopped = True
                        break
                    # 等首帧确定通道数
                    if not self._header_written:
                        if self._latest is None:
                            wait = 0.05
                            sleeping = True
                        else:
                            self._write_header_locked()
                            wait = None
                            sleeping = False
                    else:
                        wait = None
                        sleeping = False
            if sleeping:
                await asyncio.sleep(wait)
                continue
            # 写一行快照（同步、持锁极短，不 await）
            with self._lock:
                if self._state == STATE_IDLE:
                    break
                t = self._count * interval
                self._write_row_locked(t)
                self._count += 1
                self._rows += 1
            await asyncio.sleep(interval)
        if auto_stopped:
            self._log(f"录制达到时长自动停止：{auto_path or ''}（{auto_rows} 行）")
            await self._emit_status()

    # ---------------- 文件写入 ----------------
    def _write_header_locked(self) -> None:
        if self._writer is None or self._latest is None:
            return
        n = len(self._latest)
        if not self._channel_names:
            self._channel_names = [f"Ch{i}" for i in range(n)]
        try:
            self._writer.writerow(["Time(s)"] + self._channel_names)
            self._header_written = True
        except Exception:
            pass

    def _write_row_locked(self, t: float) -> None:
        if self._writer is None or self._latest is None:
            return
        try:
            row = [f"{t:.6f}"] + [f"{v:.6f}" for v in self._latest]
            self._writer.writerow(row)
        except Exception:
            pass

    def _close_locked(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None
        self._writer = None
        self._task = None

    # ---------------- 事件 ----------------
    def _schedule_emit(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._emit_status(), self._loop)
        except RuntimeError:
            pass

    async def _emit_status(self) -> None:
        if self._emit_cb is not None:
            await self._emit_cb(self.status())

    # ---------------- 文件浏览 ----------------
    def list_files(self) -> list[dict]:
        try:
            files = []
            for p in sorted(self._dir.glob("*.csv")):
                files.append({
                    "name": p.name,
                    "path": str(p),
                    "size": p.stat().st_size,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                })
            return files
        except Exception:
            return []


class Plugin:
    name = "float_recorder"
    inject = ["config", "log", "justfloat"]  # 依赖 justfloat：取重命名后的通道名

    def apply(self, ctx):
        config = ctx.get("config")
        log = ctx.get("log")
        loop = asyncio.get_running_loop()

        dir_path = config.get("csv_dir", "") or default_csv_dir()
        duration = float(config.get("csv_duration_s", 300) or 300)
        sample_hz = float(config.get("csv_sample_hz", 1.0) or 1.0)

        svc = FloatRecorder(
            config, log, loop, dir_path, duration, sample_hz,
            emit_cb=lambda status: ctx.emit("float_recorder_status", status),
            jf=ctx.get("justfloat"),
        )
        ctx.provide("float_recorder", svc)

        async def on_frame(data):
            svc.update_latest(data.get("channels", []))

        ctx.on("justfloat_frame", on_frame)

        def teardown():
            svc.stop()

        ctx.effect(lambda: None, teardown)
        log(f"浮点录制服务已就绪（目录：{dir_path}，默认时长：{duration:.0f}s）")
