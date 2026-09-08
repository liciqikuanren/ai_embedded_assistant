# RTT 集成设计文档（J-Link RTT → AI嵌入式助手）

> 状态：已定稿。本文是固件（HC_CPB）与主机（ai_embedded_assistant）双侧改造的对照基准。

## 1. 目标

在现有AI嵌入式助手基础上，加入 J-Link RTT 通道，实现：

1. **RTT Shell**（下位机 `nr_micro_shell` 已跑在 RTT ch0）；
2. **波形通道**（下位机把 justfloat 帧写进 RTT ch1）→ 主机经 TCP 转发给 VoFA；
3. **设备日志**（下位机 log 走 RTT ch2）→ 主机控制台按需显示、AI 按需拉取；
4. **AI 交互**：Shell 用响应式（`shell_exec`），日志用拉取式（`read_log`）。

## 2. 四态模式（替换现有 `debug_mode` 布尔）

| # | 模式 | 探针 | 交互控制台 | 波形TCP转发面板 | 录制源 | AI 命令/响应 | AI 数据查看 |
|---|---|---|---|---|---|---|---|
| 1 | 串口交互（默认） | 串口 | 串口控制台（RX/TX）+ SendPanel | ✗ | 串口 justfloat | 串口命令 | — |
| 2 | 串口 + TCP VoFA 转发 | 串口 | 串口控制台 + SendPanel | ✓ | 串口 justfloat | 串口命令 | — |
| 3 | RTT Shell 交互 | J-Link | RTT控制台（Shell/Log）+ Shell输入行 | ✗ | 无 | RTT ch0（`shell_exec`） | RTT ch2（`read_log`） |
| 4 | RTT Shell + 波形 + TCP VoFA | J-Link | RTT控制台 + Shell输入行 | ✓ | RTT ch1 波形 | RTT ch0（`shell_exec`） | RTT ch2（`read_log`） |

- 默认模式为 1（串口交互）。
- **AI 连接是独立可选开关**，与四态模式正交。

## 3. 架构与数据流

```
                    固件 (STM32H743)
   RTT ch0 (shell)  ←/→  nr_micro_shell（终端）
   RTT ch1 (wave)   ←    justfloat 帧（float[] + 尾 00 00 80 7F）
   RTT ch2 (log)    ←    LOG_INFO/DEBUG...（按级别）

        │ J-Link (SWD)                │ 串口 (UART)
        ▼                              ▼
   主机 rtt 插件 (pylink 独立线程)     主机 serial 插件
        │ ch0/ch1/ch2 轮询             │
        ▼                              ▼
   事件总线 (cordis ctx) ────────► UI 桥 (bridge) ──► 控制台
        │                              │
        ├─ ch1 → 波形 feed → justfloat → TCP 服务端 → VoFA
        ├─ ch1/串口 → float_recorder（按模式切换源）
        └─ ch0/ch2 → AI 工具 shell_exec / read_log（按模式）
```

关键原则：

- **模式 = 数据流路由器**：切模式时联动「探针 / 面板 / 录制源 / AI 命令链路 / AI log 链路」五处。
- 用两个抽象服务隔离切换：
  - **波形 feed 服务**：单一消费者（录制 + TCP 转发共用），源由模式注入；
  - **AI 命令通道服务**：单一 `exec(cmd)`，实现由模式注入（串口发送 / RTT）。

## 4. 固件侧改动（`F:\HC_CPB_Floder\code\HC_CPB`）

1. **log 走 ch2**：`system/log/log_config.h` 中 `RTT_Printf` 由 `SEGGER_RTT_printf(0, ...)` 改为 `SEGGER_RTT_printf(2, ...)`。
2. **波形写 ch1**：新增/复用 justfloat 帧生成，把 `float[]` + 尾 `{0x00,0x00,0x80,0x7f}` 周期写入 RTT ch1（`SEGGER_RTT_Write(1, ...)`）。
3. **BUFFER_SIZE_DOWN 16 → 128**（`system/RTT/SEGGER_RTT_Conf.h`），保证 shell 长命令输入可靠。
4. **RTT 缓存**：确认 RTT 控制块/缓冲落在非缓存区（DTCM `0x20000000`）或做 cache 维护。
5. shell 传输已为 RTT（`SHELL_TRANSPORT_TYPE = SHELL_TRANSPORT_RTT`），无需改。

## 5. 主机侧改动（`ai_embedded_assistant`）

### 5.1 模式选择器与配置
- `settings` 面板加「模式」四选一下拉，替换 `debug_mode`；配置键 `mode`（取值 `serial`/`serial_vofa`/`rtt_shell`/`rtt_vofa`），默认 `serial`。
- 保留 `debug_mode` 兼容读取（旧配置迁移到 `mode`）。

### 5.2 rtt 插件（`plugins/rtt.py`）
- 提供 `rtt` 服务：`connect()` / `disconnect()` / `send_shell(cmd)` / `read_shell()` / `read_log()` / `read_wave()` / 状态。
- pylink 在**独立线程**运行（阻塞库），轮询 ch0/ch1/ch2，经 `ctx.emit` 上抛：`rtt_shell_rx` / `rtt_log` / `rtt_wave`。
- `rtt_start()` 定位控制块（优先 map/ELF 符号，其次手填地址）。
- 下行写 ch0 追加 `\r`（见 §7）。

### 5.3 波形 TCP 转发（主机为服务端）
- 新增 `vofa_server` 服务（或扩展 `tcp_forward`）：`asyncio.start_server` 监听，向所有 VoFA 客户端广播 justfloat 字节。
- 数据源按模式：串口 justfloat（模式 2）/ RTT ch1（模式 4）。
- 状态事件 `vofa_status`。

### 5.4 控制台 UI
- 右栏 `QTabWidget`（或等价）按模式切换：
  - **串口控制台**（模式 1/2）：统一输出框 + ☑RX/☑TX + 现有 SendPanel（下方）；
  - **RTT 控制台**（模式 3/4）：统一输出框 + ☑Shell/☑Log（ANSI 剥离）+ Shell 输入行（回车→`\r`，↑↓历史）。
- 复选框只过滤显示、不丢数据（沿用 `rx_show/tx_show` 语义）。

### 5.5 录制 feed 源切换
- `float_recorder` 的输入抽象为「波形 feed」，由模式注入：串口 justfloat（模式 1/2）或 RTT ch1（模式 4）。

### 5.6 AI 工具
- `ai_server` 按模式加载工具：
  - 模式 1/2：串口命令工具（FireWater 等，现有）；
  - 模式 3/4：`shell_exec`（响应式：命令+`\r` → 收 ch0 至提示符/超时 → 返回去 ANSI 文本）+ `read_log`（拉取式：级别/关键字/条数过滤，从 ch2 环形缓冲读）。
- 日志**不自动推入 AI 上下文**，仅按需拉取。

## 6. 模式切换路由表

| 动作 | 模式 1 | 模式 2 | 模式 3 | 模式 4 |
|---|---|---|---|---|
| 断开旧探针 | — | 串口 | — | 串口 |
| 连接新探针 | 串口 | 串口 | J-Link | J-Link |
| 面板 | 串口控制台 | 串口控制台+波形面板 | RTT控制台 | RTT控制台+波形面板 |
| 录制源 | 串口 justfloat | 串口 justfloat | 无 | RTT ch1 |
| AI 命令链路 | 串口 | 串口 | RTT ch0 | RTT ch0 |
| AI 数据查看 | — | — | ch2 | ch2 |

切换顺序：断旧探针 → 连新探针 → 切面板 → 切录制源 → 重载 AI 工具 → 广播 `mode_changed`。

## 7. 关键实现细节（已从固件源码确认）

- **Shell 换行符 = `\r`（0x0D）**：`NR_SHELL_END_OF_LINE = 1` → `NR_SHELL_END_CHAR = '\r'`；`ansi_get_char()` 原样返回输入字符，只有 `\r` 触发命令执行。`\n` 单独发不执行，`\r\n` 会多一个空行。
- **提示符 = `HC_dqj@root:`**（`NR_SHELL_USER_NAME`），作为响应式“命令完成”锚点。
- **ANSI**：`NR_SHLL_FULL_ANSI = 1`，shell 输出带 ANSI 转义，主机（GUI 与 AI 工具）需剥离后再显示/返回。
- **RTT 通道**：ch0=shell、ch1=波形（JScope 已用 ch1）、ch2=log。
- **TCP 方向**：主机做服务端，VoFA 连入。
- **justfloat 帧尾**：`{0x00,0x00,0x80,0x7f}`（小端 `+inf`），与现有 `justfloat.py` 一致。

## 8. 决策记录

- 探针用 **pylink**（square/pylink），**非 pyOCD**（pyOCD 无 RTT；J-Link 支持本就经 pylink）。
- log/shell 共用一个控制台页，用「显示」复选框过滤（不丢数据）。
- SendPanel 保持独立在下方（串口专用），Shell 输入行为 RTT 专用输入行。

## 9. 实施顺序

1. 固件侧（log 走 ch2、波形 ch1、BUFFER_SIZE_DOWN 128、缓存确认）；
2. 主机插件（rtt 插件、模式选择器、vofa_server）；
3. UI（控制台切换、Shell 输入行、波形面板）；
4. AI 工具（shell_exec、read_log）按模式加载；
5. 验证（语法/编译/测试）。
