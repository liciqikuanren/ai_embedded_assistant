# AI 嵌入式助手（ai_embedded_assistant）

「AI 嵌入式全流程」的**调试验证**环节：一个集**上位机调试软件 + DSH 桥插件 + Skills** 于一体的仓库。
配合 `eide-mcp-skill`（编译/下载）即可由 AI 完成 **编辑代码 → 编译 → 下载 → 调试** 的完整闭环。

- 软件本体：串口 / RTT（J-Link 或 DAPLink，Shell/波形/日志）调试 + JustFloat 波形 + CSV 录制 + 内置 MCP 服务 + AI 联动（数据推 DSH 会话）。
- 软件与固件业务无关，可接任何串口 / RTT 设备；详细用法见 [`docs/使用文档.md`](docs/使用文档.md)。

## 仓库结构

```
ai_embedded_assistant/
├── software/                 上位机软件
│   ├── code/                 源码：main.py + core/plugins/ui/tests + docs/（串口指令文档、RTT 设计）
│   │                         （cd software/code → pip install -r requirements.txt → python main.py）
│   └── dist/                 打包产物：build/（中间）、dist/（绿色版 exe）、dist-installer/（安装包）※ 不入库
├── dsh_plugin/
│   └── ai-embedded-bridge/   DSH 桥插件（Node，原 serial-bridge）：接收软件推送 → 注入指定 Agent 会话；
│                             标准 dsh bundle（dsh.bundle + cordis.patch.yml），可 dsh plugin add / 市场安装；
│                             Web UI 悬浮面板：总开关（off/chat/monitor）+ 目标会话选择 + 实时状态
├── skills/
│   └── ai-embedded-assistant/   调试验证 Skill：软件使用 + MCP 操作（串口/RTT/波形/录制）+ PID 经验
│                                    （编译/下载用独立仓库 eide-mcp-skill，见「相关仓库」自行安装）
├── docs/
│   └── 使用文档.md                  软件使用文档（安装/四种运行模式/界面/配置/FAQ/应用案例/打包）
└── README.md
```

## 分工：谁负责什么

| 环节 | 承担者 | 入口 |
|---|---|---|
| 编辑代码 | AI（当前会话） | 直接改固件工程 |
| 编译 / 重建 / 清理 | **eide-mcp-skill** | EIDE MCP（`127.0.0.1:8940/mcp`）：`check/build/rebuild/clean/reload/add-src-dir` |
| 下载 / 烧录 | **eide-mcp-skill** | `flash`（可 `--erase-all`） |
| 调试 / 验证 / 调参 | **软件 + ai-embedded-assistant** | 串口 / RTT shell / 日志 / 波形 / 录制 CSV，GUI 或 MCP |

> 给 AI 的指令约定：**编译/烧录** → 用 eide skill（独立安装的 eide-mcp-skill；会话未加载时先提示用户安装）；
> **调试/连板/看日志波形/下发命令** → 用 `ai-embedded-assistant` 操作本软件（MCP `127.0.0.1:8765/mcp`）。

## 快速开始

### 1. 软件（AI嵌入式助手）
- 源码运行：`cd software/code && pip install -r requirements.txt && python main.py`
- 绿色版 / 安装包：`software/dist/dist/AIEmbeddedAssistant/AIEmbeddedAssistant.exe`、
  `software/dist/dist-installer/AIEmbeddedAssistant-Setup.exe`
- 启动后内置 MCP：`http://127.0.0.1:8765/mcp`（Streamable HTTP）。

### 2. DSH 桥插件（ai-embedded-bridge）
一个标准 DSH bundle：`package.json` 声明 `dsh.bundle.patch`，插件行由同目录的 `cordis.patch.yml` 插入。

- 安装（装完**重启 DSH** 生效）：`dsh plugin --profile web add github:<owner>/<repo>`；
  本地源码装就先 `cd dsh_plugin/ai-embedded-bridge && pnpm pack`，再
  `dsh plugin --profile web add <绝对路径>/ai-embedded-bridge-0.1.0.tgz`。
  ⚠️ **别**直接 `dsh plugin add <本目录路径>`——pnpm 只建符号链接，Node 走真实路径，
  会落到 `$DSH_HOME/profiles/` 之外从而找不到 `@deepseek-ai/*`，启动即报 `ERR_MODULE_NOT_FOUND`
  （详见插件 README）。本包没有 `prepare`/构建脚本，不需要 `allowBuilds` 构建授权。
- 插件提供 Web UI 右下角悬浮面板（注入总开关 off/chat/monitor + 目标会话选择 + 实时状态）。
- 软件侧推送默认地址：`http://127.0.0.1:3080/plugins/ai-embedded-bridge/incoming`。
- 端点、模式与兼容性说明见 [`dsh_plugin/ai-embedded-bridge/README.md`](dsh_plugin/ai-embedded-bridge/README.md)。

### 3. Skills
- `ai-embedded-assistant`：调试验证（串口/RTT/波形/录制 + 与 eide 的接力流程）——按 `skills/ai-embedded-assistant/SKILL.md` 使用（本仓库自带）。
- `eide-mcp-skill`（编译/下载，**独立仓库，需自行安装搭配**）：`git clone git@github.com:AlexiaQAQ/eide-mcp-skill.git`，
  VSCode EIDE 扩展开启 `EIDE.MCP.Server`（端口 8940）后按其 `SKILL.md` 使用。

## 改名 / 迁移说明（原 serial-assistant 系列）

统一改名为 **ai_embedded_assistant** 家族，源工程（原 `Serial_Port_Assistant`）已同步改名：

| 原 | 现 |
|---|---|
| 软件 `Serial_Port_Assistant` / 「串口助手」 | `ai_embedded_assistant` /「AI嵌入式助手」（窗口标题与安装包已同名） |
| 配置目录 `~/.serial_assistant` | `~/.ai_embedded_assistant`（旧目录可整体改名/拷贝迁移） |
| DSH 插件 `serial-bridge`（目录 `dsh-serial-bridge`，端点 `/plugins/serial-bridge/*`） | `ai-embedded-bridge`（目录 `dsh_plugin/ai-embedded-bridge`，端点 `/plugins/ai-embedded-bridge/*`） |
| Skill `serial-assistant-mcp` | `ai-embedded-assistant` |

注意：DSH 里若装过旧插件 `serial-bridge`，需换成 `ai-embedded-bridge` 并重启 DSH；
`software/dist` 内打包产物（exe/安装包）为改名前的旧构建，重新打包后即为新名。

## 相关仓库

- `eide-mcp-skill` — 编译/下载，独立 git 仓库 `github.com/AlexiaQAQ/eide-mcp-skill`（需自行 clone 安装搭配，未随本仓库分发）
- 旧重组仓库 `serial-port-assistant`（含早期工作流文档，仅存档）
