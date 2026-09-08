---
name: ai-embedded-assistant-mcp
description: >-
  Operate the AI Embedded Assistant (ai_embedded_assistant) desktop tool — a serial / J-Link RTT debug
  console with waveform view, CSV recording and an MCP Streamable HTTP server at http://127.0.0.1:8765/mcp.
  Use when debugging a running embedded target: connect serial port or J-Link RTT, run shell commands
  (shell_exec), pull device logs (read_log), rename JustFloat channels, send FireWater temperature-PID
  commands, record float channels to CSV, or tune temp_kp/temp_ki/temp_kd. Covers the four run modes
  (serial / serial_vofa / rtt_shell / rtt_vofa), per-mode tool sets, AI-push constraints, and the
  edit → compile → flash → debug loop in cooperation with the eide skill (compile/flash via EIDE MCP).
---

# AI嵌入式助手 — 使用与 MCP 操作指南

AI嵌入式助手是上位机（主机侧）调试软件：**串口/RTT 调试 + 波形查看 + 数据录制 + AI 联动**，
软件本身与固件业务无关，可接任何串口 / J-Link RTT 设备。本 Skill 教你**怎么用这个软件**（含图形界面与 MCP 两种方式），
并说明它和 `eide-mcp-skill`（EIDE 编译/烧录）如何接力完成 **编辑代码 → 编译 → 下载 → 调试** 的完整闭环。

- 软件源码：本仓库 `software/code`（独立运行时即工程根）；从该目录 `pip install -r requirements.txt && python main.py`。
- 详细功能与界面步骤见 `docs/使用文档.md`（仓库根）；指令协议见 `software/code/docs/串口指令文档.md`。
- 配置默认 `~/.ai_embedded_assistant/config.yaml`（YAML，兼容旧 config.json），CSV 默认 `软件运行目录/csv_floder/`。

## 0. 定位与分工（先读）

本仓库是「AI 嵌入式全流程」的**调试验证**环节。链条分工：

| 环节 | 谁来干 | 说明 |
|---|---|---|
| 编辑代码 | AI（本会话） | 直接在工程目录改 .c/.h |
| 编译 / 重建 | **eide skill**（eide-mcp-skill） | EIDE MCP（默认 `127.0.0.1:8940/mcp`）：`check/build/rebuild/clean/flash/reload/add-src-dir` |
| 下载 / 烧录 | **eide skill** | `flash`（可 `--erase-all`） |
| 调试 / 验证 / 调参 | **本技能 + AI嵌入式助手** | 串口 / RTT shell / 日志 / 波形 / CSV 录制，全部经 MCP 或 GUI |

> 规则：用户说「编译/烧录/下载/重建/清理」→ 交给 **eide skill**：会话已加载就直接用其子命令（`build`/`rebuild`/`flash`/`clean`…，EIDE MCP `127.0.0.1:8940/mcp`）；
> 未加载则提示用户**自行安装** eide-mcp-skill（独立仓库 `github.com/AlexiaQAQ/eide-mcp-skill`，需 VSCode EIDE 扩展开启 MCP.Server）。
> 说「调试/连板子/看日志/看波形/下发命令/调 PID/录数据」→ 用本技能操作 AI嵌入式助手。两者互补，先编译烧录成功、再上电连 RTT 调试。

## 1. 四种运行模式（决定你能干什么）

顶部「运行模式」下拉切换（四态，即时热切换插件并刷新 MCP 工具集）：

| 模式 | 通道 | 用途 | 主要 MCP 工具面 |
|---|---|---|---|
| `serial` | 串口 | 普通收发 | 串口通用收发 + 配置管理（可任意发/读字节） |
| `serial_vofa` | 串口+波形 | 串口波形送 VOFA+ | 波形/录制/指令工具（**不能**通用收发） |
| `rtt_shell` | RTT ch0/ch2 | Shell + 设备日志 | RTT 工具（`rtt_connect/shell_exec/read_log`） |
| `rtt_vofa` | RTT ch0~ch2 | 固件调试一体 | RTT + 波形 + 录制 |

- 模式 = 数据流路由器：`get_status` / `config_status` 返回当前 `mode`；不确定就用它确认。
- 工具会按“已加载的服务”出现——先查再操作，不存在的工具别硬调。

## 2. 连接前置与 MCP 端点

1. 启动软件，顶部切到目标运行模式，串口模式先 `open_port`，RTT 模式先 `rtt_connect`。
2. MCP 端点：`POST http://127.0.0.1:8765/mcp`（Streamable HTTP），请求头 `Accept: application/json, text/event-stream`；
   body JSON-RPC（`{"jsonrpc":"2.0","id":N,"method":"tools/call","params":{"name":"...","arguments":{...}}}`），响应取 SSE `data:` 行 JSON 的 `result.content[].text`。
3. 发中文/参数建议先把 JSON 写成无 BOM UTF-8 文件再 POST，避免控制台编码乱码。
4. AI 联动（把数据实时推给 DSH 会话）前置：DSH 已加载 **ai-embedded-bridge** 插件（默认推 `http://127.0.0.1:3080/plugins/ai-embedded-bridge/incoming`）、
   软件内 MCP 已启动、GUI 勾选「推送数据到 DSH」；**总开关在 DSH 侧**（悬浮面板：off/chat/monitor），软件无法绕过。
   - `serial`：推串口接收数据；`rtt_shell/rtt_vofa`：推 Shell 输出（AI 自己命令的响应不推）；`serial_vofa`：**禁用推送**（波形数据只走 CSV）。

## 3. MCP 工具总览（按模式/服务可用）

- **串口**：`list_ports` `open_port(port,baudrate,parity,stopbits,flow)` `close_port` `get_status`
- **通用收发**（仅 `serial`）：`send(data,as_hex,encoding,newline)` `read_received(as_hex,encoding,clear)`
- **指令**（FireWater，`serial_vofa`）：`list_commands` `send_command("temp_sw:1;temp_tar:37;")` `set_params({...})` `send_preset("闭环控制默认")`；参数名与范围会被校验
- **配置**（全模式）：`config_status` `config_save(path)` `config_load(path)` `config_set(key,value)`（支持 `mode` 等键，改 `mode` 即热切换）
- **RTT**（rtt 模式）：`rtt_connect` `rtt_disconnect` `rtt_status` `shell_exec(cmd,timeout)`（响应式，等待提示符返回）`read_log(count,grep,level)`（拉取 ch2 日志）
- **波形/录制**（vofa 模式）：`tcp_forward_start/stop/status`；`justfloat_status/enable/reset/frames/latest/rename/reset_names/apply_doc_channels`；`float_recorder_start/pause/resume/stop/status/list/set_dir/set_duration/set_sample_hz`

## 4. 典型流程

### 4.1 RTT 固件调试（最常用，`rtt_shell`/`rtt_vofa`）
1. `rtt_connect` → `rtt_status` 确认 shell ch0 / 波形 ch1 / 日志 ch2 已建。
2. Shell 交互：`shell_exec("help")` 看命令；`shell_exec("<cmd> -h")` 看用法；命令以回车触发执行。
3. 看日志：`read_log(level="ERROR")` 或 `read_log(grep="xxx")`；日志按级别着色（DEBUG/INFO/WARN/ERROR/FATAL）。
4. 波形（`rtt_vofa`）：`justfloat_latest` 看实时通道；`justfloat_rename` 或 `justfloat_apply_doc_channels` 按文档命名；
   `float_recorder_set_sample_hz({"hz":50})` → `float_recorder_start({"duration":10})` → 到点自动停 → `float_recorder_list` 找 CSV → 读文件分析。
5. **设备复位后 RTT 会话失效**：烧录后先 `rtt_disconnect` 再 `rtt_connect`，ch2 日志才会继续。

### 4.2 串口波形调参演示（`serial_vofa`，FireWater 指令）
1. `get_status` + `justfloat_status`（注意通道是 Ch0.. 还是已命名）。
2. 通道命名：`justfloat_apply_doc_channels` 一键按指令文档命名 8 通道，或 `justfloat_rename({"0":"PumpRPM"})`。
3. 发指令：`set_params({"temp_sw":1,"temp_tar":37})` / `send_command("temp_sw:1;temp_tar:37;")` / `send_preset(...)`；
   下一帧（10ms）即可在 `justfloat_latest` 看到回显。
4. 录制与分析同 4.1（录制表头自动使用重命名通道名；20Hz+ 足够看清阶跃响应）。
5. 阶跃要诀：先切开环（`temp_sw:0`）让温度回落 → 下发目标（当前温度 +20）→ 开始录制 → 150ms 后 `temp_sw:1` 触发阶跃。

### 4.3 完整闭环：编辑 → 编译 → 下载 → 调试
1. 在固件工程改代码；
2. **eide**：`build`（或 `rebuild`），有错误先修；
3. **eide**：`flash`（确认板子已连，必要时 `--erase-all`）；
4. 上电后 **本技能**：`rtt_connect` → `read_log` 看启动日志 → `shell_exec` 交互验证功能 → 波形/录制做定量分析（上升时间/超调/稳态）；
5. 有问题 → 分析日志/波形定位 → 改代码 → 回到 2 循环；硬件/机械问题则回报给人。

> 前置：步骤 2/3 的编译与烧录依赖 **eide-mcp-skill**（独立仓库，需自行安装并开启 EIDE MCP，见 §0）；本技能只做烧录后的调试验证。

## 5. 注意事项 / 坑

- 工具随模式出现：调不到某个工具先 `config_status` 看 mode、确认对应服务已加载（改 mode 热切换，无需重启软件）。
- 调试查看数据优先走「录制 CSV + 读文件」，别指望读原始字节（JustFloat 100Hz 二进制帧，原始字节无意义）。
- 指令/参数越界会被拒（如 `temp_sw:2`、`temp_tar:150`）——先 `list_commands` 看元数据再发。
- 10ms 高频帧只在后台解析/录制，UI 表格 250ms 节流；看实时值用 `justfloat_latest`。
- CSV 首行目标值可能是录制开始前的旧值；分析以本次设置值为准，或逐行确认。

## 附录：温度 PID 调参实测经验（案例参考，勿想当然）

**参数含义（以固件文档为准）**：`temp_kp` 比例系数（大→电流饱和升温快但撤流猛）；`temp_ki` 积分系数（**大→windup→严重超调不收敛**）；
`temp_kd` 是**电流执行器一阶惯性阻尼（不是 PID 微分项）**（大→电流钝、刹车慢→超调更大）。

Δ≈20°C 阶跃实测（环境相关，仅参考）：`12/0/2` → 0.68s 到达、超调 +1.8°C、纯比例有静差（适合快速响应）；
小 `ki`（0.1~0.3 量级）可消静差但升温段易 windup。**先看固件文档与实测曲线，别照抄参数。**

常见坑：忘发 `temp_sw:1`（看到的是开环降温曲线）；把 `temp_kd` 当微分项调（方向反了）；
固件复位后参数归零需重新下发；测量阶跃**先开录制、再跳目标**，否则录到的是热态段。
