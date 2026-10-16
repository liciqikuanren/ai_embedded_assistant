# 讨论稿：AI 调试提速与工程/代码改进

> 性质：**只读研究产出的讨论材料，未修改任何生产代码、未连接真实硬件**。文中出现的接口/字段/流程均为"建议形态"，不是已落地的改动。
> 输入：t1 仓库测绘、t2 工程与工作流评估、t3 AI 调试体验机会点、t4 代码风险审计；本稿由 synthesis 任务独立核对后整合。
> 路径约定：仓库根 = `F:\AI\Skills_Lib\ai_embedded_assistant`，`SC/` = `software/code/`。
> 核验环境（本机实测，与 t4 一致）：Python 3.14.2 / mcp 1.27.1 / uvicorn 0.32.0 / PyQt6 6.11.0 / pyserial 3.5 / pylink-square 1.7.0 / pyyaml 6.0.3。
> 核对方式：本稿作者抽查 **22 条关键论断**的 `file:line` 证据、独立复跑 2 项实测（`pytest tests`、`tests/test_debug_switch.py`）、用脚本重新统计若干数字；对不成立或夸大的结论做了修正/降级（见附录 B）。**核对过程中未改动工作区任何文件**（`git status --short` 与开工前一致）。

---

## 0. 摘要（先看这 8 条）

1. **现状**：上位机 = Qt 主线程（UI）+ 1 个后台 asyncio 线程（业务插件 + 内嵌 uvicorn/FastMCP）+ 3 类阻塞读线程（串口 / RTT / J-Link）+ 每包一个推送线程；AI 与软件之间的**唯一控制面是 MCP 工具**，而工具集**随四态运行模式动态装载**，每切一次模式就整只重启 `ai_server`（`SC/main.py:63-76`、`SC/plugins/ai_server.py:544-585,657-671`）。
2. **AI 侧的真实瓶颈不是"能不能"，而是"AI 看不见状态、只能靠猜和轮询"**：连接/复位/录制/日志缓冲/工具集都没有单一事实来源，AI 每轮要用 4~5 次工具调用凑出真相（t3 F1）。
3. **最有杠杆的改动是"只读聚合 + 把失败显性化"**，不是重构架构：`session_snapshot`、`read_log` 增量游标、诊断共享（`_friendly_open_error` 移植 + `open_port` 带诊断）、把 35 处 `except: pass` 里的关键路径改成返回 `ok:false`。
4. **日志是"每次全量重发"**：`read_log` 把 64KB 缓冲整个 `splitlines()` 后取尾部 count 行，无游标、无时间戳、无去重（`SC/plugins/rtt.py:218-230,71-72,266-268`）。
5. **波形数据的成本被低估**：抽样 CSV `log_20260824_172151.csv` = 23,751 字节 / 273 行 / 9 列 ≈ **25~40 token/行（量级估计）**；`SKILL.md:101` 推荐 20Hz+，而录制默认 1Hz（`SC/plugins/config.py:85`、`SC/plugins/float_recorder.py:355`）。50Hz×300s = 15000 行 ≈ 数十万 token——**"AI 直接读 CSV"这条路不可用，必须软件侧先算、AI 只拿结论**。
6. **异步事件对 AI 完全不可见**：只有同步请求-响应工具，没有"等条件"能力；录制到点自停、RTT 掉线、串口断线全靠 AI 轮询撞运气（`float_recorder.py:245-254`、`rtt.py:275-281`、`serial_port.py:174-182`）。**但新增的等待工具必须做成 `async def`**——现有同步工具会阻塞整个后台事件循环（`mcp` 1.27.1 对同步工具直接内联调用：`func_metadata.py:93-96`），这正是 t3 的 A2 方案与 t4 的 D1 风险的交汇点（见 §B.6）。
7. **可靠性上最该先修的两条**：串口"关闭→立即重开"竞态（重复读线程 / 服务句柄被清空，t4 已用 `loop://` 复现 8/20 与 5/10）、以及 MCP 绑定地址一旦被改成 `0.0.0.0` 就**无鉴权**暴露全部工具且 DNS-rebinding 保护整体失效（`SC/plugins/config.py:45` 可被 `config_set` 任意改写；`mcp` 只在 host ∈ {127.0.0.1, localhost, ::1} 时自动启用 `TransportSecuritySettings`，见 `server/fastmcp/server.py:177-183`）。
8. **本稿推翻了 3 条上游结论、修正了 8 条**（附录 B），其中最重要的是：**仓库里的 CSV 并没有"入库"**（`git ls-files "*.csv"` = 0，26 个文件在磁盘但被 `.gitignore:12` 忽略）、**`rtt_shell_prompt` 默认不是空串**（`config.py:74` = `"HC_dqj@root:"`，故"每条 shell 命令必然等满 3s 超时"不成立）、**`except: pass` 不是 20 处而是 35 处**（生产代码）。

---

## A. 工程现状与改进点

### A.1 现状一页图（核对后保留）

```
[主线程] Qt event loop ── UI 插件（MainWindow / SerialController / UIBridge，26 个 pyqtSignal）
[后台线程] asyncio loop（run_backend，main.py:41-90）
   ├ config → serial/transmitter/tcp_forward/justfloat/float_recorder/rtt（按 mode 装载，main.py:65-71）
   ├ 最后单独加载 ai_server（main.py:74-76），否则只能注册基础工具
   ├ uvicorn + FastMCP  http://127.0.0.1:8765/mcp（stateless_http=True）→ 40 个注册点；实测正常模式 11 个工具，vofa 模式 33/38 为静态推算（未复核，见 F-3）
   ├ float_recorder 快照任务（asyncio）+ serial 自动重连协程
   └ 同步工具在此线程内联执行 → 一次 shell_exec 可让整个后台循环停摆
[serial-reader 线程] 2ms 轮询 → run_coroutine_threadsafe(ctx.emit(...))
[rtt-reader 线程] 10ms 轮询 ch0/ch1/ch2
[ReplyHttpServer 线程] ThreadingHTTPServer（默认关闭，仅 serial/serial_vofa 模式）
[每 push 一个临时 daemon 线程] urllib POST → DSH 桥 /incoming（ai_push_enabled，默认 false）
```

数据面（设备 → 软件 → AI）：串口/RTT → 事件总线 → ① UI 接收区（毫秒时间戳）② justfloat 解析 → 录制 CSV ③ TCP/VoFA 广播 ④ 可选推送到 DSH 会话（单向注入，只有 `text` 有效）。
**关键不对称**：UI 侧每条记录有方向/时间戳/来源（`SC/ui/panels/receive.py`、`SC/ui/panels/rtt.py:228,274`），AI 侧拿到的只有顺序文本行、无时间戳、无游标。

### A.2 改进点清单（工程与工作流类）

代价口径：小 = <半天且无架构影响；中 = 1~3 天或涉及多文件一致性；大 = 需要分期/架构决策。

| 编号 | 优先级 | 问题 | 证据 | 影响 | 建议 | 代价 |
|---|---|---|---|---|---|---|
| A-1 | **P0** | `requirements.txt` 只有 4 行，缺 `uvicorn`（MCP 启动硬依赖）与 `pylink-square`（RTT），且全部 `>=` 无上限 | `SC/requirements.txt:1-4`；`SC/plugins/ai_server.py:22` 模块级 `import uvicorn`；`SC/plugins/rtt.py:17-24`；`SC/AIEmbeddedAssistant.spec:9`（点名 uvicorn/sse_starlette/pydantic_settings，**无 pylink**） | 三处文档都教用户跑 `pip install -r requirements.txt`（根 `README.md:44`、`docs/使用文档.md`、`SKILL.md:19`）→ 干净机器上装完 MCP 起不来；`-windowed` 下日志进 devnull，表现为"软件正常、AI 连不上" | 补 `uvicorn`、`pylink-square`；加上限（如 `mcp>=1.27,<2`、`PyQt6>=6.9,<7`）；README 声明 Python 版本要求 | 小 |
| A-2 | **P0** | `justfloat_autoname` 注册在 `if jf is not None:` **守卫之外**，模式隔离契约破裂，`test_debug_switch.py` 是全仓唯一红灯 | `SC/plugins/ai_server.py:366-383`（对比同文件 `:264-265` 的守卫）；`SC/tests/test_debug_switch.py:67-68,122` | ① 唯一红灯让 CI 不可用；② AI 在 `serial`/`rtt_shell` 下看到一个必然 `ok:false` 的僵尸工具；③ 文档三方不一致（`ai_server.py:3` 写"6 个工具"、`SC/README.md:91` 写 10、实测正常模式 **11**） | 二选一：把注册挪进守卫（约 2 行、最一致）；或保留"全局注册 + 运行时降级"并同步改测试与三处文档 + 补白名单断言 | 小（①②）/ 小-中（文档路线） |
| A-3 | **P0** | 文档承诺了一个**代码里不存在**的端点 `/echo`，且 `ai_reply_enabled` 默认值说反了；桥面板还残留对应 DOM/CSS | 桥只有 4 条路由 `dsh_plugin/ai-embedded-bridge/lib/index.js:216-241`（`/echo` 落到 `:239-241` 的 404）；全文 `echo` 只命中 `:301` 的 CSS；`SC/README.md:214,218,235` 宣称 `/echo`；`SC/README.md:227` 写"默认开" vs `SC/plugins/config.py:52` = `False`；桥 README 端点表只有 4 条（`dsh_plugin/ai-embedded-bridge/README.md:70-77`） | 用户按提示勾"AI 回复回写"→ 404 → 现象是"勾了没反应"，排查成本高；同时 `SC/README.md:211-213` 描述的"双向联动（默认开）"与实际默认关闭矛盾 | 明确取舍：要么恢复 `/echo` + `handleEcho`，要么删残留 DOM/CSS 与 3 处文档并把 `ai_reply_enabled` 标 deprecated；端点表建立单一来源 | 小（删）/ 中（恢复） |
| A-4 | **P0** | 静默吞异常：生产代码共 **35 处** `except …: pass`（另 3 处在 tests），其中写路径失败会让"操作失败"变成"什么都没发生" | 实测统计：`tcp_forward.py` 8、`ai_server.py` 7、`float_recorder.py` 5、`rtt.py` 3、`main.py` 2、`serial_port.py` 2、`history.py` 2、`justfloat.py` 2、`recorder.py` 2、`config.py` 1、`logger.py` 1；关键点：`SC/plugins/config.py:127-135`（写盘失败无声）、`SC/plugins/float_recorder.py:290-294,296-303`（写表头/写行失败被吞）、`SC/main.py:85-88`（卸载异常被吞，端口 10048 只能猜） | **直接打击本工程的核心价值**：AI 只看到 `{"ok": true}`，于是在错误前提上继续推理（"改了参数重启又回去"、"在录制"实际 0 行） | 关键路径（config.save、float_recorder 写行/写表头、serial 打开）改为"记日志 + 返回 `ok:false`/错误字段"；非关键路径至少 `log()`；`_rows` 只在真正写入成功后累加 | 小（逐点，约 35 处） |
| A-5 | **P1** | 没有统一测试入口：`pytest tests` 6 红 24 绿（全是 async 用例不被支持），其余脚本需逐个手跑（t2 实测 8/9 绿）；无 `pytest.ini`/`conftest.py`/CI | 本稿复跑：`python -m pytest tests -q -p no:cacheprovider` → **6 failed, 24 passed**，失败全是 `async def functions are not natively supported`；`python tests/test_debug_switch.py` → **exit=1**（`AssertionError: [... 'justfloat_autoname' ...]`）；仓库无 pytest 配置 | 同一份测试两种跑法两种结论，团队无法用一个命令判"现在是否健康"，A-2 这类契约破坏长期无人发现 | 加 `pytest.ini`（`pythonpath=.` + `asyncio_mode=auto`）并声明 `pytest-asyncio`；保留 `main()` 手跑入口；再上 Windows CI + `AUTO_QUIT_MS` 冒烟（`SC/main.py:118-121` 已有钩子） | 小 |
| A-6 | **P1** | `SC/README.md` 整节停在"正常/调试"两态时代，且 5 处 `config.json` 中有 **3 处错**、1 处指向另一台机器的绝对路径、1 处引用不存在的 CSV 示例 | `SC/README.md:84-103` 整节 + `:86` `debug_mode`（实现是四态 `mode`，`SC/plugins/config.py:58-61` 明说已取代）；`:150,196,220` 写当前配置为 `config.json`（实际 `config.yaml`，`config.py:101`）；**注意 :64 与 :76 提到 `config.json` 是正确的**（旧版回退文件确实存在，`config.py:104-105,114-115`）；`:3` = `F:\Python_Floder\...` 绝对路径；`:130` 引用 `csv_floder/log_20000103_232730.csv`（不存在）；`:165` 写 `get_status` 含 `debug_mode`（实际返回 `mode`，`SC/plugins/ai_server.py:108-119`）；`:91` 工具数 10 | 这是离开发者最近、最有权威感的文档，AI 会照抄：按 `:86` 改 `debug_mode` 改了没用、按 `:196` 改 `config.json` 改了个空文件 | 84-103 重写为四态 `mode` 表（内容可复用 `SKILL.md:42-50`），三处 `config.json` → `config.yaml`，删绝对路径，示例换成真实文件，工具清单补 `justfloat_autoname` 并统一工具数 | 小-中 |
| A-7 | **P1** | 无 `__version__`；仓库内安装包/绿色版是 2026-09-15 的旧构建，而最后一条提交 `27937a9` 未重新打包；文档还在教用户"对齐请用源码版" | `SC/AIEmbeddedAssistant.iss:5` 是唯一版本号（0.2.0）；`SC/**/*.py` 无 `__version__`；`SKILL.md:60-61`、根 `README.md:78-79` | 用户拿到的产物与 `main` 不是同一份，报 bug 无法定位版本；文档等于劝人别用安装包 | 加 `SC/core/version.py` 并由打包脚本注入 `.iss`；UI 标题/设置页显示版本；README 加"软件版本 ↔ 桥插件版本"对照表；重新打包后删旧警告 | 小-中 |
| A-8 | **P1** | 日志服务过薄：无级别、无时间戳、无文件、异常无堆栈；`-windowed` 下 stdout/stderr 被重定向到 devnull | `SC/plugins/logger.py:13-18`（`print` + emit）；`SC/main.py:20-23` | 安装版用户没有任何日志可交，AI 排查上位机自身问题只能靠猜；`docs/使用文档.md` 把退出时 uvicorn 堆栈解释为"无害噪音"正说明缺受控日志通道 | `log(level, msg)` + 滚动文件 `~/.ai_embedded_assistant/logs/app.log`；冻结版把 devnull 换成文件流；给 MCP 加 `app_log(tail,grep,level)`（与设备侧 `read_log` 区分） | 中 |
| A-9 | **P1** | 配置只有"键名白名单"，无类型/取值域校验；`csv_dir` 默认值三处说法不一 | `SC/plugins/config.py:16-86`（DEFAULTS 是唯一 schema）、`:117`（只做 `k in DEFAULTS`）、`SC/plugins/ai_server.py:228-241`（`config_set` 任意值直写并立即落盘）；`config.py:83` `csv_dir:""` vs `float_recorder.py:353` `or default_csv_dir()` vs `SKILL.md:21` "软件运行目录/csv_floder/" vs `SC/README.md:128` | 手改 YAML 写错 `mode` → 热切换静默不动；AI 无法从 `config_status` 知道合法值域；录制后按文档找 CSV 找不到 | DEFAULTS 升级为带类型/枚举/范围的表（打包已带 `pydantic_settings`），`load()` 校验并记日志；`config_status` 返回键与值域；统一 `csv_dir` 三处语义 | 中 |
| A-10 | **P1** | 打包相关只有一处做 frozen 判定，其余靠"加载顺序 + `__file__` 相对路径"隐式成立；spec 的 hiddenimports 与实际运行依赖不同步 | `SC/plugins/float_recorder.py:31-35`；`SC/main.py:25-38`；`SC/AIEmbeddedAssistant.spec:8,9` | 任何一处改用 `os.getcwd()/sys.executable` 就会在绿色版静默失效；`pylink` 未进 hiddenimports → **冻结版 RTT 能否用有待验证** | 抽 `core/paths.py`（`app_dir()/data_dir()`）统一 frozen 判定；spec 补 `pylink` 条件分支；用 `AUTO_QUIT_MS` 做绿色版冒烟 | 中 |
| A-11 | P2 | `plugins/codec.py` 是无人使用的死层（唯一调用点是 transmitter 注入），其余模块直接 `from core.codec import ...`；`core/` 与 `plugins/` 无职责文字契约（`core/theme.py` 直接 import PyQt6） | `SC/plugins/codec.py:3-11`；`SC/ui/controller.py:12`、`SC/plugins/recorder.py:7`、`SC/plugins/ai_server.py:24-25`；`SC/core/theme.py:9-11` | 依赖方向变成 plugins/ui → core 直连；`core/` 若被当成"纯库"会踩 Qt 依赖 | 二选一并写进 README（删死层或统一走服务） | 小 |
| A-12 | P2 | 工具清单/模式表在 4 份文档重复，无权威源声明，已实际漂移 | `SC/README.md:158-194`、`SKILL.md:71-83`、`docs/使用文档.md:46-55`、根 `README.md:36-39` | 改一处要同步四处；AI onboarding 不知以谁为准 | 定权威源（模式/工具面 → `SKILL.md`），其余只放链接 + 摘要 | 小 |
| A-13 | P2 | 跨仓库依赖 `eide-mcp-skill` + VSCode EIDE MCP(8940) 未随仓库分发，缺一键体检 | 根 `README.md:64-65`、`SKILL.md:34-36,104-111` | AI 无法独立完成"编辑→编译→下载"，这是 B 类里最高频的断点 | README 加"最小闭环前置清单"（DSH + 桥插件 + 软件 MCP + eide-mcp-skill）+ `scripts/check-env.ps1` 体检进程/端口 | 小 |
| A-14 | P2 | 桥插件安装坑已知但仍在（直接 add 目录 → symlink realpath 越界 → `ERR_MODULE_NOT_FOUND`）；`package.json` 的 peer 预发布范围需人工维护 | `dsh_plugin/ai-embedded-bridge/package.json:40-41`；桥 README 安装章节 | 新用户必然踩一次一次性失败，错误信息看起来像插件坏了 | 固化 `scripts/install-bridge.ps1`（pack→add→提示重启）；peer 范围加自动校验 | 小-中 |
| A-15 | P2 | 仓库卫生：`.gitignore` 规则是后加的，磁盘上 26 个 CSV（916,624 字节）**未被 git 跟踪**；文档引用不存在的示例；ruff 基线 14 errors 且无配置 | 实测 `git ls-files "*.csv"` = **0**，`git check-ignore` 命中 `.gitignore:12`；`SC/csv_floder/` 26 个文件；`SC/README.md:130`；`ruff check .` = 14（7 F841 / 5 E402 / 2 F401，t2 实测） | 文档指向不存在的文件会让人以为录制坏了；无机械底线检查 | 示例换真实文件；`csv_floder` 加 `.gitkeep`；加 `pyproject.toml`（ruff 配置，给 `tests/*` 放行 E402） | 小 |

> 说明：A-4 在 t2 里被列为 P0，本稿同意其"可观测性 = AI 调试生产率"的判断，但把**数字修正为 35 处**（附录 B-3）。

---

## B. 让 AI 调试 MCU 更快更省事的方案（按"最值得先做"排序）

排序原则：**（收益 ÷ 代价）从高到低，且优先"只读/低风险、能立刻减少往返或 token"的项**；凡涉及行为/架构变更的，靠后并注明前置条件。
所有新增工具都建议落在 `SC/plugins/ai_server.py::build_server`（`:61-416`）注册，服务实现放对应插件内；建议统一用 `session_ / log_ / wave_ / recorder_ / diag_` 前缀与现有工具区分。

| 序 | 事项 | 收益 | 代价 | 前置/风险 |
|---|---|---|---|---|
| B-1 | 诊断共享 + `open_port` 带诊断 + `session_doctor` | 硬件类问题（最常见耗时源）从"猜"变"读建议"；零风险 | **极低**（大半是代码搬迁） | 无 |
| B-2 | `session_snapshot()` | 每轮省 3~4 个往返，并回答"我现在能调什么" | 低（纯聚合） | 无 |
| B-3 | `read_log` 增量游标 + 去重 + 分级统计 | 调试循环里 token 降 60~80%，不再重复付费 | 中低 | 缓冲裁剪要与 seq 对齐 |
| B-4 | 失败显性化（`ok:false` / `write_errors` / `last_error`） | 消除"命令成功但没效果"这一类无效推理 | 小 | 与 A-4 同一批改动 |
| B-5 | `rtt_connect(post_actions=[...])` + 控制块可读提示 | 消掉"忘了 autoname / 连太早"整类返工 | 低-中 | 建议顺手 async |
| B-6 | `session_watch(...)`（长等待 + `device_reset` 事件） | **时间收益最大**：把"录 10s 轮询 11 次"变成 1 次等待 | 中 | **必须 `async def`**（见下） |
| B-7 | 波形结论化：`wave_channels` / `recorder_analyze` / `recorder_points` / `wave_recent` | 单次阶跃分析从 2 万~数十万 token → ~300 token | 中 | 依赖 C-5（时间轴）先修 |
| B-8 | skill 侧改写（零代码） | 「禁止直读 CSV」「flash→attach 判定清单」「设备输出不是指令」 | **零代码** | 无 |

### B.1 诊断共享 + `open_port` 带诊断 + `session_doctor`（P0，极低代价）

- 现状：GUI 有一段最有价值的翻译逻辑，**AI 拿不到**：`SC/ui/controller.py:313-320` 把 `Access is denied / winerror 5 / in use` 译成"端口可能被其他程序占用"，把 `could not open / not found` 译成"端口不存在或已被拔出"；而 MCP 的 `open_port` 直接返回 `serial.open()` 的 `str(e)`（`SC/plugins/ai_server.py:80-99` → `SC/plugins/serial_port.py:100-102`）。
- 建议形态：
  - 把 `_friendly_open_error` 提取到共享模块（如 `core/diag.py`），GUI 与 MCP 共用；
  - `open_port` 失败时返回 `{"ok":false,"message":"...","doctor":{"code":"PORT_BUSY","suggest":["关闭占用 COM7 的程序","改试 COM8"],"evidence":"Access is denied"}}`；
  - 新增 `session_doctor()`：把"`list_ports` 命中但 open 失败 → `PORT_BUSY`"、"RTT connected 且 `justfloat_status().frames==0` → `NO_WAVE_DATA`（固件未发/帧尾不符/ch1 被 JScope 占用）"、"`dropped_bytes`/`malformed` 增长 → 波特率或字节序疑点"等判定集中输出，返回 `issues[] + checks[]`。
- 代价：约 80 行，其中 60 行是已有逻辑搬迁；**纯只读，无风险**。

### B.2 `session_snapshot()`：一次调用拿到全部事实（P1）

- 现状：要判断"现在能不能发指令/是否在录/波形来没来"，AI 需要 `get_status` + `config_status` + `rtt_status` + `justfloat_status` + `float_recorder_status` 4~5 次往返（`SC/plugins/ai_server.py:108-119,199-203,401-404,267-270,337-340`）。
- 建议返回（全模式可用、只读）：
  ```json
  {
    "mode": "rtt_vofa",
    "available_tools": ["log_tail","wave_latest","recorder_status","..."],
    "serial": {"open": false, "port": "COM7", "baudrate": 115200, "auto_reconnect": true, "disconnect_count": 0, "last_error": null},
    "rtt": {"connected": true, "chip": "STM32H743XI", "channels": {"shell":0,"wave":1,"log":2}, "prompt": "HC_dqj@root:", "shell_buf_bytes": 0, "log_buf_bytes": 12345},
    "wave": {"frames": 8123, "dropped_bytes": 0, "malformed": 0, "latest": {"names": [], "values": []}},
    "recorder": {"state": "recording", "rows": 812, "remaining_s": 121.4, "sample_hz": 50, "path": "..."},
    "device": {"reset_count": 2, "last_reset_at": "...", "last_boot_line": "..."},
    "server": {"host": "127.0.0.1", "port": 8765, "tools_version": "..."}
  }
  ```
- `available_tools` 两种实现：① 注册时维护一份显式名单（**推荐**，不依赖私有 API）；② 读 `FastMCP._tool_manager.list_tools()`（本机 mcp 1.27.1 存在，`server/fastmcp/server.py:315-317` 与 `tools/tool_manager.py:41`，但属私有属性，版本升级可能变）。
- 代价：约 60 行只读聚合；`device.reset_count` 依赖 B.6 的复位检测。

### B.3 `read_log` 增量游标 + 去重 + 分级统计（P0/P1）

- 现状：`SC/plugins/rtt.py:218-230` 每次把整个 `_log_buf` decode + `strip_ansi` + `splitlines`，再取尾部 count 行；缓冲 64KB（`:71-72`）且裁剪时不计数（`:266-268`）；行内无到达时间。多次调用重复返回同一批行。
- 建议形态（兼容旧签名）：
  ```
  read_log(count=500, grep="", level="", cursor=None, limit=200, dedupe=True, include_ts=False)
  → {"lines":[...], "next_cursor": 48213, "dropped_from_cursor": false, "timed_out": false,
     "stats": {"by_level": {"ERROR":3,"INFO":181}, "total_lines":512, "returned":200, "deduped":312}}
  ```
  语义：`cursor=None` → 从尾部往前 limit 行（保持兼容）；`cursor=N` → 只返回 seq > N 的新行并回 `next_cursor`；`include_ts=True` 时在行首附主机接收时间（需在 `rtt.py:266` 处同时记录 `time.monotonic()`）。
- 实现要点：给 `_log_buf` 加单调 seq，**裁剪时同步推进 `first_seq`**，否则 `dropped_from_cursor` 会算错；`dedupe` 用"连续重复折叠为 1 行 + ×N"。
- 另建议一个 `log_summary(window="since_last_call")`：只回 `{"ERROR":3,"WARN":12,"error_templates":[{"tpl":"[ERROR] i2c timeout dev=%d","count":3,"last":"..."}]}`，让 AI 判断"这轮有没有新问题"不必拉 500 行。
- 代价：约 60~75 行 + 缓冲结构微调；风险低（注意 UTF-8 边界与裁剪对齐）。

### B.4 失败显性化：把"静默失败"变成结构化失败（P0）

- 现状（见 A-4）：`SC/plugins/float_recorder.py:290-303` 写表头/写行失败被吞，而 `:277` 的 `_rows += 1` 在锁内**无条件**累加 → AI 侧 `float_recorder_status` 仍报 recording、行数还在涨，实际磁盘 0 行；`SC/plugins/config.py:127-135` 写盘失败无声。
- 建议形态：写入路径记录 `write_errors / last_error / last_error_at`，连续失败 N 次自动停止并置 `state="error"`；`_rows` 只在 `writerow` 成功后才 +1；`config_set/config_save` 返回 `ok:false + reason` 并在 UI 提示。
- 收益：这是"AI 只能在 `{"ok":true}` 上继续推理"的根因修复，属于**所有其它 AI 提速方案的地基**——没有它，B-1/B-2 的诊断结论也会建立在假状态上。

### B.5 `rtt_connect(post_actions=[...])` + 控制块可读提示（P1）

- 现状：MCP 的 `rtt_connect` 只返回 `{"ok":true,"message":"已连接"}`（`SC/plugins/ai_server.py:389-393`），而 GUI 在连接后会自动做通道名自动发现（`SC/ui/controller.py:364-366,584-586`；MCP 侧**没有**）。控制块找不到时只有 pylink 原文（`SC/plugins/rtt.py:136-147` 只在缺 DLL 时包装）。
- 建议形态：连接成功后自动执行 `justfloat_autoname`（等价 GUI），返回 `{"ok":true,"chip":"...","channels":{...},"autoname":{"ok":true,"names":[...]},"boot_log":[...],"verify":{"wave_frames_seen":12,"log_lines_seen":3}}`；`rtt_start` 失败时补一条可读提示："未定位到 RTT 控制块：固件可能尚未执行 `SEGGER_RTT_Init`（烧录后等 200~500ms 再连），或在 `rtt_control_block_addr` 显式指定"。
- 收益：消掉"连上 → 忘了 autoname → 通道名是 Ch0.. → 录制表头全错 → 重录"整类返工。代价：约 40 行。

### B.6 `session_watch(...)`：AI 主动等（时间收益最大，**必须 async**）

- 现状：AI 只有同步请求-响应工具；录制到点自停（`SC/plugins/float_recorder.py:245-254`）、RTT 掉线（`rtt.py:275-281`）、串口断线/重连（`serial_port.py:174-209`）都不可见，只能轮询（skill 里就是这么写的）。
- 建议形态：
  ```
  async def session_watch(timeout_s=15, kinds=["serial_disconnected","rtt_disconnected",
                           "recorder_state","mode_changed","device_reset","log_lines"])
  → {"events":[{"seq":17,"ts":"...","kind":"recorder_state","data":{...}}], "timed_out": true}
  ```
  实现：新增 `deque(maxlen=200)` + 单调 seq 的事件环，在 `ctx.on(...)` 上收集**已有事件**（`serial_disconnected/reconnecting/reconnect_failed`、`rtt_disconnected`、`float_recorder_status`、`ai_server_ready`），工具内 `asyncio.Event` + `wait_for` 等待；`device_reset` 由 justfloat 比较 `timestamp` 通道回绕/跳变（`SC/docs/串口指令文档.md` 明确 timestamp 从 0 累计、每帧 +0.01、作 X 轴）。
- **关键约束（t3 方案与 t4 风险的冲突点，本稿的裁决）**：现有 MCP 工具全部是同步 `def`，mcp 1.27.1 对同步工具**直接内联调用**（`func_metadata.py:93-96`），因此一次 `shell_exec` 会让整个后台循环停摆：TCP 广播停、录制采样停、其它 MCP 请求串行化、DSH 推送停（`SC/plugins/ai_server.py:407-409` 且 `timeout` 无上限；`SC/plugins/rtt.py:207-216` 用 `time.sleep(0.02)` 轮询）。所以：
  1. `session_watch` 必须是 `async def` + `await`，不得用 `time.sleep`；
  2. 建议把 `shell_exec`、`justfloat_autoname` 一并改成 `async def`（提示符由 `rtt_shell_rx` 置 Event，工具 `await`），并给 `timeout` 加上限（clamp ≤10s）；
  3. 等待上限建议 **10~20s**（不是 60s），并在超时/断开时立即返回，避免 AI 卡死；DSH MCP 客户端的请求超时值**待验证**（见 F-7）。
- 代价：事件缓冲 + 等待原语约 80 行；复位检测约 20 行。风险：中（改的是工具的执行模型，需与 C-2 一起做）。

### B.7 波形结论化：让 AI 拿结论而不是拿数据

- 现状：工具面没有任何统计/特征能力（`SC/plugins/ai_server.py:314-363` 只有 start/pause/resume/stop/status/list/set_*），而 CSV 抽样约 25~40 token/行，`SKILL.md:116` 还写着"优先录制 CSV + 读文件"。
- 建议形态（先做前两个）：
  1. `wave_channels()`：返回"通道 → 单位/含义/量程"（`SC/plugins/commands.py` 已有 `(name, unit)`；`SKILL.md` 附录已警告 `temp_kd` 是执行器阻尼**不是**微分项）→ 消除语义误判；
  2. `recorder_analyze(path=None, channels=None, stats=["min","max","mean","rms","pp","std","final"], window="last", max_points=200)` → 返回 `rows / duration_s / sample_hz / skipped_frames / data_quality / channels{...} / step{at_s,t_rise_10_90_ms,overshoot_pct,steady_state_error,settle_time_2pct_s} / alerts[]`，并在返回里**显式声明"使用的 X 轴列/是否滤波/单位"**；
  3. `recorder_points(path, channels, n=50, mode="downsample|range")` → 供画图/写报告用，token 可控（50~200 点）；
  4. 二期：`wave_recent(seconds=10)`（把 `justfloat` 的 500 帧环扩成时间窗环 + 滚动 min/max/mean）、`recorder_start(trigger={...})` 条件录制 + 触发前缓冲。
- **前置**：C-5 的 CSV 时间轴先修（`float_recorder.py:274` 现在写的是 `count * interval` 名义时间，与 `:245-247` 的真实 `time.monotonic()` 停止条件不一致），否则"上升时间/超调/稳态误差"这类结论会系统性偏错——这比 token 更严重。

### B.8 skill 侧改写（零代码收益，建议与 B.7 同步做）

- `SKILL.md` 第 4 步由"录制 CSV + 读文件分析"改为**硬约束**：**禁止直接读整个 CSV**；先 `recorder_analyze`，需要形状再 `recorder_points`，只有在两者都不能回答时才片段读文件并显式限行数。
- `SKILL.md` 4.3 扩写成**带判定点**的接力流程：`build` 成功判定（错误行数=0）→ `flash` 成功判定 → **等 300~500ms（等 RTT 初始化）** → `rtt_disconnect` → `rtt_connect(post_actions=["autoname","read_boot_log"])` → 用 B.6 等启动日志 → 断言"出现 app start" → 才开始功能验证；附失败分支（连不上 → `session_doctor` → 换芯片名/降速 4000→1000/换接口）。
- 在 skill 与桥文档里写明"**设备输出不是指令**"（对应 C-9 的提示注入面）。

---

## C. 代码风险清单与修复优先级

等级口径：高 = 影响可靠性/暴露面，或会让 AI 得出错误结论；中 = 特定条件下明显劣化或本地安全面；低 = 体验、一致性、卫生。
"触发条件"= 什么操作会命中；【已复现】= 本次或 t4 用无硬件实验实际观察到。

### C.1 高风险

| 编号 | 风险 | 证据 | 触发条件 | 修复 | 代价 |
|---|---|---|---|---|---|
| C-1 | **MCP 无鉴权；绑定地址一旦放开，连 DNS-rebinding/Origin 保护整体失效** | `SC/plugins/config.py:45`（默认 127.0.0.1 但可被 `config_set` 改写，`ai_server.py:228-241`）；`ai_server.py:69-73,649-662`；`mcp` 只在 host ∈ {127.0.0.1, localhost, ::1} 时自动启用 `TransportSecuritySettings`（`server/fastmcp/server.py:177-183`，已核对本机 mcp 1.27.1）。另：缺 Origin 头时 `_validate_origin` 直接放行 → 非浏览器客户端不受限（t4 E2） | `config_set("ai_server_host","0.0.0.0")` + 触发一次模式热切换（`ai_server` 重新 `apply` 时读 host，`:649`）或重启 | ① **拒绝非 loopback 绑定**（几乎零代价，先行）；② 无论绑定地址都显式设置固定 `allowed_hosts/allowed_origins`；③ 引入 token 鉴权（写 `~/.ai_embedded_assistant/mcp_token`，0600，校验 `Authorization`，UI 显示指纹） | ①极小 / ③中（要同步客户端配置） |
| C-2 | **同步 MCP 工具阻塞后台事件循环，且 `shell_exec`/`justfloat_autoname` 的 timeout 无上限** | 所有工具是同步 `def`（`ai_server.py:75-415`）；`mcp` 对同步工具内联调用（`func_metadata.py:93-96`）；`rtt.py:207-216`（`time.sleep(0.02)` 轮询）；`ai_server.py:407`（`timeout: float = 3.0` 无 clamp）；同类：`ai_server.py:366-383` → `justfloat.py` 的查询窗口最多阻塞 timeout 秒 | 一次 `shell_exec`（尤其 AI 传大 timeout）；波形模式下的 `justfloat_autoname` | 工具改 `async def` + `await`；shell 等待改事件驱动（提示符置 Event + `wait_for`）；timeout 加上限（≤10s）并 clamp | 中 |
| C-3 | **串口"关闭→立即重开"竞态：重复读线程 / 服务句柄被清空** | `SC/plugins/serial_port.py:104`（重开时 `clear()` 共享停止标志）、`:105-108`（起新线程）、`:111-123`（`close()` 不 join）、`:154`（`while not self._reader_stop.is_set()`）、`:87`（`_closing=False`）+ `:174-182`（旧线程退出时清 `_serial` 并触发虚假 `serial_disconnected` + 重连）。【已复现】t4：`loop://` + close/open 20 次 → 8 次留下 2 个 `serial-reader`；慢速 open 10 次 → 5 次 `_serial is None` | MCP `open_port`（已开则先 `close()` 再 `open()`，`ai_server.py:95-97`）；UI 快速切端口；`_reconnect_loop` 与手动 open 并发 | 用"代际令牌"替代共享 Event（每次 open 生成 `gen`，读线程携带自己的 `serial` 与 `gen`，代际不符即退出）；`close()` 加 `join(timeout=0.5)`；`_open_internal` 不清共享标志；`_on_reader_exit` 先校验"是否仍为当前代际"再改状态 | 中（改动集中在一个类） |
| C-4 | **每条接收数据新建一个推送线程（线程爆炸）** | `ai_server.py:430-442`（`threading.Thread(target=_post, daemon=True).start()`），调用点 `:445-460`，挂在 `serial_data_received` 与 `rtt_shell_rx` 上；失败静默（`:439-440`）；超时 2s | `ai_push_enabled=true`（默认 false，`config.py:48`；`serial_vofa` 被 UI 强制禁用）且桥不可达/慢时：串口 2ms 轮询即一段 → 每秒数百线程 | 单工作线程 + `queue.Queue(maxsize=N)` + 合批/丢旧；或复用 `requests.Session`/连接池 + 背压 | 小-中 |

### C.2 中风险

| 编号 | 风险 | 证据 / 触发条件 | 修复 | 代价 |
|---|---|---|---|---|
| C-5 | **CSV 时间轴是名义时间，AI 的时序结论会系统性偏错** | `float_recorder.py:274`（`t = _count * interval`）vs `:245-247`（停止条件用真实 `time.monotonic()`）；`:278` 不补偿漂移；触发：任何一次循环阻塞（含 C-2） | 写真实 `monotonic - _started_at`；CSV/status 增加 `actual_sample_hz` 与 `missed_samples`；`recorder_analyze` 显式声明 X 轴列 | 小 |
| C-6 | **录制/配置写在失败时静默，AI 拿到残缺数据却不知情** | `float_recorder.py:290-294,296-303`（`except: pass`）+ `:277`（无条件 `_rows += 1`）；`config.py:127-135`；触发：磁盘满/目录只读/被 AV 锁/编码问题 | 见 B.4：`write_errors/last_error` + 连续失败自动停 + `_rows` 成功才累加 | 小 |
| C-7 | **RTT 读线程异常退出不释放 J-Link 句柄** | `rtt.py:275-281`（只置 `_connected=False; _jlink=None`）vs `disconnect()`（`:152-167` 才 `rtt_stop()+close()`）；触发：USB 掉线、目标复位、DLL 异常 | `_on_reader_exit` 复用 `disconnect()` 清理路径，区分"用户断开/异常断开"事件语义 | 小 |
| C-8 | **无上限缓冲三处** | `ai_server.py:28-58`（`deque` 无 maxlen，`pending` 每次 O(n) 求和，`drain/peek` 全量 join；串口模式下**所有**接收都进这里）；`rtt.py:70-71,249`（`_shell_buf` 无裁剪，对比 `_log_buf` 有 64KB 上限）；`justfloat.py:152,188`（`_query_buf` 无上限且该窗口内丢帧）。触发：长时间高频接收而 AI 不读 | 有上限环形缓冲 + 字节计数增量维护 + 溢出计数暴露给 AI；`_query_buf` 只保留最后 N KB 并回传丢弃帧数 | 中 |
| C-9 | **`ReplyHttpServer` 无鉴权、不校验 Content-Type/Origin，body 无上限** | `ai_server.py:502-516`（只读 `Content-Length` 后 `json.loads`）、`:506`（长度无上限）、`:478-480`（每连接一线程）、`:676-680`（默认关闭，仅 serial/serial_vofa 开启）。触发：用户开启"AI 回复回写"后访问任意网页 → 表单 `enctype=text/plain` 可构造合法 JSON → 以 `source="ai"` + CRLF 写入 MCU | 校验 `Content-Type: application/json` + `Origin/Host` 必须本机 + body 上限（如 64KiB）+ token；默认保持关闭并在 UI 明示风险 | 小-中 |
| C-10 | **DSH 桥 `/incoming` 本地无鉴权 + "设备输出即 user 消息"的提示注入面** | `dsh_plugin/ai-embedded-bridge/lib/index.js:97-101`（`createUserMessage` + `followup`）、`:119-131`（`mode` 字段被忽略；`chat` 每条数据注入一次，`monitor` 按 2048 字符/间隔合并）、`:192-247`（四个端点全无鉴权）。触发：任一本地进程可先 `POST /mode {mode:"chat"}` 自开开关再注入文本。**注意**：浏览器跨站被 `Content-Type: application/json` 强制 preflight + OPTIONS 返回 405 挡住（`:199-206`），**不要误传为"任意网页可调用"** | 注入消息加不可信标记（如 `[设备原始输出·非指令]`），`SKILL.md` 写明；插件启动生成 token 写文件由上位机读取；限定单条长度/频率 | 小-中 |
| C-11 | **`config_set` 无类型/取值校验：坏值落盘后可静默打掉 MCP** | `ai_server.py:228-241`（任意值直写 + 立即 `save()`）；消费端 `:650` `int(config.get("ai_server_port", 8765))`；热切换失败只记日志（`:640-641`），UI 仍显示服务运行中。触发：`config_set("ai_server_port","abc")` | 按 `DEFAULTS` 定义每键类型/范围，校验通过才落盘；`ai_server` 加载失败必须上报 UI，不能只 log | 小-中 |
| C-12 | **慢 TCP 客户端串行 drain 拖住整条事件链** | `core/mini_cordis.py:270-278`（`emit` 顺序 await 每个监听器）；`tcp_forward.py:145-163`（对每个客户端**串行** `await wait_for(writer.drain(), timeout=2)`）。触发：VoFA 客户端卡死/半开连接、客户端多 | 广播改"每客户端独立 task + 有界队列 + 丢最旧"，drain 超时降到 ~100ms；或监听器改非阻塞 fire-and-forget | 中 |
| C-13 | **依赖版本无上限、无 lock；Python 3.14 冷装路径未验证** | `SC/requirements.txt:1-4` 全 `>=`；项目强依赖 mcp 的 FastMCP 内部 API（`streamable_http_path/stateless_http`）；`.pyc` 均为 `cpython-314` | 加上限 + `requirements.lock`/`pyproject`；README 声明 Python 版本；`AUTO_QUIT_MS` 冒烟进 CI | 小-中 |

### C.3 低风险

| 编号 | 风险 | 证据 | 修复 |
|---|---|---|---|
| C-14 | 配置键静默丢弃 / 面板设置不持久化 | `SC/ui/panels/rtt.py:319-322,327-332` 写 `rtt_shell_show/rtt_log_show/rtt_wrap/rtt_scroll`，**4 个键都不在** `config.py:DEFAULTS`（`:16-86`），被 `:117` 过滤；`justfloat` 面板无 `settings_dict` | 补 DEFAULTS，或"未知键记警告"而非静默丢弃 |
| C-15 | `read_frames(limit<=0, clear=True)` 不清空，与 docstring 不一致 | `SC/plugins/justfloat.py:118-127`（`clear=True` 且 `limit<=0` 直接返回不 pop）；docstring `ai_server.py:285-288` 写"默认读后清空" | 统一语义或在 docstring 写明 |
| C-16 | 协议歧义/文本化的可见性不足 | `justfloat.py:68-71`（若采样数据里出现连续两个 `+inf` 会被误判图片帧吞 28 字节）、`:103-106`（超 64KB 从头部丢，可能从帧中间开始）；`core/codec.py:18-23`（`errors="replace"`，二进制流文本化变 U+FFFD） | 统计"误判图片帧/被截断/单帧通道数突变"次数并暴露；非文本比例高时自动切 hex 并提示 |
| C-17 | UI 线程阻塞与无谓落盘 | `ui/controller.py:348-366`（`rtt.connect()` 在 `:357`，同步执行 `pylink.JLink().open/connect/rtt_start`，数百 ms~数秒且持锁）、`:281-285`（枚举 COM）；`:554-571`（打开历史）→ `plugins/recorder.py:68-69`（`read_bytes()` **先整读再截断**）；`ui/panels/send.py:101-105` → `controller.py:617-631` → `config.py:183-185`（**每字符一次全量 YAML 落盘**）；`ui/panels/*.py` 全文无 `setMaximumBlockCount`（本稿已 grep 确认）；主题切换触发 5000 行富文本重建 | 连接/断开放工作线程 + 按钮置忙；大文件流式限量读；`QTimer` 去抖 300~500ms；`setMaximumBlockCount` |
| C-18 | 受限的任意路径读/写、推送地址可改、敏感数据、插件目录可执行 | `ai_server.py:205-226`（`config_save/load` 任意路径）、`:348-350`（`float_recorder_set_dir`）；`config.py:50` + `ai_server.py:426-441`（`urllib.urlopen` 直连可改 URL 含 `file://`）；仓库样例 CSV + `history.json`；`core/mini_cordis.py:456-478`（`scan_plugins` 动态 exec 目录内所有 `*.py`）+ `spec:8`（plugins 随产物分发） | 路径规范化 + 白名单（拒绝 UNC/系统目录/`..`）；`ai_push_url` 限 http(s)+本机白名单；文档声明数据敏感性 + 清理入口；插件目录哈希/白名单 |
| C-19 | 打包与退出路径 | `spec:28-29,42-43`（`upx=True`、`console=False` → 未捕获异常只留 devnull）；`main.py:82-90`（finally 里调用**同步** `unload_plugin`，异步 teardown 被 `create_task` 入队后紧随 `asyncio.run` 收尾 → uvicorn 优雅停机可能不执行，对照 `ai_server.py:695-718`）；`main.py:126`（`backend.join(timeout=3)` 超时后强退）；`SC/AIEmbeddedAssistant.iss:8` 的 `MyAppId` 转义后形如 `{...-AIEmbeddedAssistant}`，**疑似不是合法 GUID**（待验证） | `upx=False` 或排除 Qt DLL；崩溃日志写文件；finally 改 `await unload_plugin_async`；ISCC 实测后再定 AppId |

### C.4 明确"未发现问题"的部分（避免过度推断，沿用 t4）

- 跨线程 UI 更新路径本身安全：后台线程只 `pyqtSignal.emit`，Qt 队列投递到主线程；未发现直接跨线程操作 QWidget（除一条当前不可达的卸载路径）。
- 录制文件句柄在正常/异常路径都由 teardown 关闭；问题是"静默失败与时间语义"，不是句柄泄漏。
- `ConfigStore` 用 `yaml.safe_load`，不构成反序列化 RCE；`hex_to_bytes` 有字符/长度校验。
- FastMCP 在默认 `127.0.0.1` 下**自带** DNS-rebinding/Origin 保护——这点是好消息，但被 C-1 的配置开关绕过。

---

## D. 最值得先做的 5 件事

1. **`session_snapshot()` 只读聚合**（含 `available_tools`）：把每轮调试的 4~5 次状态探测压成 1 次往返，并正面回答"我现在到底能调什么"；代价约 60 行只读聚合、无风险。
2. **诊断共享 + `open_port` 带诊断 + `session_doctor()`**：把 GUI 已有的 `_friendly_open_error`（`SC/ui/controller.py:313-320`）搬到 `core/diag.py` 供 MCP 复用，让"端口被占用/板子没连/没有波形"从"猜+反复试"变成"读建议"；代价极低（大半是代码搬迁），硬件类问题每轮都可能命中。
3. **`read_log` 增量游标 + 去重 + `by_level` 统计**（兼容旧签名）：调试循环里 5 次拉日志从"5×全量"变成"1×全量 + 4×增量"，token 降 60~80%；代价约 60~75 行，需要把 64KB 裁剪与 seq 对齐。
4. **失败显性化：关键写路径不再静默**（`config.save`、录制写行/写表头、串口打开 → `ok:false` + `write_errors/last_error`，`_rows` 成功才累加）：这是"AI 在假状态上推理"的根因修复，也是第 1、2 项结论可信的前提；代价小（约 35 处逐点改造，先改关键路径）。
5. **仓库卫生三连**：`requirements.txt` 补 `uvicorn` + `pylink-square`（并加上限）、把 `justfloat_autoname` 挪进 `if jf is not None:` 守卫、补最小 `pytest.ini`（`asyncio_mode=auto` + `pythonpath=.`）：一次消除"新人照文档装完 AI 全废"、"全仓唯一红灯"、"同仓库两种测试结论"三个 onboarding 级故障；代价小（改动 <10 行 + 一个配置文件）。

> 以可靠性为先的团队可以把第 5 项换成 **C-3 串口重开竞态改造（代际令牌）**：收益是消除 `serial-reader` 泄漏与虚假断线（t4 已复现 8/20、5/10），代价中（改动集中在一个类）。

---

## E. 不建议做的事（过度设计 / 性价比低）

1. **把 RTT 日志/波形实时推送进 DSH 会话（扩展现有 bridge push）**——表面是"AI 实时感知"，实际是上下文污染 + 反复唤醒 + 每个字节都进 token；桥的 `chat` 模式每条数据注入一次（`lib/index.js:122-124`），`monitor` 只是合并（`:126-129`）。正确形态是 B.6 的"AI 主动等 + 游标取"。
2. **给 `justfloat_frames` 更大缓冲/更全返回**——500 帧@10ms 已够"看一眼前几帧"（`justfloat.py:59` deque maxlen），放大只会爆 token；要"看历史"应该走 B.7 的**统计后**返回。
3. **在 `serial_vofa` 下恢复通用 `send`/`read_received`**——JustFloat 是二进制流，对 AI 无意义（`SC/README.md:96-98` 的判断是对的）；更好的做法是给串口模式加**文本行日志环**（B.3 的同源方案），只给"能读懂的日志"。
4. **现在整体重构"服务常驻、工具常注册、mode 只做数据路由"**——收益只有 1~2s 重连窗口与工具面漂移，但动的是 `main.py:63-76`、`ui/controller.py:219`（`_switch_mode`）、`ai_server.py:544-585` 三处一致性，风险中；建议与 `available_tools`（B.2）配套、**分期**做，不要为首批收益动它。
5. **pyOCD/OpenOCD + gdb MI 全量接入（断点/单步/变量/回溯）**——对 HardFault/竞态/内存踩踏确实是终极手段，`debug-live` skill + `.mcp.json` 里的 `cortex-debug` 已能覆盖一部分；但它是另一套工具链的完整工程，超出本仓库范围。定位为"可选演进路线"，先把 RTT 侧观测效率（B 组）做完。
6. **给 `read_log` 加正则/查询语言**——AI 不需要查询语言，它需要"少返回"；B.3 + `log_summary` 足够。
7. **现在上 lint 全家桶 / 重构 `mini_cordis` / 拆 wheel / 补 UI 自动化测试**——收益与当前痛点不匹配；先做 `pytest.ini` + ruff 配置（基线 14 errors）即可。
8. **为"总开关在 DSH 侧"再加一层自证机制**——桥已用 `state.mode` 忽略 incoming 里的 `mode` 字段（`lib/index.js:119-121`），软件侧 `ai_push_mode` 是死字段但无害；值得做的只是**在 UI/文档里标注它只作参考**，不必新增握手协议。

---

## F. 待验证清单

> 规则：以下都是**无法仅靠静态代码证据定论**的判断。逐条给出所需信息/验证方法。

| # | 待验证项 | 为什么不能定论 | 需要什么信息/方法 |
|---|---|---|---|
| F-1 | 冻结版（PyInstaller）RTT 模式是否可用 | `spec:9` hiddenimports **无 pylink**，`rtt.py:17-24` 缺库时静默降级为"未安装" | 跑一次打包产物：`rtt_connect` 是否报"pylink 未安装"；必要时补 hiddenimports |
| F-2 | 冻结版 MCP 实际工具数（技术日志记 10，静态推算 serial 模式 11） | 我实测的 11 是**源码版**（正常模式） | 在 exe 里 `tools/list` 对照；并核对当时 mcp 版本 |
| F-3 | `serial_vofa` / `rtt_vofa` 的工具数 33/38 | 本次只实测了正常模式 11；vofa 数值来自 t1 静态推算，未逐条复核 | 用同样手法在 vofa 模式下 `tools/list` 计数 |
| F-4 | A-2/C-3 中"OS 句柄仍被持有导致后续 open 失败" | 本次只用 `loop://` 证明"服务状态被清空"；pyserial 3.5 Windows 无 `__del__` 是静态证据 | 真实 COM 口上复现 close→open 循环，观察 `Access is denied` |
| F-5 | 循环发送是否会出现两个发送循环 | `transmitter.py:64-77` 的 `Future.cancel()` 是异步取消；需允许加临时计数 | 真机高频反复切换"定时循环发送"，在 `_cycle_loop` 内计数 |
| F-6 | Inno Setup `MyAppId` 是否合法 GUID | `iss:8` 转义后末段含非十六进制字符，疑似非法 | 用 ISCC 编译 + 检查 `HKCU\...\Uninstall\*` 键名 + 0.1.0→0.2.0 覆盖升级行为 |
| F-7 | **DSH/MCP 客户端的请求超时上限**（决定 `session_watch` 能等多久） | 本机 8765 未运行，无法实测；客户端侧超时未在本仓库 | 真机 + DSH MCP 客户端配置；据此把等待上限定在 10~20s（或改轮询式短等待） |
| F-8 | CSV 的精确 token/行 | 抽样口径可复现（23,751 字节/273 行/9 列），但换算未记录方法与 tokenizer | 用目标模型 tokenizer 对同一文件精确计数；目前的"25~40 token/行"只是量级估计 |
| F-9 | 采样率高于实际帧率时的"重复值平段"严重程度 | 依据 `float_recorder.py:274-278` 的名义时间与 latest 快照语义推断 | 真机：50Hz 采样 100Hz 帧率 vs 10Hz 帧率，对比 CSV 平台段 |
| F-10 | 文档所述 `/echo` + `simplifyReply` 是否存在于**已安装的 DSH 版本** | 本仓库 `lib/index.js` 无该端点（`:216-241`），但 `SC/README.md:214-235` 描述得很具体 | 核对实际安装到 `$DSH_HOME/profiles/*/node_modules/ai-embedded-bridge` 的版本/源码 |
| F-11 | "ch1 被 JScope 占用"这一诊断提示的前提 | 来自 t3 引用的 `rtt_design.md`（本稿未逐行核对该文档） | 核对文档与固件 RTT 通道分配约定 |
| F-12 | 冻结版安装包实际打包版本与配置目录 | 据文档是改名前的旧构建（配置 `~/.serial_assistant`），未解包验证 | 解包 `software/dist/dist-installer/*.exe` 或运行一次观察配置目录 |
| F-13 | 桥插件的加载时序/多 profile 行为 | 本机同时存在于 `profiles/node_modules` 与 `profiles/web/node_modules` | DSH 多 profile 加载日志 + 启动顺序 |
| F-14 | `csv_dir` 默认值"三处说法不一"的实际落点 | 代码是 `or default_csv_dir()`（`float_recorder.py:353`），冻结版落 `~/.ai_embedded_assistant/csv_floder`（`float_recorder.py:29-34` 的 frozen 分支） | 源码版与冻结版各录一次，确认文件实际位置 |
| F-15 | `.mcp.json` 指向 `localhost:7580/sse`（cortex-debug）是否可用 | 该文件是本次会话新出现的未跟踪文件，与 8765 无关 | 启动 cortex-debug 后实际调用 |
| F-16 | `time.sleep(0.002)` 在目标机器上实际睡眠分布（C-3 命中率） | 与机器/负载相关；t4 只在 `loop://` 上测过 | 真机重复 close/open N 次 + 统计 `serial-reader` 线程数 |

---

## 附录 A：核对记录（22 条抽查，均为本稿作者亲自打开文件/复跑）

| # | 被核对的断言 | 证据（已亲自确认） | 结论 |
|---|---|---|---|
| 1 | `justfloat_autoname` 注册在守卫之外，正常模式也可见 | `SC/plugins/ai_server.py:366-383`（守卫在 `:264-265` 已关闭） | ✅ 成立 |
| 2 | `test_debug_switch.py` 是唯一红灯，退出码 1 | 实测 `python tests/test_debug_switch.py` → exit=1，断言输出含 `justfloat_autoname`；断言位于 `tests/test_debug_switch.py:67-68,122` | ✅ 复现 |
| 3 | `pytest tests` = 6 failed / 24 passed，失败均为 async 不被支持 | 实测 `pytest tests -q -p no:cacheprovider` → `6 failed, 24 passed in 0.51s` | ✅ 复现 |
| 4 | `exec_shell` 提示符为空时必然等到 timeout | `SC/plugins/rtt.py:207-216`（`if prompt and prompt in self._shell_buf`）；**但** `config.py:74` 默认 `"HC_dqj@root:"` 非空 | ⚠️ 条件性成立，默认不成立（B-1，已降级） |
| 5 | `read_log` 无游标/无时间戳/取尾部 count 行 | `SC/plugins/rtt.py:218-230`、缓冲 `:71-72,266-268` | ✅ 成立 |
| 6 | RTT 读线程异常退出不释放 J-Link | `rtt.py:275-281` vs `disconnect()` `:152-167`（`rtt_stop()+close()`） | ✅ 成立 |
| 7 | 串口重开竞态（共享停止标志 / 不 join / 清句柄） | `serial_port.py:87,104,105-108,111-123,154,174-182` | ✅ 成立 |
| 8 | 每包一推线程 + 失败静默 + payload 含死字段 | `ai_server.py:430-442`、`:439-440`、`:445-460` | ✅ 成立 |
| 9 | `config.save` 静默吞异常；`ai_reply_enabled` 默认 False | `config.py:127-135`、`:52` | ✅ 成立 |
| 10 | CSV 时间列用名义时间；失败仍计数 | `float_recorder.py:274`、`:277`、`:290-294,296-303`；停止条件用真实时钟 `:245-247` | ✅ 成立 |
| 11 | 依赖清单缺 uvicorn/pylink，spec 点名 uvicorn 无 pylink | `SC/requirements.txt:1-4`、`ai_server.py:22`、`rtt.py:17-24`、`spec:9` | ✅ 成立 |
| 12 | 桥只有 4 条路由，`/echo` 只有残留 CSS；软件文档仍在宣传 | `lib/index.js:216-241`、`:301`；`SC/README.md:214,218,235` | ✅ 成立 |
| 13 | `ai_reply_enabled` "默认开" vs 代码 False | `SC/README.md:227` vs `config.py:52` | ✅ 成立 |
| 14 | 事件总线串行 await；TCP 广播串行 drain 2s | `mini_cordis.py:270-278`；`tcp_forward.py:145-163` | ✅ 成立 |
| 15 | `RxBuffer` 无上限、统计 O(n) | `ai_server.py:28-58`（`deque()` 无 maxlen；`pending` = `sum(len(c))`） | ✅ 成立 |
| 16 | mcp 1.27.1：同步工具内联执行；保护只在 loopback 启用 | `func_metadata.py:93-96`；`fastmcp/server.py:177-183` | ✅ 成立 |
| 17 | RTT 面板 4 个键不在 DEFAULTS 被静默丢弃 | `ui/panels/rtt.py:319-322,327-332` vs `config.py:16-86` | ✅ 成立 |
| 18 | `_friendly_open_error` 只服务 GUI，MCP 返回裸异常 | `ui/controller.py:307,313-320`；`ai_server.py:97-99` + `serial_port.py:100-102` | ✅ 成立 |
| 19 | 正常模式下 `rtt_shell` 也有 `send/read_received` | `ai_server.py:121-151`（`debug_mode = bool(extra.get("justfloat"))`）+ `main.py:65-71`（rtt_shell 只排除波形插件） | ✅ 成立 |
| 20 | `SC/README.md` 两态时代 5 处硬错 + 绝对路径 + 不存在的示例 | `:84-103`、`:86`、`:91`、`:150,196,220`、`:3`、`:130`、`:165` | ✅ 成立（5 处 config.json 中 2 处正确，见 B-4） |
| 21 | `get_status` 返回 `mode` 而非 `debug_mode` | `ai_server.py:108-119` | ✅ 成立 |
| 22 | CSV 样例约 40 token/行 | `SC/csv_floder/log_20260824_172151.csv` = 23,751 B / 273 行 / 9 列（实测），换算方法未记录 | ⚠️ 量级成立、数值待定（B-5） |

## 附录 B：与三份原报告的差异（修正 / 降级 / 删除）

| # | 原论断 | 本稿裁定 | 依据 |
|---|---|---|---|
| B-1 | t3 F13："`rtt_shell_prompt` 默认空（`config.py:74`），空 prompt 下 `exec_shell` 必然等到 timeout；AI 执行 5 条 shell 命令 = 15s 纯等待" | **修正+降级**：`config.py:74` 默认值是 `"HC_dqj@root:"`，**非空**；默认配置下提示符命中即返回，"5×3s 纯等待"不成立。仅当用户把该键清空时才出现"必然等满超时"。结论降级为**条件性**问题，仍建议在 `shell_exec` 返回里加 `matched_prompt/timed_out/elapsed_ms`（因为超时也返回 `ok:true`，`rtt.py:216`） | `SC/plugins/config.py:74`；`SC/plugins/rtt.py:207-216` |
| B-2 | t1 §4.6："仓库里仍存在 26 个已入库的 CSV"；t4 E8："仓库内已提交 21 个录制样例" | **删除**：两者都不成立。实测 `git ls-files "*.csv"` = **0**，26 个文件（916,624 字节）在磁盘上但被 `.gitignore:12` 忽略（`git check-ignore` 命中）。t2 §3.4 的判断（"CSV 未入库，好"）正确；真正的卫生问题是"文档引用了目录里不存在的示例"与"本地目录堆积历史数据" | 实测 `git ls-files` / `git check-ignore` / `Get-ChildItem SC/csv_floder` |
| B-3 | t2 6.1："20 处 `except: pass`" | **修正（量级上调）**：生产代码精确统计为 **35 处**（`tcp_forward.py` 8、`ai_server.py` 7、`float_recorder.py` 5、`rtt.py` 3、`main.py` 2、`serial_port.py` 2、`history.py` 2、`justfloat.py` 2、`recorder.py` 2、`config.py` 1、`logger.py` 1），另 3 处在 tests。t2 报告里按文件的分布（config 4、tcp 4、main 3、rtt 2、serial 1）需一并修正；结论方向（静默失败普遍、危害 AI 调试）不变且更强 | 正则 `except[^\n]*:\s*\n\s*pass` 全仓统计 |
| B-4 | t2 4.1："`SC/README.md` 5 处写 `config.json`（实际 config.yaml）" | **部分修正**：5 处出现 `config.json`，但 `:64` 与 `:76` 描述的是**旧版回退文件**（`config.py:104-105,114-115` 确实优先 `config.yaml`、回退读取 `config.json`），语义**正确**；真正写错的是 `:150`、`:196`、`:220` 三处（把当前配置文件说成 `config.json`）。其余硬错（`:3` 绝对路径、`:86` `debug_mode`、`:91` 工具数 10、`:130` 不存在的 CSV 示例、`:165` `debug_mode`）全部成立 | `SC/README.md:3,64,76,84-103,130,150,165,196,220` |
| B-5 | t3 §0.3："录制 CSV 实测每行 ≈ 40 token（23.7KB/272 行）" | **量级保留、数值降级**：抽样可复现（`log_20260824_172151.csv` = 23,751 字节 / 273 行 / 9 列 ≈ 87 字节/行），但换算为 token 的方法与 tokenizer 未记录；按数值密度估 ≈25~40 token/行。**结论不变**：50Hz×300s 的 CSV 直读不可行（十万级 token），必须软件侧先算 | 实测文件大小/行数/列数 |
| B-6 | t3 F23："没有约定'用哪一列当 X 轴'" | **部分修正**：`SC/docs/串口指令文档.md` 对 `timestamp` 列明确写着"单位秒，每帧 +0.01，**作 X 轴**"，所以"约定"是有的；真实问题是**录制器自产的 `Time(s)` 列与它不一致**（名义时间，`float_recorder.py:274`），而 AI 默认读的是 `Time(s)` | `SC/docs/串口指令文档.md`（timestamp 行）；`SC/plugins/float_recorder.py:274` |
| B-7 | t4 F3："`PyQt6>=6.6` 会解析出没有 3.14 wheel 的版本 → 干净机器装不起来" | **降级为待验证**：`.pyc` 为 cpython-314 与"6.6/6.7 无 3.14 wheel"是间接证据，pip 的实际解析行为取决于版本/标签匹配与 sdist 回退，未做冷装实测 | 见 F-3（t4 自己的判定方法） |
| B-8 | t2 3.4："`SC/csv_floder/` 现存量 895 KB / 27 个文件" | **修正数字**：实测 916,624 字节 / **26** 个文件（可能测量时点不同） | `Get-ChildItem SC/csv_floder` |
| B-9 | t3 §1.2 引用 "`ui/panels/rtt.py:238,274` 有毫秒时间戳" | **行号修正**：实际在 `:228` 与 `:274`（`:238` 附近是导入/常量区） | `SC/ui/panels/rtt.py:228,274` |
| B-10 | t4 A1 说明 `close+open` "20 次试验 8 次留下 2 个读线程"、A2 "10 次 5 次 `_serial is None`" | **保留（标注为上游实验）**：本稿未重跑（会引入线程/文件副作用），但代码路径逐行核对一致（`serial_port.py:87,104,111-123,174-182`），据此列为高风险 | `SC/plugins/serial_port.py` |
| B-11 | t1 §2.1："实际工具数 serial=11 / rtt_shell=11 / serial_vofa=33 / rtt_vofa=38" | **部分保留**：正常模式 11 已由本稿实测确认（含 `justfloat_autoname`）；vofa 两个数值来自静态推算，本稿**未逐条复核**，标为待验证（F-3） | 实测 `test_debug_switch` 输出；`ai_server.py` 注册点 |

---

## 附录 C：本稿的口径声明

- 所有 file:line 均指**当前工作区**（含未提交改动）的文件内容；行号可能随改动漂移，引用时请以符号名（函数/类名）为准。
- "代价"是相对本仓库规模的经验估计，不是承诺排期。
- 本稿把"上游报告的结论"与"本稿复核后的结论"分列（附录 A/B），凡未通过复核的一律降级为待验证（F 表），不写成结论。
- 未列入的常见工程建议（引入 lint 全家桶、重构 `mini_cordis`、拆包 wheel、UI 自动化测试）属"收益与当前痛点不匹配"，见 §E.7。
