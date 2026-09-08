"""MVC 控制器：视图信号 → 服务调用；后台事件 → 视图更新；设置持久化。"""

import asyncio
import csv
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog

from core import theme as theme_mod
from core.codec import bytes_to_hex, bytes_to_text


class SerialController(QObject):
    def __init__(self, ctx, window, bridge):
        super().__init__()
        self._ctx = ctx
        self._w = window
        self._b = bridge
        # 当前生效的配置文件（按需加载/保存后跟随）；当前运行模式（检测配置热切换）
        self._cfg_file: str | None = None
        self._active_mode: str | None = None

        s = window.settings
        r = window.receive
        t = window.send
        tc = window.tcp
        jf = window.justfloat
        fr = window.recorder
        rt = window.rtt

        w = window
        # 视图 → 控制器
        s.open_clicked.connect(self._on_open)
        s.refresh_clicked.connect(self._refresh_ports)
        s.auto_reconnect_toggled.connect(self._on_auto_reconnect)
        s.params_changed.connect(self._persist_settings)
        w.load_config_clicked.connect(self._on_load_config)
        w.save_config_clicked.connect(self._on_save_config)
        w.mode_selected.connect(self._on_mode_changed)
        w.theme_changed.connect(self._on_theme_changed)
        r.clear_clicked.connect(self._on_clear)
        r.save_clicked.connect(self._on_save)
        r.load_clicked.connect(self._on_load)
        r.settings_changed.connect(self._persist_settings)
        t.send_clicked.connect(self._on_send)
        t.cycle_toggled.connect(self._on_cycle_toggled)
        t.cycle_interval_changed.connect(self._on_cycle_interval_changed)
        t.payload_changed.connect(self._on_payload_changed)
        t.settings_changed.connect(self._persist_settings)
        tc.toggled.connect(self._on_tcp_toggled)
        tc.settings_changed.connect(self._persist_settings)
        jf.toggled.connect(self._on_justfloat_toggled)
        jf.reset_clicked.connect(self._on_justfloat_reset)
        jf.renamed.connect(self._on_justfloat_renamed)
        fr.start_clicked.connect(self._on_recorder_start)
        fr.pause_clicked.connect(self._on_recorder_pause)
        fr.resume_clicked.connect(self._on_recorder_resume)
        fr.stop_clicked.connect(self._on_recorder_stop)
        fr.refresh_clicked.connect(self._refresh_recorder_files)
        fr.settings_changed.connect(self._persist_settings)

        # 后台事件 → 视图
        bridge.rx.connect(r.append_rx)
        bridge.tx.connect(r.append_tx)
        bridge.state.connect(self._on_state)
        bridge.ready.connect(self._on_ready)
        bridge.log.connect(self._on_log)
        bridge.ai_ready.connect(self._on_ai_ready)
        bridge.tcp_status.connect(self._on_tcp_status)
        bridge.justfloat_status.connect(self._on_justfloat_status)
        bridge.float_recorder_status.connect(self._on_recorder_status)
        bridge.mode_changed.connect(self._on_mode_changed_event)
        rt.connect_clicked.connect(self._on_rtt_connect)
        rt.send_shell.connect(self._on_rtt_send)
        rt.settings_changed.connect(self._persist_settings)
        bridge.rtt_shell_rx.connect(rt.append_shell)
        bridge.rtt_log.connect(rt.append_log)
        bridge.rtt_status.connect(rt.set_status)
        bridge.rtt_shell_tx.connect(rt.append_command)

        # 录制剩余时间轮询（每 500ms 刷新状态标签）
        self._recorder_timer = QTimer(self)
        self._recorder_timer.setInterval(500)
        self._recorder_timer.timeout.connect(self._poll_recorder)

    # ---------------- 服务获取 ----------------
    def _svc(self, name):
        return self._ctx.get(name, strict=True)

    # ---------------- 就绪 ----------------
    def _on_ready(self):
        cfg = self._svc("config")
        if cfg is not None:
            self._active_mode = cfg.get("mode", "serial")
            self._apply_config_to_ui(cfg)
        self._refresh_ports()
        self._w.settings.set_enabled(True)
        self._w.set_state("就绪")

    def _apply_config_to_ui(self, cfg) -> None:
        """把配置应用到所有面板 + 调试面板显隐（加载配置后复用）。"""
        self._w.settings.apply_config(cfg.as_dict())
        self._w.receive.apply_config(cfg.as_dict())
        self._w.send.apply_config(cfg.as_dict())
        self._w.tcp.apply_config(cfg.as_dict())
        self._w.justfloat.apply_config(cfg.as_dict())
        self._w.recorder.apply_config(cfg.as_dict())
        self._w.rtt.apply_config(cfg.as_dict())
        self._w.set_config_path(cfg.path)
        mode = cfg.get("mode", "serial")
        self._w.set_mode(mode)
        # 页面风格：与当前激活主题不同才整体应用（避免就绪时重复 setStyleSheet）
        theme_name = cfg.get("theme", "dark")
        if theme_name != theme_mod.active_theme():
            self._apply_theme(theme_name)
        else:
            self._w.set_theme(theme_name)
        # AI 联动：串口+VoFA 下 justfloat 数据流不注入 DSH（走录制 CSV），
        # 其余模式（serial / rtt_shell / rtt_vofa）允许推送（串口接收 / RTT shell 输出）。
        if mode == "serial_vofa":
            self._w.settings.set_ai_push_enabled(
                False, "串口+VoFA 模式下已禁用：串口 justfloat 数据只通过录制 CSV 查看，不直接注入 DSH 上下文")
        else:
            self._w.settings.set_ai_push_enabled(True)
        if mode in ("serial_vofa", "rtt_vofa"):
            # 波形模式：录制剩余时间由轮询定时器刷新（两种 VoFA 模式都要跑）
            self._refresh_recorder_files()
            self._poll_recorder()
            self._recorder_timer.start()
        else:
            self._recorder_timer.stop()

    # ---------------- 页面风格（主题） ----------------
    def _apply_theme(self, name: str) -> None:
        """应用主题：切换渲染调色板 + 全局 QSS + 重绘富文本面板历史。"""
        name = name if name in ("dark", "light") else "dark"
        theme_mod.set_active(name)
        QApplication.instance().setStyleSheet(theme_mod.theme_stylesheet(name))
        self._w.set_theme(name)
        self._w.receive.refresh_display()
        self._w.rtt.refresh_display()

    def _on_theme_changed(self, name: str):
        cfg = self._svc("config")
        if cfg is not None:
            cfg.update({"theme": name})
        self._apply_theme(name)
        self._w.show_message(
            f"页面风格已切换：{'深色' if name == 'dark' else '浅色（白底橙黑）'}"
        )

    # ---------------- 配置文件（YAML 按需加载/保存） ----------------
    def _on_load_config(self):
        cfg = self._svc("config")
        if cfg is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self._w, "加载配置", cfg.path, "配置文件 (*.yaml *.yml *.json);;所有文件 (*)")
        if not path:
            return
        try:
            cfg.load_from(path)
        except Exception as e:
            self._w.show_error(f"加载配置失败：{e}")
            return
        # 界面反馈：配置文件框显示本次加载的文件，后续「保存配置」默认写到该文件
        self._cfg_file = path
        self._w.set_config_path(path)
        self._apply_config_to_ui(cfg)
        # 若加载的运行模式与当前生效模式不同 → 热切换后台插件（面板显隐由 set_mode 处理）
        loaded_mode = cfg.get("mode", "serial")
        if self._active_mode is not None and loaded_mode != self._active_mode:
            self._on_mode_changed(loaded_mode)
        self._refresh_ports()
        self._w.show_message(f"已加载配置 {path}")

    def _on_save_config(self):
        cfg = self._svc("config")
        if cfg is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._w, "保存配置", self._cfg_file or cfg.path,
            "YAML 文件 (*.yaml);;JSON 文件 (*.json)")
        if not path:
            return
        try:
            cfg.save_to(path)
        except Exception as e:
            self._w.show_error(f"保存配置失败：{e}")
            return
        self._cfg_file = path
        self._w.set_config_path(path)
        self._w.show_message(f"已保存配置 {path}")

    def _on_mode_changed(self, mode: str):
        """四态模式切换 → 立即动态加载/卸载插件并刷新面板（无需重启）。"""
        self._active_mode = mode
        cfg = self._svc("config")
        if cfg is not None:
            cfg.update({"mode": mode})
        self._w.set_mode(mode)
        self._w.show_message("正在切换运行模式...")
        cordis, loop = self._backend()
        if cordis is None or loop is None:
            self._w.show_error("后台循环未就绪，无法热切换（请重启程序）")
            return
        asyncio.run_coroutine_threadsafe(self._switch_mode(cordis, mode), loop)

    def _backend(self):
        """返回 (cordis 宿主, 后台事件循环)。"""
        return (
            getattr(self._ctx, "host", None),
            getattr(self._ctx, "backend_loop", None),
        )

    async def _switch_mode(self, cordis, mode: str) -> None:
        """后台执行：按四态模式加载/卸载插件，重启 ai_server 刷新工具，广播事件。"""
        from plugins.ai_server import Plugin as AiServerPlugin
        from plugins.float_recorder import Plugin as FloatRecorderPlugin
        from plugins.justfloat import Plugin as JustFloatPlugin
        from plugins.rtt import Plugin as RttPlugin
        from plugins.tcp_forward import Plugin as TcpForwardPlugin

        want_wave = mode in ("serial_vofa", "rtt_vofa")
        want_rtt = mode in ("rtt_shell", "rtt_vofa")

        # 卸载不需要的插件
        for name in ("float_recorder", "justfloat", "tcp_forward"):
            if not want_wave and cordis.ctx.get(name, strict=False) is not None:
                await cordis.unload_plugin_async(name)
        if not want_rtt and cordis.ctx.get("rtt", strict=False) is not None:
            await cordis.unload_plugin_async("rtt")

        # 加载需要的插件
        if want_rtt and cordis.ctx.get("rtt", strict=False) is None:
            await cordis.load_plugin_async(RttPlugin())
        if want_wave:
            if cordis.ctx.get("tcp_forward", strict=False) is None:
                await cordis.load_plugin_async(TcpForwardPlugin())
            if cordis.ctx.get("justfloat", strict=False) is None:
                await cordis.load_plugin_async(JustFloatPlugin())
            if cordis.ctx.get("float_recorder", strict=False) is None:
                await cordis.load_plugin_async(FloatRecorderPlugin())

        # 重启 AI 接口：按新模式的可用服务重新注册 MCP 工具（等旧端口释放）
        if cordis.ctx.get("ai_server", strict=False) is not None:
            await cordis.unload_plugin_async("ai_server")
            await asyncio.sleep(0.5)
        await cordis.load_plugin_async(AiServerPlugin())
        await self._ctx.emit("mode_changed", {"mode": mode})

    def _on_mode_changed_event(self, data):
        """后台切换完成 → 主线程刷新面板显隐与参数。"""
        data = data or {}
        mode = data.get("mode", "serial")
        self._active_mode = mode
        self._w.set_mode(mode)
        cfg = self._svc("config")
        if cfg is not None:
            self._w.set_config_path(cfg.path)
            self._w.tcp.apply_config(cfg.as_dict())
            self._w.justfloat.apply_config(cfg.as_dict())
            self._w.recorder.apply_config(cfg.as_dict())
            self._w.rtt.apply_config(cfg.as_dict())
            if mode == "serial_vofa":
                self._w.settings.set_ai_push_enabled(
                    False, "串口+VoFA 模式下已禁用：串口 justfloat 数据只通过录制 CSV 查看，不直接注入 DSH 上下文")
            else:
                self._w.settings.set_ai_push_enabled(True)
            if mode in ("serial_vofa", "rtt_vofa"):
                self._refresh_recorder_files()
                self._poll_recorder()
                self._recorder_timer.start()
            else:
                self._recorder_timer.stop()
        self._w.show_message(f"运行模式已切换：{mode}")

    def _refresh_ports(self):
        serial = self._svc("serial")
        if serial is None:
            return
        self._w.settings.set_ports(serial.list_ports())

    # ---------------- 连接控制 ----------------
    def _on_open(self):
        serial = self._svc("serial")
        if serial is None:
            return
        if serial.is_open:
            serial.close()
            self._w.settings.set_connected(False)
            self._w.set_state("已断开")
            return
        params = self._w.settings.serial_params()
        if not params.get("port"):
            self._w.show_error("请先选择端口号")
            return
        cfg = self._svc("config")
        if cfg is not None:
            cfg.update(params)
        serial.set_auto_reconnect(self._w.settings.reconnect_enabled(), params)
        ok, err = serial.open(params)
        if not ok:
            self._w.show_error(f"打开串口失败：{self._friendly_open_error(err)}")
            return
        # 同步更新 UI（serial_opened 事件也会异步更新，双保险）
        self._w.settings.set_connected(True)
        self._w.set_state(f"已连接 {params['port']} @ {params['baudrate']}")

    @staticmethod
    def _friendly_open_error(err: str) -> str:
        s = str(err).lower()
        if "access is denied" in s or "winerror 5" in s or "permission" in s or "in use" in s:
            return f"{err}\n\n端口可能被其他程序占用，请关闭占用该端口的程序后重试。"
        if "could not open" in s or "not found" in s or "filenotfound" in s or "no such file" in s:
            return f"{err}\n\n端口不存在或已被拔出，请点击「刷新」重新扫描。"
        return str(err)

    def _on_auto_reconnect(self, enabled: bool):
        serial = self._svc("serial")
        if serial is None:
            return
        serial.set_auto_reconnect(enabled, self._w.settings.serial_params())
        self._persist_settings()

    # ---------------- TCP 转发 ----------------
    def _on_tcp_toggled(self, checked: bool):
        tcp = self._svc("tcp_forward")
        if tcp is None:
            self._w.tcp.set_status({"running": False})
            return
        if checked:
            params = self._w.tcp.tcp_params()
            tcp.start(params["host"], params["port"])
        else:
            tcp.stop()

    def _on_tcp_status(self, status):
        status = status or {}
        self._w.tcp.set_status(status)
        if not status.get("running") and status.get("error"):
            self._w.show_error(f"TCP 转发启动失败：{status['error']}")

    # ---------------- RTT 连接与 Shell ----------------
    def _on_rtt_connect(self):
        rtt = self._svc("rtt")
        if rtt is None:
            self._w.show_error("RTT 服务未就绪（当前模式未加载 RTT 插件）")
            return
        if rtt.is_connected:
            rtt.disconnect()
            return
        ok, err = rtt.connect()
        if not ok:
            self._w.show_error(f"连接 J-Link 失败：{err}")
        else:
            self._w.show_message("已连接 J-Link RTT")

    def _on_rtt_send(self, cmd: str):
        rtt = self._svc("rtt")
        if rtt is None:
            self._w.show_error("RTT 服务未就绪")
            return
        ok, err = rtt.send_shell(cmd)
        if not ok:
            self._w.show_error(err or "shell 发送失败")

    # ---------------- justfloat 协议解析 ----------------
    def _on_justfloat_toggled(self, checked: bool):
        jf = self._svc("justfloat")
        if jf is None:
            return
        jf.set_enabled(checked)
        self._persist_settings()

    def _on_justfloat_reset(self):
        jf = self._svc("justfloat")
        if jf is not None:
            jf.reset()

    def _on_justfloat_renamed(self, mapping: dict):
        jf = self._svc("justfloat")
        if jf is not None:
            jf.rename(mapping)

    def _on_justfloat_status(self, stats):
        # 表格（通道名 + 最新值）由节流后的状态事件刷新（10ms 帧下避免高频 UI 更新）
        self._w.justfloat.set_stats(stats)

    # ---------------- 浮点录制 ----------------
    def _on_recorder_start(self, duration: int, hz: float):
        fr = self._svc("float_recorder")
        if fr is None:
            return
        fr.set_duration(duration)
        fr.set_sample_hz(hz)
        result = fr.start(duration=duration)
        if not result.get("ok"):
            self._w.show_error(result.get("message", "录制启动失败"))
        self._persist_settings()

    def _on_recorder_pause(self):
        fr = self._svc("float_recorder")
        if fr is not None:
            fr.pause()

    def _on_recorder_resume(self):
        fr = self._svc("float_recorder")
        if fr is not None:
            fr.resume()

    def _on_recorder_stop(self):
        fr = self._svc("float_recorder")
        if fr is not None:
            result = fr.stop()
            self._refresh_recorder_files()
            if result.get("path"):
                self._w.show_message(
                    f"录制已保存：{Path(result['path']).name}（{result.get('rows', 0)} 行）"
                )

    def _on_recorder_status(self, status):
        status = status or {}
        if status.get("state") == "idle" and status.get("reason") == "timeout":
            self._w.show_message(
                f"录制达到时长自动结束：{Path(status.get('path', '')).name}"
            )
            # 到点自动结束也要刷新已录文件列表，否则新文件不出现
            self._refresh_recorder_files()
        self._w.recorder.set_status(status)

    def _poll_recorder(self):
        fr = self._svc("float_recorder")
        if fr is not None:
            self._w.recorder.set_status(fr.status())

    def _refresh_recorder_files(self):
        fr = self._svc("float_recorder")
        if fr is not None:
            self._w.recorder.set_files(fr.list_files())

    # ---------------- 发送 ----------------
    def _on_send(self):
        tx = self._svc("transmitter")
        if tx is None:
            self._w.show_error("发送服务未就绪")
            return
        t = self._w.send
        ok, err = tx.send(t.send_text(), t.tx_hex(), t.tx_encoding(),
                          t.send_newline(), t.newline_kind())
        if not ok:
            self._w.show_error(err or "发送失败")

    def _on_cycle_toggled(self, checked: bool):
        tx = self._svc("transmitter")
        if tx is None:
            return
        if checked:
            ok, err = self._sync_payload(tx)
            if not ok:
                self._w.show_error(err or "发送内容无效")
                self._w.send.cycle_check.setChecked(False)
                return
            tx.start_cycle(self._w.send.cycle_interval_ms())
        else:
            tx.stop_cycle()

    def _on_cycle_interval_changed(self, value: int):
        tx = self._svc("transmitter")
        if tx is not None and tx.is_cycling:
            tx.start_cycle(value)

    def _on_payload_changed(self):
        tx = self._svc("transmitter")
        if tx is not None and tx.is_cycling:
            self._sync_payload(tx)  # 循环中静默更新，出错保留上次有效 payload

    def _sync_payload(self, tx):
        t = self._w.send
        return tx.set_payload(t.send_text(), t.tx_hex(), t.tx_encoding(),
                              t.send_newline(), t.newline_kind())

    # ---------------- 接收区 ----------------
    def _on_clear(self):
        self._w.receive.clear()

    def _on_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self._w, "保存接收数据", "recv_log.txt",
            "文本文件 (*.txt);;CSV 文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    wr = csv.writer(fh)
                    wr.writerow(["timestamp", "direction", "source", "hex", "text"])
                    for direction, ts, data, source in self._w.receive.raw_entries():
                        wr.writerow([
                            ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                            direction,
                            source or "",
                            bytes_to_hex(data),
                            bytes_to_text(data, self._w.receive.rx_encoding_combo.currentText()),
                        ])
            else:
                Path(path).write_text(self._w.receive.receive_text(), encoding="utf-8")
        except Exception as e:
            self._w.show_error(f"保存失败：{e}")
            return
        self._w.show_message(f"已保存到 {path}")

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self._w, "打开历史数据文件", "",
            "数据文件 (*.txt *.csv *.log);;所有文件 (*)")
        if not path:
            return
        encoding = self._w.receive.rx_encoding_combo.currentText()
        recorder = self._svc("recorder")
        try:
            if recorder is not None:
                text = recorder.load_text(path, encoding)
            else:
                text = Path(path).read_text(encoding=encoding, errors="replace")
        except Exception as e:
            self._w.show_error(f"打开失败：{e}")
            return
        self._w.receive.append_analysis(text)
        self._w.show_message(f"已加载 {path}")

    # ---------------- 状态与日志 ----------------
    def _on_state(self, name: str, payload):
        if name == "opened":
            info = payload or {}
            self._w.settings.apply_serial_params(info)
            self._w.settings.set_connected(True)
            self._w.set_state(f"已连接 {info.get('port', '')} @ {info.get('baudrate', '')}")
            self._w.show_message(f"已连接 {info.get('port', '')}")
        elif name == "closed":
            self._stop_cycle_ui()
            self._w.settings.set_connected(False)
            self._w.set_state("已断开")
        elif name == "disconnected":
            self._stop_cycle_ui()
            self._w.settings.set_connected(False)
            self._w.set_state("连接断开")
            self._w.show_message("串口连接已断开")
        elif name == "error":
            self._w.show_error(str(payload))
        elif name == "reconnecting":
            self._w.set_state("断线，正在自动重连...")
        elif name == "reconnect_failed":
            self._w.set_state("自动重连中...")
            self._w.show_message(f"重连失败：{payload}")

    def _stop_cycle_ui(self):
        self._w.send.cycle_check.setChecked(False)

    def _on_log(self, msg: str):
        self._w.statusBar().showMessage(msg, 3000)

    def _on_ai_ready(self, info):
        info = info or {}
        self._w.settings.set_ai_status(
            True, info.get("host", ""), info.get("port", 0)
        )

    # ---------------- 持久化 ----------------
    def _persist_settings(self):
        cfg = self._svc("config")
        if cfg is None:
            return
        data = {}
        data.update(self._w.settings.settings_dict())
        data.update(self._w.receive.settings_dict())
        data.update(self._w.send.settings_dict())
        data.update(self._w.tcp.settings_dict())
        data.update(self._w.recorder.settings_dict())
        data.update(self._w.rtt.settings_dict())
        cfg.update(data)
        serial = self._svc("serial")
        if serial is not None and serial.is_open and self._w.settings.reconnect_enabled():
            serial.set_auto_reconnect(True, self._w.settings.serial_params())
