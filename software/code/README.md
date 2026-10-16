# Cordis AI嵌入式助手

基于 [Cordis](F:\Python_Floder\Python_Example\Cordis_Test) 插件框架与 PyQt6 实现的AI嵌入式助手。
架构遵循「**内核最小、外壳是薄 Host、一切能力都是插件（插件即服务）**」：每个功能是一个自包含插件，
通过 `ctx.provide` 暴露一个服务，通过事件总线协作。

## 架构

```
main.py                外壳 Host（QApplication + Cordis + 双线程桥，非插件）
core/                  内核 + 纯库（无生命周期）
  mini_cordis.py       内核（依赖注入 + 事件总线 + 副作用生命周期）
  codec.py             编解码纯函数
  theme.py             深色主题 QSS
plugins/               服务插件（扁平、自包含，load_dir 自动按依赖排序加载）
  config.py            服务 config：配置持久化
  logger.py            服务 log：日志
  codec.py             服务 codec：HEX/文本/多编码
  history.py           服务 history：发送历史 + 快捷指令
  serial_port.py       服务 serial：pyserial 收发、端口扫描、自动重连
  transmitter.py       服务 transmitter：手动/定时循环/文件发送
  recorder.py          服务 recorder：数据记录(.txt/.csv)
  tcp_forward.py       服务 tcp_forward：TCP 服务端，串口 ↔ 客户端双向透传
ui/                    UI 叶子插件（主窗口外壳 + 设置/接收/发送面板 + 控制器）
```

线程模型：主线程跑 Qt/UI，后台线程跑 asyncio 业务插件；`UIBridge` 把后台事件桥接到 Qt 信号。

## 运行

```bash
pip install -r requirements.txt
python main.py
```

无头冒烟测试（自动退出）：

```bash
# Windows PowerShell
$env:AUTO_QUIT_MS=3000; python main.py
```

## 功能

- **基础配置与连接**：端口扫描/刷新、波特率（支持自定义）、数据位 5-8、停止位 1/1.5/2、
  校验 None/Odd/Even/Mark/Space、流控 None/RTS-CTS/XON-XOFF、打开/关闭、断线自动重连。
- **数据收发与显示**：ASCII/HEX 收发、多编码（UTF-8/GBK/GB2312/GB18030/Big5/ASCII/Latin-1/UTF-16）、
  发送新行(CRLF/LF/CR)、定时循环发送、发送历史、快捷指令、从文件发送；
  接收区 HEX/文本切换、时间戳、自动换行、自动滚动、**「显示接收 / 显示发送」可视开关**
  （可分别隐藏接收或发送数据，字节计数与历史记录不受影响）、清空、保存 .txt/.csv、加载历史文件分析。
- **进阶**：中文等多字节字符经编码选择正常显示。
- **运行模式**：正常模式仅提供 AI 功能；开启「调试模式」后额外启用 TCP 转发、
  JustFloat 协议解析与浮点通道录制（UI 面板 + AI 均可操作）。
- **JustFloat 协议解析**：把串口传来的 JustFloat 数据帧（小端浮点数组 + 帧尾
  `{0x00, 0x00, 0x80, 0x7f}`）解析为浮点通道，并识别图片前导帧；面板显示通道表格
  （可重命名、实时数值）与解析统计。
- **浮点通道录制**：对解析出的浮点通道按采样率定时快照录制为 CSV
  （`csv_floder/log_YYYYMMDD_HHMMSS.csv`），支持设置时长（默认 5 分钟，到点自动结束）、
  中途暂停/继续、手动结束。
- **AI 接口（MCP）**：内置 MCP Streamable HTTP 服务，AI 可打开/配置串口、收发数据；
  接收区对「AI 发送 / 人发送 / 设备回传」打标签（`→[AI]` 紫、`→[人]` 绿、`←` 白）。

配置与历史持久化到 `~/.ai_embedded_assistant/`（配置为 **YAML**：`config.yaml`，兼容读取旧版
`config.json`；读取兼容 BOM，写入为无 BOM UTF-8）。

## AI 操作指南（Skill）

`../../skills/ai-embedded-assistant/SKILL.md` 收录了通过 MCP 接口操作本程序的经验：
连接方式、调试模式 AI 能力边界、标准操作流程（通道命名 → 发指令 → 录制观察）、
温度 PID 调参实测结论（推荐 `kp=12, ki=0, kd=2`，温差 20°C 阶跃 0.68s 到达、超调 1.8°C）
与常见坑（`temp_kd` 是执行器阻尼非微分项、积分饱和、忘发 `temp_sw:1` 等）。

## 配置管理（YAML，按需加载/保存）

- **默认配置**：`~/.ai_embedded_assistant/config.yaml`（YAML 格式，含全部参数，中文可读）；
  若不存在则自动回退读取旧版 `config.json`（不丢失已有配置）。
- **按需加载**：串口设置面板「配置文件 → 加载配置」选择任意 `.yaml/.json` 载入（也可 AI 调
  `config_load(path)`），载入后所有面板与调试模式显隐立即刷新。
- **按需保存**：「保存配置」把当前全部参数另存为 `.yaml/.json`（也可 AI 调 `config_save(path)`）。
- **按需修改**：面板上修改参数即自动保存；AI 也可用 `config_set(key, value)` 修改并落盘。
- 布局：**参数设置全部在左侧**（串口设置 / TCP 转发 / JustFloat 解析 / 浮点录制），
  **右侧上方为数据接收、下方为数据发送**，左右宽度可拖动分隔条调整。

## 运行模式（正常 / 调试）

「串口设置 → 运行模式」可勾选「调试模式」（配置键 `debug_mode`，默认 `false`），
**勾选后立即生效**（动态加载/卸载调试插件并刷新面板与 AI 工具，无需重启）：

| 模式 | 加载的插件 | UI 面板 | AI（MCP）能力 |
|---|---|---|---|
| 正常（默认） | 仅 AI 相关（ai_server 等） | 串口设置/接收/发送/AI 联动 | 通用收发 + 配置管理（10 工具） |
| 调试 | + TCP 转发 / justfloat / float_recorder | + TCP 转发/JustFloat 解析/浮点录制 | **只能发送串口指令**；查看数据只能通过录制 CSV |

调试模式下 AI 的能力约束：
- **发送**：只能用 `send_command` / `set_params` 发送 **FireWater 串口指令**（依据 `docs/串口指令文档.md`，
  格式 `关键字:数值;`，如 `temp_sw:1;temp_tar:37;`），不再提供通用 `send`；
- **接收查看**：不再提供 `read_received` 原始字节读取，只能通过 `float_recorder_*` 录制与
  `float_recorder_list` 查看生成的 CSV（JustFloat 二进制帧 10ms 高频，原始字节无意义）；
- **不注入 DSH 上下文**：调试模式下「AI 联动推送」被强制禁用（灰显），justfloat 数据流
  不会直接注入 DSH Agent 会话——只能通过录制 CSV 查看；
- 实时状态仍可看 `justfloat_latest`（当前帧数值）与面板通道表格；
- 可用 `justfloat_apply_doc_channels` 一键按文档命名 8 个通道
  （temp_kp/temp_ki/temp_kd/temp_sw/temp_tar/temp_value/temp_current/timestamp）。

调试模式下 AI 可操作 TCP 转发、JustFloat 解析与录制（见下文「AI 接口（MCP）」工具清单）。

## JustFloat 协议解析（调试模式）

JustFloat 是小端浮点数组字节流协议：

- **采样数据帧**：`float fdata[CH_COUNT]`（小端）+ 帧尾 `{0x00, 0x00, 0x80, 0x7f}`。
- **图片前导帧**：7 个 int32（`id/size/width/height/format` + 两个 `0x7F800000`），结尾 8 字节为连续两个帧尾。

面板「JustFloat 协议解析」：启用开关、帧数/图片帧/丢弃字节统计、**通道表格**（名称可双击重命名、
实时显示最新数值）、重置解析器。无帧尾的杂乱数据超过 64KB 会自动丢弃最旧部分防爆（协议约定）；
51 单片机发送端需按大端调换字节序。配置键：`justfloat_enabled`（解析开关）、
`justfloat_channel_names`（通道重命名映射，如 `{"0": "PumpRPM", "1": "Flow_L_min"}`）。

**通道重命名与录制联动**：解析出的通道默认名 `Ch0..ChN-1`，可在面板双击重命名（或 AI 调
`justfloat_rename`）。重命名后的通道名会作为浮点录制的 CSV 表头，通道数据即录制数据源——
录制 `start` 不指定通道名时自动继承 justfloat 的命名。

## 浮点通道录制（调试模式）

「浮点通道录制」面板：设置时长（默认 300 秒）与采样率（默认 1Hz），开始/暂停/继续/结束。
录制对 justfloat 解析出的最新通道值按采样间隔定时快照写行（参考 Storage 快照模式）：

- 保存位置：`csv_floder/`（配置键 `csv_dir`，空则默认项目根 `csv_floder`），文件名 `log_YYYYMMDD_HHMMSS.csv`；
- 表头 `Time(s),Ch0,Ch1,...`（通道名可在 `start(channels=...)` 中自定义），数值 6 位小数，
  文件带 BOM（utf-8-sig，Excel 可直接打开）；示例见 `csv_floder/log_20000103_232730.csv`；
- 时长到点自动结束（状态 `reason=timeout`）；暂停不写行但总时长照走；通道数变化自动跳过该帧；
- 配置键：`csv_dir`、`csv_duration_s`（默认 300）、`csv_sample_hz`（默认 1.0）。

## TCP 转发（服务端）

主界面「TCP 转发（服务端）」面板：勾选「启用转发」即在本机指定地址/端口启动监听（默认
`127.0.0.1:9000`），可随时取消勾选停止。数据流：

- **串口 → 客户端**：串口收到的每一段数据，原样广播给所有已连接的 TCP 客户端（多客户端同时接收）。
- **客户端 → 串口**：任一客户端发送的字节流，原样写入串口（`source="tcp"`，接收区以琥珀色
  `→[TCP]` 标签显示）。例：客户端发送 `aaa`，串口即发送 `aaa`。

说明：

- 转发为**原始字节透传**，不做 HEX/文本/换行处理。
- 串口未打开时，客户端发来的数据静默丢弃（不弹窗）。
- 慢客户端会被自动断开，不影响其他客户端接收。
- 启动时默认不自动开启监听；修改地址/端口后需重新勾选启用才生效。
- 端口被占用/地址非法时弹窗提示，开关自动复位。
- 配置键：`tcp_host`、`tcp_port`（可在 `~/.ai_embedded_assistant/config.json` 或界面中修改）。

## AI 接口（MCP）

启动后本地会起一个 MCP Streamable HTTP 服务（默认 `http://127.0.0.1:8765/mcp`），
在控制台日志里可看到地址。把该地址配到你的 MCP 客户端即可（Claude Desktop / Cursor 等，
DSH 用 `@deepseek-ai/dsh-mcp-client` 配 `transport: streamable-http`）。

工具清单（正常模式：通用收发 + 配置管理；调试模式：指令专用 + 调试工具，两者互斥）：

| 工具 | 说明 |
|---|---|
| `list_ports` | 列出可用串口 |
| `open_port(port, baudrate, bytesize, parity, stopbits, flow)` | 打开/重配串口 |
| `close_port` | 关闭串口 |
| `get_status` | 连接状态与 AI 接收缓冲统计（含 `debug_mode`） |
| `send(data, as_hex, encoding, newline)` | 发送（HEX/文本、可追加换行）**仅正常模式** |
| `read_received(as_hex, encoding, clear)` | 读取接收到的数据（默认读后清空）**仅正常模式** |
| `config_status` | 查询当前配置文件路径与配置项数 |
| `config_save(path)` | 把当前全部配置保存到指定 .yaml/.json（缺省默认文件） |
| `config_load(path)` | 从指定 .yaml/.json 加载配置（缺省重载默认文件） |
| `config_set(key, value)` | 修改一项配置并立即保存（仅支持已定义配置键） |
| `tcp_forward_start(host, port)` | 启动 TCP 转发服务端 |
| `tcp_forward_stop` | 停止 TCP 转发 |
| `tcp_forward_status` | 查询 TCP 转发状态 |
| `justfloat_status` | 查询 justfloat 解析统计 |
| `justfloat_enable(enabled)` | 启用/停用 justfloat 解析 |
| `justfloat_reset` | 重置解析器（清缓冲与统计） |
| `justfloat_frames(limit, clear)` | 读取累积浮点帧（默认读后清空） |
| `justfloat_latest` | 查询当前解析到的通道：`{names, values, count}`（重命名后名字 + 最新数值） |
| `justfloat_rename(mapping)` | 重命名通道（如 `{"0": "PumpRPM", "Ch1": "Flow_L_min"}`），录制表头同步使用 |
| `justfloat_reset_names` | 清除全部通道重命名（恢复 Ch0..） |
| `list_commands` | 列出可下发的串口指令（温度 PID 场景说明 + 5 参数元数据 + 4 个预设） |
| `send_command(command)` | 发送一条串口指令（如 `temp_sw:1;temp_tar:37;`，校验参数与范围） |
| `set_params(params)` | 按 `{"temp_sw": 1, "temp_tar": 37}` 构造并发送串口指令 |
| `send_preset(preset)` | 一键发送温度 PID 场景预设：`闭环控制默认` / `调大比例系数` / `增大阻尼` / `切回开环` |
| `justfloat_apply_doc_channels` | 一键按指令文档命名 8 个通道 |
| `float_recorder_start(channels, duration)` | 开始录制（不传 channels 时继承 justfloat 重命名通道名） |
| `float_recorder_pause` / `float_recorder_resume` | 暂停 / 继续录制 |
| `float_recorder_stop` | 结束录制并落盘 CSV |
| `float_recorder_status` | 查询录制参数与状态（state/rows/remaining/duration/sample_hz/dir） |
| `float_recorder_list` | 列出录制目录下 CSV 文件 |
| `float_recorder_set_dir(path)` | 设置录制目录 |
| `float_recorder_set_duration(seconds)` | 设置录制默认时长（下次 start 生效） |
| `float_recorder_set_sample_hz(hz)` | 设置录制采样率（下次 start 生效） |

端口/地址可在 `~/.ai_embedded_assistant/config.json` 修改 `ai_server_host`、`ai_server_port`，
或设置 `ai_server_enabled=false` 关闭。

## AI 联动推送（串口数据直连 DSH Agent）

AI嵌入式助手可把**接收到的数据**推送到 DSH 的 `ai-embedded-bridge` 插件，注入选定的 Agent 会话，
让 AI 像聊天一样实时响应串口数据。两种模式（可在 DSH 页面切换）：

- **聊天**：每收到一条数据，AI 就回一条。
- **监听**：数据先缓冲，按间隔合并成一条监控消息再交给 AI。

> **总开关在 DSH 侧**：`ai-embedded-bridge` 默认 `mode=off`（不注入），并忽略
> `incoming` 请求体里的 `mode` 字段——只有你在 DSH 页面上打开注入（聊天/监听）才会注入，
> AI嵌入式助手无法绕过。AI嵌入式助手侧的 `ai_push_mode` 仅作为参考值。

**双向联动（默认开）**：注入串口数据后，DSH 桥等 AI 回复回合结束，把回复回写AI嵌入式助手
（`POST {text}` 到 `ai_reply_host:ai_reply_port`，默认 `http://127.0.0.1:8766/`），
AI嵌入式助手再通过 `transmitter`（source="ai"）把 AI 回答从串口发出。
可在 DSH 悬浮面板里勾选/取消「AI 回复回写串口」，或 `POST /plugins/ai-embedded-bridge/echo {enabled}` 切换。

**页面回复 ≠ 串口回复**：回写串口前默认做**简化**（`simplifyReply`，默认开）——
去 Markdown 符号、压缩空行、截断（默认 600 字符），只发核心内容；页面上的完整回答不变。
可在面板勾选/取消「串口回复简化」，或 `POST /plugins/ai-embedded-bridge/echo {enabled, simplify, maxChars}` 调整。

配置（`~/.ai_embedded_assistant/config.json`）：

| 键 | 说明 |
|---|---|
| `ai_push_enabled` | 是否推送（AI嵌入式助手侧开关；还需 DSH 侧总开关打开才真正注入） |
| `ai_push_mode` | `chat` / `monitor`（仅参考，实际模式由 DSH 侧决定） |
| `ai_push_url` | 桥地址，默认 `http://127.0.0.1:3080/plugins/ai-embedded-bridge/incoming` |
| `ai_reply_enabled` | 是否开启 AI 回复回写 HTTP 服务（默认开） |
| `ai_reply_host` / `ai_reply_port` | 回复回写服务地址，默认 `127.0.0.1:8766` |

DSH 侧的桥插件在 `../../dsh_plugin/ai-embedded-bridge/`（Node），装到 DSH web profile 的 `node_modules/ai-embedded-bridge`
并注册进 `cordis.patch.yml` 后，**重启 DSH** 生效。桥端点：

- `POST /plugins/ai-embedded-bridge/incoming` `{text, hex}` — 推送数据（mode 忽略，由 DSH 侧开关决定）
- `POST /plugins/ai-embedded-bridge/mode` `{mode, monitorMs?}` — 总开关/模式：`off` / `chat` / `monitor`
- `POST /plugins/ai-embedded-bridge/echo` `{enabled?, simplify?, maxChars?}` — 回写开关 / 简化开关 / 截断长度
- `POST /plugins/ai-embedded-bridge/target` `{agentId}` — 指定注入会话（`""` = 自动选最新会话）
- `POST /plugins/ai-embedded-bridge/status` `{}` — 查询状态 + 在线会话列表（`agents`、`latestAgentId`）

### DSH 页面悬浮面板

插件通过 `webServer.tapIndex` 向 DSH 每个页面注入一个右下角悬浮「串口」按钮，点击弹出面板：

- **总开关**：关闭 / 聊天 / 监听（默认关闭，不开启就不注入）
- **注入会话**：下拉精确选择串口数据注入到哪个 Agent 会话（或「自动 = 最新会话」），
  下拉显示会话标题/目录名，悬停可看完整 id 与创建时间
- **AI 回复回写串口**：勾选后，AI 的回答自动从串口发出（双向联动）
- **串口回复简化**：勾选后回写前自动去 Markdown/压行/截断，只发核心内容（页面回答不变）
- **状态**：当前模式、目标会话、在线会话数、缓冲字符数、回写开关，每 3 秒自动刷新

修改插件代码后需**重启 DSH**（`node_modules/ai-embedded-bridge` 是启动时加载的静态插件，
无热重载）。


