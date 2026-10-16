---
name: ai-embedded-assistant
description: >-
  Operate the AI Embedded Assistant (ai_embedded_assistant) desktop debug tool — serial / RTT debug console with
  waveform view, CSV recording, and an MCP Streamable HTTP server at http://127.0.0.1:8765/mcp.
  Covers RTT over BOTH J-Link (pylink) and DAPLink/CMSIS-DAP (pyOCD): rtt_probe selection, pyOCD target/ELF control-block
  location, probe exclusivity during flashing. Use when debugging a running embedded target: connect serial or RTT,
  run shell commands (shell_exec), pull device logs (read_log), rename JustFloat channels (wave_names), record channels
  to CSV, tune temp_kp/ki/kd, or wire the edit -> build -> flash -> debug loop together with the EIDE MCP (eide_build/eide_flash).
---

# AI嵌入式助手 — 使用与 MCP 操作指南

AI嵌入式助手是上位机（主机侧）调试软件：**串口/RTT 调试 + 波形查看 + 数据录制 + AI 联动**。
软件与固件业务无关，可接任何串口 / RTT 设备；**RTT 探针可选 J-Link 或 DAPLink/CMSIS-DAP**（见 §2）。
本 Skill 教你**怎么用这个软件**（GUI 与 MCP 两种方式），以及它和 EIDE MCP 如何接力完成 **编辑代码 → 编译 → 烧录 → 调试**。

- 软件源码：本仓库 `software/code`；从该目录 `pip install -r requirements.txt && python main.py`。
- 详细界面步骤见 `docs/使用文档.md`；RTT 设计见 `software/code/docs/rtt_design.md`。
- 配置默认 `~/.ai_embedded_assistant/config.yaml`；CSV 默认 `软件运行目录/csv_floder/`。

## 0. 定位与分工（先读）

| 环节 | 谁来干 | 工具 / 技能 |
|---|---|---|
| 编辑代码 | AI | 直接改 .c/.h |
| 编译 / 重建 / 清理 | **EIDE MCP** | `eide_build` / `eide_rebuild` / `eide_clean`（`eide-mcp-skill`、`stm32-eide-dev`） |
| 烧录 / 下载 | **EIDE MCP** | `eide_flash`（OpenOCD/cmsis-dap 或 JLink，取决于工程 uploader） |
| 调试 / 验证 / 调参 | **本技能 + AI嵌入式助手** | 串口·RTT shell·日志·波形·CSV，全部可经 MCP |
| VOFA+ 界面 | `vofa-ui-config` 技能 | 生成 `*.tabviews.json` / `*.cmds.json` / `channels.csv` |

> 说「编译/烧录/下载」→ EIDE MCP；说「调试/连板/看日志波形/下发命令/调 PID/录数据」→ 本技能。**先编译烧录成功，再连 RTT 调试。**

## 1. 四种运行模式（决定你能干什么）

| 模式 | 通道 | 用途 | 工具面 |
|---|---|---|---|
| `serial` | 串口 | 通用收发 | 串口收发 + 配置管理 |
| `serial_vofa` | 串口 + 波形 | 串口波形送 VOFA+ | FireWater 指令 / 波形 / 录制（**无通用收发**） |
| `rtt_shell` | RTT ch0/ch2 | Shell + 日志 | `rtt_connect` / `shell_exec` / `read_log` |
| `rtt_vofa` | RTT ch0~ch2 | 调试一体 | RTT + 波形 + 录制 + TCP 转发 |

模式 = 数据流路由器；`get_status` / `config_status` 返回当前 `mode`。工具随「已加载的服务」出现，先查再调。

## 2. 探针：J-Link 与 DAPLink（重点）

**RTT 与探针无关**——它只是目标 RAM 里的控制块 + 环形缓冲；任何能读写目标内存的调试器都能用。
所以 **DAPLink 不需要改固件**，主机只要知道控制块地址即可。

### 2.1 选探针（配置键 `rtt_probe`）

| `rtt_probe` | 后端 | 依赖 | 典型场景 |
|---|---|---|---|
| `jlink`（默认） | SEGGER pylink（`JLink_x64.dll`） | 安装 SEGGER J-Link 软件；`rtt_jlink_dll` 可显式指定 DLL | 手上只有 J-Link |
| `dap` | pyOCD（`pyocd.debug.rtt`） | `pip install pyocd`；Windows 需 WinUSB 驱动 | DAPLink / 任意 CMSIS-DAP 探针 |

相关配置键（`config_set` / `config_status`）：

| 键 | 说明 |
|---|---|
| `rtt_probe` | `jlink` / `dap` |
| `rtt_chip` | J-Link 芯片名（如 `STM32H743XI`） |
| `rtt_dap_target` | pyOCD 目标名（如 `stm32h743xx`；**不是** `STM32H743XI`） |
| `rtt_dap_elf` | 固件 `.axf`/`.elf`，解析符号 `_SEGGER_RTT` 拿控制块地址（**最稳，推荐**） |
| `rtt_control_block_addr` | 直接指定控制块地址（如 `0x24006ef8`）；留空则走 ELF / 扫描 |
| `rtt_cb_search_addr` / `rtt_cb_search_size` | 不给地址也不给 ELF 时的扫描范围；留空＝扫 STM32H7 常用 RAM 区 |
| `rtt_serial_no` | J-Link 序列号 / **DAPLink UID**（多个探针时用来挑） |
| `rtt_interface` / `rtt_speed` | `SWD`/`JTAG`、接口速度 kHz |
| `rtt_jlink_dll` | `JLink_x64.dll` 路径（留空自动搜索 EIDE/SEGGER） |

### 2.2 DAPLink 的控制块定位（踩过的坑）

pyOCD 内置 `stm32h743xx` 的内存映射**不含 AXI SRAM**，而且其自动字节扫描有步进 bug——所以**别指望 pyOCD 自动找控制块**。
主机实现的三级定位：

1. `rtt_control_block_addr` 精确地址；
2. `rtt_dap_elf` 解析 `_SEGGER_RTT`（例：`H7_Template.axf` → `0x24006ef8`）——**推荐**，重编后自动跟随；
3. 在 `rtt_cb_search_addr/size`（默认扫 AXI SRAM/DTCM/SRAM1-3/SRAM4/ITCM）里扫 `SEGGER RTT` 标志。

`rtt_status` 会回 `{connected, probe, probe_name, detail, chip, interface}`，`detail` 里带目标名与命中的控制块地址。

### 2.3 烧录（DAPLink / J-Link）

- **EIDE MCP**：`eide_flash` 用工程 `.eide/eide.yml` 的 `uploader`（`JLink` 或 `OpenOCD`）。用 DAPLink 时把它切成 `OpenOCD` + `interface: cmsis-dap`；Keil CMSIS-DAP 等探针 OpenOCD 也能驱动。
- **纯命令行**：`pyocd flash -t stm32h743xx --probe <uid> firmware.hex`（或 J-Link 的 `JLink.exe -device ... -CommandFile commands.jlink`）。
- ⚠️ **探针是独占的**：软件连着 RTT 时探针被占用，烧录会失败（OpenOCD 报 `CMSIS-DAP command CMD_INFO failed` / `Cannot connect`，JLink 报 `No emulator ... found`）。
  正确顺序：**`rtt_disconnect` → `eide_flash` → `rtt_connect`**（见 §6.3）。
- 烧录后控制块地址会变：用 `rtt_dap_elf` 指向新 `.axf` 即可自动跟随；只填了固定地址的要更新。

## 3. 连接前置 / MCP 端点 / DSH 注册

### 3.1 启动软件
- 检查：`Get-Process AIEmbeddedAssistant -ErrorAction SilentlyContinue`
- 启动：`Start-Process $env:AI_EMBEDDED_ASSISTANT_EXE`（未定义则用安装路径；源码版 `python main.py`）
- 就绪：`Test-NetConnection 127.0.0.1 -Port 8765`；GUI 切到目标运行模式

### 3.2 裸 HTTP 调用（不经 DSH）
`POST http://127.0.0.1:8765/mcp`（Streamable HTTP），头 `Accept: application/json, text/event-stream`；
body JSON-RPC，响应取 SSE `data:` 行的 `result.content[].text`。中文参数先写无 BOM UTF-8 文件再 POST。

### 3.3 接进 DSH（推荐，AI 直接用原生工具）
在 DSH profile 的 `cordis.patch.yml` 里挂 MCP 客户端（面板的「MCP」受管块格式）：

```yaml
# >>> dsh-skill-mcp-panel:mcp:begin
- insert:
    - id: panel-mcp-ai-embedded-assistant
      name: "@deepseek-ai/dsh-mcp-client"
      config: { serverName: ai-embedded-assistant, transport: streamable-http, url: http://127.0.0.1:8765/mcp }
    - id: panel-mcp-eide
      name: "@deepseek-ai/dsh-mcp-client"
      config: { serverName: eide, transport: streamable-http, url: http://127.0.0.1:8940/mcp }
# <<< dsh-skill-mcp-panel:mcp:end
```

挂上后工具名变成 `mcp__ai-embedded-assistant__rtt_connect`、`mcp__eide__eide_build` 等，本 Skill 下文用短名指代。
**软件（8765）与 VSCode EIDE 扩展（8940）都必须在线**，否则对应条目连接失败。

## 4. MCP 工具总览（短名）

- **串口**：`list_ports` `open_port` `close_port` `get_status`
- **通用收发**（仅 `serial`）：`send` `read_received`
- **FireWater 指令**（`serial_vofa`）：`list_commands` `send_command("temp_sw:1;temp_tar:37;")` `set_params({...})` `send_preset(...)`
- **配置**：`config_status` `config_save/load` `config_set`（改 `mode` 会热切换，约 1~2s 后 MCP 工具集刷新）
- **RTT**：`rtt_connect` `rtt_disconnect` `rtt_status` `shell_exec(cmd,timeout)` `read_log(count,grep,level)`
- **波形/录制**：`tcp_forward_start/stop/status`；`justfloat_status/enable/reset/frames/latest/rename/reset_names/autoname/apply_doc_channels`；`float_recorder_start/pause/resume/stop/status/list/set_dir/set_duration/set_sample_hz`

> `justfloat_frames` 只保留 **500 帧**（100Hz 下≈5s），要看完整瞬态就在阶跃后 ~5s 内取帧；更长的曲线用录制 CSV。

## 5. 与其他 MCP / 技能的分工

| 技能 | 负责 | 入口 |
|---|---|---|
| **本技能** | 串口/RTT 调试、日志、波形、录制、PID 调参 | MCP 8765 |
| `eide-mcp-skill` | `check/build/rebuild/clean/flash/reload/add-src-dir` | EIDE MCP 8940 |
| `stm32-eide-dev` | EIDE 工程配置、驱动/代码开发工作流 |（技能文档，无 MCP） |
| `vofa-ui-config` | VOFA+ 界面 JSON、通道绑定、commands/CSV |（脚本 + 技能） |
| `jtool-hardware` | I2C/SPI/GPIO/PWM/ADC/EEPROM 硬件适配器 | JTool CLI |

## 6. 典型流程

### 6.1 RTT 调试（`rtt_shell` / `rtt_vofa`）
1. `rtt_connect` → `rtt_status` 确认探针/目标/控制块与三条通道（shell ch0 / 波形 ch1 / 日志 ch2）。
2. 固件支持 `wave_names` 时，连接后会自动按固件通道名重命名（也可 `justfloat_autoname`）。
3. `shell_exec("help")` 看命令；`read_log(level="ERROR")` 看日志。
4. 波形：`justfloat_latest` 看实时值；录制用 `float_recorder_set_sample_hz` → `float_recorder_start` → 读 CSV 分析。
5. **设备复位 / 烧录后必须 `rtt_disconnect` → `rtt_connect`**，否则 ch2 日志不再送达。

### 6.2 串口波形调参（`serial_vofa`，FireWater）
`get_status` → `justfloat_apply_doc_channels` 命名 → `set_params({"temp_sw":1,"temp_tar":37})` → 录制分析。
阶跃要诀：先开环（`temp_sw:0`）让温度回落 → 下发目标 → 开始录制 → 150ms 后 `temp_sw:1` 触发。

### 6.3 完整闭环：编辑 → 编译 → 烧录 → 调试（实测顺序）
1. 改固件代码；
2. `eide_build`（失败先修）；
3. **先 `rtt_disconnect` 释放探针**；
4. `eide_flash`；
5. 等 0.3~1s（等 `SEGGER_RTT_Init` 跑完）→ `rtt_connect`；
6. `read_log` 看启动日志、`shell_exec` 验证功能、波形/录制做定量分析。

> 若第 4 步报 `CMSIS-DAP command CMD_INFO failed` / `Cannot connect to J-Link`：**探针被占用**（多半是本软件 RTT 没断），回到第 3 步。

## 7. 注意事项 / 坑

- **RTT 模式下 TCP/VOFA 回发走的是 shell，不是 FireWater**：`rtt_vofa` 时主机把 TCP 客户端发来的命令转给 **RTT shell(ch0)**；固件的 FireWater 解析器只挂在串口上。
  所以 VOFA 滑块绑定的命令必须写成**固件 shell 命令**，且**大小写敏感**（`strcmp`）：`temp_kd:2.65` ✅ / `TEMP_KD:2.65` ❌（`no command named`）。分隔符空格或冒号都行（`nr_shell_strtok(token, " :")`），行尾 `;` 无害（`atof` 在非数字处停）。
- **通道名用同一种大小写**：固件 `wave_names` 输出的名字要和 VOFA `channels.csv` 完全一致（否则自动命名会覆盖你 CSV 里的名字）。本工程已统一成小写 `temp_kp…`。
- shell 的回显与提示符会被主机一并广播给 VOFA（`tcp_forward` 的 `rtt_shell_rx` 分支），可能混进 JustFloat 字节流；波形抖/断先怀疑这里。
- 工具随模式出现；切模式约 1~2s 后 MCP 工具集刷新，期间调用可能失败，稍后重试。
- 调试数据优先「录制 CSV + 分析」，别读原始二进制字节；`justfloat_frames` 只 500 帧。
- 指令参数越界会被拒（`temp_sw:2`、`temp_tar:150`）——先 `list_commands` 看范围。

## 附录 A：温度 PID 调参实测经验

**先看固件实现的两个坑**：
1. `pid.c` 的积分是 `i_err += err`，**不乘 dt**。100Hz 下 `ki` 的有效强度是标称的 100 倍；积分权限 = `ki × i_err_limit`。
2. 温度环调用 `PID_Set(..., kd=0.0f, ...)`，**`temp_kd` 不是 PID 微分项**，它只改电流执行器时间常数 `tau = TEMP_SIM_TAU_BASE·(1+kd)`。

**热模型只有加热没有制冷**（`p_heat = I²R`、`p_loss = K·(T−T_amb)`），所以：
**目标温度低于环境温度时，温度永远停在环境温度**（电流被限到 0），这不是 bug。

实测（环境 25°C，`i_err_limit = output_limit = 100`）：

| 参数 | 25→37°C 超调 | 稳定时间 | 备注 |
|---|---|---|---|
| `kp=20 / ki=0.5`（旧固件默认） | 上电实测峰值 38.5（>50%） | — | 大 kp 直接把电流打满 |
| `kp=5.5 / ki=0.52 / kd=2.65`（现场实测） | **55%**（峰值 43.6） | 20.8s | 有静差、积分 windup |
| `kp=3.0 / ki=0.15 / kd=0.5`（**现固件默认** `TEMP_SIM_DEF_*`） | **7.5%**（仿真）/ 实测 25→37 无超调（末值 36.997）、37→45 阶跃 10.3% | 4~6s | 无静差；积分权限 15A 足够覆盖到 100°C |

调参要点：`ki` 别大（0.15 量级即可）；`temp_kd` 调大只会让电流更钝；**先开录制/取帧再跳目标**，否则录到的是热态段。

> 固件侧配套：默认值必须**同时预置进 VOFA 接收通道**（`Temp_Sim_Init` 末尾调 `Temp_Sim_Set_Pid/Target/Switch`），
> 否则 `Temp_Sim_Load_Params` 首拍就用清零的 RX 值覆盖默认值（上电即 kp=ki=kd=0 / sw=0 / tar=0，表现为温度停在环境温度）。

## 附录 B：故障速查

| 现象 | 原因 / 处理 |
|---|---|
| 温度停在环境温度不动 | 目标 < 环境且模型无制冷；把目标设到环境之上 |
| 温度超调巨大/不收敛 | `ki` 过大（windup）；降到 0.15 量级 |
| `no command named: TEMP_KD` | 命令名必须小写（shell 大小写敏感） |
| 下发命令没反应 | `rtt_vofa` 下命令进的是 shell；确认命令名是 shell 命令、且 `temp_sw` 等开关已打开 |
| `eide_flash` 报 `CMD_INFO failed` / `Cannot connect` | 探针被占用：先 `rtt_disconnect` 再烧 |
| RTT 连不上（DAPLink） | `pip install pyocd`、WinUSB 驱动、`rtt_dap_target` 填 `stm32h743xx`；填 `rtt_dap_elf` 定位控制块 |
| RTT 连不上（J-Link） | 装 SEGGER 软件或配 `rtt_jlink_dll` |
| 波形抖动/断 | shell 回显混入 JustFloat 流（§7） |
| 通道名对不上 | `wave_names` 与 `channels.csv` 大小写/顺序不一致 |
