# t2 工程与工作流改进点评估（只读，未改任何生产代码）

> 方法：基于 t1 survey + 本轮自行只读抽查（含**实际执行测试**取证）。
> 约定：仓库根 = `F:\AI\Skills_Lib\ai_embedded_assistant`，`SC/` = `software/code/`。
> 每项格式：现状证据(file:line) → 影响 → 建议 → 代价(小/中/大) → 优先级(P0/P1/P2)。
> 本轮唯一副作用：跑测试产生的 `__pycache__`（已存在的忽略产物）；`_tmp_*` 与 `.pytest_cache` 已确认无残留。

---

## 0. 本轮实测取证结果（先看这个）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests -q`（SC/ 下） | **6 failed, 24 passed**；6 个失败全是 `async def functions are not natively supported` |
| `python -m pytest tests --collect-only` | 只收集到 30 个用例，来自 **5/9** 个测试文件（`test_ai_reply / test_commands(部分) / test_config(部分) / test_debug_switch / test_tcp_forward / test_e2e_*` 未进收集） |
| 逐个 `python tests/test_X.py` | 8 个脚本 exit=0；**`test_debug_switch.py` exit=1（真实回归）** |
| `python -m ruff check .` | 14 errors：7×F841 unused-variable、5×E402、2×F401（ruff 0.15.17，环境已装；仓库无任何 ruff 配置） |
| `python -m mypy --version` | 环境无 mypy（仓库也无 mypy.ini/pyproject） |

结论一句话：**测试今天不是绿的，而且没有统一的跑法**；打包/文档/依赖三处存在可复现的漂移。

---

## 1. 模块划分与依赖方向

### 1.1 【P2】`codec` 服务无人使用，与 `core.codec` 直连并存（不是"两份实现"，但确实是死层）
- 证据：`SC/plugins/codec.py:3-11` 只是 `ctx.provide("codec", core.codec)` 的薄包装；实际调用点
  `SC/plugins/transmitter.py:8,102` 传 `codec=ctx.get("codec")`，而
  `SC/ui/controller.py:12`、`SC/ui/panels/receive.py:12`、`SC/plugins/recorder.py:7`、
  `SC/plugins/ai_server.py:24-25` **全部直接 `from core.codec import ...`**。
- 影响：`SC/README.md:18` 把 `plugins/codec.py` 列为"服务 codec"，但服务化的意义（可替换/可注入）没被兑现；
  依赖方向变成 plugins/ui → core 直连，`core/` 事实上是"共享工具库"而非"内核"。新增平台（如改编码策略）时无单点。
- 建议：二选一，别两头挂着——要么删掉 `plugins/codec.py` 并在 README 说明 codec 属于 `core/` 纯函数层；
  要么让调用点统一走 `ctx.get("codec")`。
- 代价：小。优先级：P2（纯清洁度，无功能风险）。

### 1.2 【P2】`core/` 与 `plugins/` 的职责边界没有文字契约
- 证据：`core/` 里同时有内核（`mini_cordis.py`）、纯函数（`codec.py`）、Qt 主题（`theme.py`，`SC/core/theme.py:9-11`
  直接 import PyQt6）；`SC/README.md:9-25` 的结构图把三者都算"内核 + 纯库（无生命周期）"。
- 影响：`core/` 若被当成"无 Qt 依赖的纯库"，冻结打包/单测（无 GUI 环境）就会踩坑——`theme.py` 一 import 就拉 PyQt6。
- 建议：README 里补一行边界约定（`core/mini_cordis.py`=内核；`core/codec.py`=纯函数；
  `core/theme.py`=Qt 资源层，允许依赖 PyQt6），或把 theme 挪到 `ui/`。
- 代价：小（文档） / 中（挪文件）。优先级：P2。

### 1.3 【P1】打包路径只有一处做了 frozen 处理，其余靠"加载顺序 + 相对路径"隐式成立
- 证据：只有 `SC/plugins/float_recorder.py:31-35` 判断 `getattr(sys,"frozen",False)`；
  `SC/main.py:25-38` 用 `Path(__file__).resolve().parent` 推 `PLUGINS_DIR`/图标；
  `AIEmbeddedAssistant.spec:8` 把 `plugins`/`core/assets`/`img` 打成 datas（落到 `_internal/`）。
- 影响：任何一处换成 `os.getcwd()`/`sys.executable` 的路径推导，都会在绿色版里静默失效
  （t1 已记录过"stdout=None 导致 MCP 静默起不来"这类同源坑，见 `docs/技术日志-打包MCP问题.md:20`）。
- 建议：抽一个 `core/paths.py`（`app_dir()/data_dir()`，统一 frozen 判定），所有路径推导引用它；
  并保留一条"绿色版冒烟"（`AUTO_QUIT_MS` 钩子已在 `SC/main.py:118-121`，可直接用）。
- 代价：中。优先级：P1。

---

## 2. 测试

### 2.1 【P1】无 pytest/CI 配置；两个文件的 async 用例 pytest 根本收不到、只能手跑
- 证据：仓库无 `conftest.py` / `pytest.ini` / `pyproject.toml` / `tox.ini` / `.github`（本轮全盘搜索为空）；
  9 个 `test_*.py` 全靠"`test_x.py` 顶部 `sys.path.insert` + `if __name__=='__main__'` 调 `main()`"自跑
  （如 `SC/tests/test_config.py:12-13,123-139`）；`test_float_recorder.py`、`test_justfloat.py` 的用例是
  `async def`，被 pytest 收到后直接报错（实测 6 个失败），而手跑走 `asyncio.run(main())` 是通的
  （`test_float_recorder.py:189,204-205`）。
- 影响：**同一份测试两种跑法结果不同**：`python -m pytest tests` 红 6 个（看起来像坏了），
  逐脚本手跑全绿（看起来没事）。团队/CI 无法用一个命令判断"现在是否健康"。
- 建议：加最小配置——`pytest.ini` 设 `pythonpath = .`（或 `conftest.py` 注入）+ `asyncio_mode = auto`
  （依赖 `pytest-asyncio`，本机已装但未声明，见 3.1）；保留 `main()` 手跑入口不冲突。
  再补一条 `Invoke-Pester`-无关的简单 `scripts/run-tests.ps1` 汇总 9 个脚本退出码，作为过渡。
- 代价：小。优先级：**P1**（没有它，2.2 的回归没人会发现）。

### 2.2 【P0】`test_debug_switch.py` 已失效：`justfloat_autoname` 在"正常模式"也无条件注册（模式隔离被打破）
- 证据（可复现）：
  - `python tests/test_debug_switch.py` → 退出码 1：
    `AssertionError: ['close_port','config_load','config_save','config_set','config_status','get_status','justfloat_autoname','list_ports','open_port','read_received','send']`
    （断言在 `SC/tests/test_debug_switch.py:67-68`：正常模式不应有 `justfloat_*`）。
  - 代码：`SC/plugins/ai_server.py:365-383` 的 `justfloat_autoname` 注册在 **`if jf is not None:` 之外**，
    只有函数体内做了 `jf = extra.get("justfloat")` 的运行时兜底；同文件其余调试工具都在守卫内
    （对比 `:264-265` 的 `if jf is not None:`）。
  - 归属：`git log -- software/code/plugins/ai_server.py` 显示该注册来自**最后一条提交 `27937a9`**，
    且该提交**没有同步改测试**（`git diff HEAD -- software/code/tests/` 为空）。
  - 同类漂移：`SC/plugins/ai_server.py:3` 头部注释仍写"提供 6 个工具"；`SC/README.md:91` 写"正常（默认）…10 工具"；
    实际正常模式是 **11 个**（含 `justfloat_autoname`）。
- 影响：**这是全仓库唯一一处"设计契约（按模式给工具面）被代码违反"**，正是 README/SKILL 反复强调的
  "工具随模式出现、不存在的工具别硬调"的核心不变量。虽然函数体返回 `{"ok": False, "message": "justfloat 服务未加载…"}`
  （行为上是安全降级，不是崩溃），但：
  ① 测试红灯让整条 CI 失去可用性；② 文档/断言与代码三方不一致，AI 侧可能误调；
  ③ 该测试失败**恰好提前于 `cordis.unload_all()`**（`:114`）抛出，本次实测 exit 用时 820ms 未挂，
  但一旦断言顺序变化让 AI 接口在 uvicorn 起来后再失败，测试进程可能因未停止的 MCP 监听而不退出（隐蔽挂死风险）。
- 建议（不改生产码的前提下可选三条）：
  ① 把注册挪进 `if jf is not None:` 守卫（最小、最一致，符合同文件其他调试工具写法）；
  ② 若刻意保持"全局可调用 + 运行时降级"，则**改测试与三处文档**（README 工具数、ai_server 头注释、SKILL.md 的模式表），
     并把该工具在正常模式的返回语义写进文档；
  ③ 无论选哪条，都补一条"正常模式工具白名单"断言（把现在的隐式约定显式化）。
- 代价：小（①约 2 行）。优先级：**P0**（它是测试红灯的唯一真凶，且涉及最主要的产品不变量）。

### 2.3 【P2】测试会污染真实 HOME / 依赖 CWD，隔离不彻底
- 证据：`SC/tests/test_config.py:98-120` 通过改 `USERPROFILE` 后写 `Path.home()/".ai_embedded_assistant"` 再删；
  其余测试自建 `tests/_tmp_*`（`test_config.py:18-21` 注释明确写"沙箱下系统 Temp 不可写"）。
- 影响：① 若测试在 `Path.home()` 解析上被绕过（Windows 上 `HOMEDRIVE/HOMEPATH` 等），会写坏开发机真实配置；
  ② `_tmp_*` 落在 `tests/` 下，异常退出时留在仓库里（本轮 9 个脚本均正常清理，已确认无残留，但无 `finally` 兜底路径偏窄）。
- 建议：统一 `tmp_path` 或 `TemporaryDirectory` + 显式 `monkeypatch.setenv`；把 `_tmp_*` 加进 `.gitignore` 兜底。
- 代价：小。优先级：P2。

### 2.4 【P2】CI 可行性：技术上完全可行，卡点只有 2.2 的红灯 + 缺 pytest 配置
- 证据：全部测试都能无硬件跑（用 `loop://` 回环串口模拟设备：`SC/tests/test_e2e_justfloat_recorder.py:47`、
  `test_ai_reply.py:46`、`test_tcp_forward.py:54`）；Qt 用例已 offscreen
  （`SC/tests/test_receive_panel.py:10` `QT_QPA_PLATFORM=offscreen`）；RTT 相关只有交互式探针
  `SC/tests/rtt_probe.py`（需真实 J-Link，**不属于自动化**）。
- 建议：GitHub Actions Windows runner 上 `pip install -r requirements.txt pytest pytest-asyncio`
  → `pytest tests`；硬件相关一律留在 `rtt_probe.py`（已是独立脚本、不在 `test_*` 命名空间，天然不会被收集）。
- 代价：小-中（配置 + 首次调绿）。优先级：P2（2.1/2.2 修好后再上）。

---

## 3. 打包与发布

### 3.1 【P0】`requirements.txt` 漏了启动 MCP 与 RTT 的硬依赖，且全部零版本上限
- 证据：`SC/requirements.txt` 只有 4 行（`PyQt6>=6.6 / pyserial>=3.5 / mcp>=1.0 / pyyaml>=6.0`）；但
  `SC/plugins/ai_server.py:22` 直接 `import uvicorn`（**不是可选依赖**，缺了 MCP 服务起不来），
  `SC/plugins/rtt.py:17-24` 才 `import pylink`（可选、缺了降级）；
  `SC/AIEmbeddedAssistant.spec:9` 的 hiddenimports 里点名了 `uvicorn`、`sse_starlette`、`pydantic_settings`
  ——即打包时知道要用，源码安装流程里没写。
- 影响：`cd software/code && pip install -r requirements.txt && python main.py`（根 `README.md:44`、
  `docs/使用文档.md:29-33`、`SKILL.md:19` 三处都是这条命令）在干净机器上会**因为缺 uvicorn 而 AI/MCP 全废**，
  且 `SC/plugins/ai_server.py:660-669` 只把异常 `log()` 掉（console=False 时日志进 devnull），表现为"AI 连不上但软件正常"。
  这正是最贵的一类 onboarding 故障。
- 建议：补 `uvicorn`（MCP 运行必需）与 `pylink-square`（RTT 模式必需，可 `; sys_platform=="win32"` 或注释说明）；
  同时给上限或锁版本（至少 `mcp>=1.0,<2`），并在 README 加"Python ≥3.10（建议 3.12/3.13；3.14 下 PyInstaller 侧有已知坑）"。
- 代价：小。优先级：**P0**（一行依赖 = 一个"照文档装完不能用"的故障）。

### 3.2 【P1】版本号只有一处、且各组件各自为政；exe/安装包与代码不同步
- 证据：全仓 `__version__` = 无（`grep version|__version__` 在 `SC/**/*.py` 零命中）；
  唯一版本号在安装脚本 `SC/AIEmbeddedAssistant.iss:5` `MyAppVersion "0.2.0"`；
  桥插件 `dsh_plugin/ai-embedded-bridge/package.json:3` = `0.1.0`（另一个组件，不同命名空间，但对外都叫"0.1/0.2"）；
  绿色版 exe 时间戳 2026-09-15 15:57、安装包 09-15 15:58，而最后一次提交是 2026-09-09，
  且 `27937a9`（通道名自动发现）**未重新打包**。
- 影响：① 用户拿到的安装包与 `main` 代码不是同一份（没有版本号可从 UI/日志读到，只能看文件时间）；
  ② 报 bug 时无法定位版本；③ 桥插件版本与软件版本无对照表。
- 建议：加 `SC/core/version.py`（`__version__ = "0.3.0"`）→ 安装脚本 `.iss` 与之保持一致（或由打包脚本注入）；
  UI 标题/设置页显示版本；`README` 增加"软件版本 ↔ 桥插件版本"对照表；打包后**自动更新 README 里的构建时间/版本**。
- 代价：小-中。优先级：P1。

### 3.3 【P2】跨仓库依赖 `eide-mcp-skill` 是硬缺口（"完整闭环"开机即断）
- 证据：根 `README.md:64-65`、`docs/使用文档.md:5`、`SKILL.md:34-36,111`：编译/烧录必须另装
  `github.com/AlexiaQAQ/eide-mcp-skill` + VSCode EIDE 扩展开 MCP(8940)，**未随仓库分发**。
- 影响：AI 会话在没有该 skill 时无法完成"编辑→编译→下载"，只能提示用户手装——这是 B 类（让 AI 调试更快）里
  最高频的断点，也是新人最容易卡住的地方。
- 建议：README 顶部加"最小闭环前置清单"（DSH + 桥插件 + 软件 MCP + eide-mcp-skill 四件套 + 各自就绪自检命令），
  并提供一个 `scripts/check-env.ps1` 一键体检（进程/端口 8765、8940、3080/插件路由）。
- 代价：小。优先级：P2（文档+脚本，收益高）。

### 3.4 【P2】发布产物与仓库卫生
- 证据：`.gitignore:7-12` 忽略 `software/dist/` 与 `csv_floder/*.csv`；`git ls-files "*.csv"` = 0（**CSV 未入库**，好）；
  但 `SC/csv_floder/` 现存量 **895 KB / 27 个文件**（最早 2026-08-24），且 `SC/README.md:130` 引用的示例
  `csv_floder/log_20000103_232730.csv` **不存在**（已实测 `Test-Path` = False）；
  `SC/float_recorder.py:32` 的 docstring 也仍在引用同一个不存在的示例名。
- 影响：目录里堆着历史录制数据（本地噪音，不影响仓库）；文档指向不存在的文件会让读者以为录制坏了。
- 建议：把示例改为真实存在的文件或删掉该引用；`csv_floder/` 增加 `.gitkeep` 说明用途。
- 代价：小。优先级：P2。

---

## 4. 文档与 onboarding

### 4.1 【P1】`SC/README.md` 整体停在"正常/调试模式"时代，与四态 `mode` 实现脱节（5 处硬错误）
- 证据（逐条已核实）：
  1. `SC/README.md:84-103` 整节「运行模式（正常 / 调试）」+ `:86` "配置键 `debug_mode`，默认 `false`" +
     `:91-92` 两张表——而实现是四态 `mode`（`SC/plugins/config.py:59-61` 注释明确写
     "`debug_mode` 已由 mode 四态取代，保留兼容旧配置"）。
  2. `:150`、`:196`、`:220` 五处写配置文件为 `~/.ai_embedded_assistant/config.json`——实际是 **`config.yaml`**
     （`SC/plugins/config.py:99-105`）。
  3. `:3` 的架构引用链接是**另一台机器的绝对路径** `F:\Python_Floder\Python_Example\Cordis_Test`。
  4. `:130` 引用不存在的 CSV 示例（见 3.4）。
  5. `:91` 工具数 10（实际正常模式 11，见 2.2）；`SC/plugins/ai_server.py:3` 头注释"6 个工具"。
- 影响：这是**离开发者最近、也是最有权威感**的一份文档（`SKILL.md` 和 `docs/使用文档.md` 都把它当细节来源），
  它错的地方 AI 会照抄：例如按 `:86` 去改 `debug_mode` 会发现"改了没用"，按 `:196` 去改 config.json 会改了个空文件。
- 建议：把 84-103 整节重写为四态 `mode` 表（内容可直接复用 `SKILL.md:42-47` 与 `docs/使用文档.md:48-55`，
  三处统一成一份"模式 ↔ 插件 ↔ 工具面"表，并只在一处维护、另两处链接过去）；五处 `config.json` 全改 `config.yaml`；
  删掉机器绝对路径；工具清单补 `justfloat_autoname`（`SKILL.md:79` 已有该条描述，可对齐）。
- 代价：小-中（是纯文档活，但需要一次三处对齐）。优先级：P1。

### 4.2 【P2】三份文档的读者定位有重叠，缺少"哪份是权威"的声明
- 证据：根 `README.md`（仓库/安装/改名迁移）、`docs/使用文档.md`（UI 逐步操作）、
  `SC/README.md`（架构与 MCP 细节）、`SKILL.md`（AI 操作流程）、`dsh_plugin/.../README.md`（桥插件）
  各自完整，但工具清单/模式表在 **4 份文档里重复**（`SC/README.md:158-194`、`SKILL.md:71-83`、
  `docs/使用文档.md:46-55`、根 `README.md:36-39`）。
- 影响：改动一次要同步四处，实际已经漂移（4.1）。AI onboarding 时无法判断以谁为准。
- 建议：定权威源（建议：模式/工具面 → `SKILL.md`；UI 操作 → `docs/使用文档.md`；架构 → `SC/README.md`），
  其余位置只放链接 + 一句摘要。
- 代价：小。优先级：P2。

### 4.3 【P2】过期描述集中在"改名前后"：SKILL.md 自己警告安装版是旧构建
- 证据：`SKILL.md:60-61` "安装版 exe 是改名前的构建（配置 `~/.serial_assistant`、`ai_push_url` 为旧地址）；
  要对齐改名后的配置与推送地址请用源码版"；根 `README.md:78-79` 同义重复。
- 影响：文档在教用户"别用我们的安装包"。要么重新打包（3.2），要么把该警告降级为脚注。
- 建议：重新打包后删除该警告；短期内在根 README「快速开始」显式标注"绿色版/安装包为 09-15 旧构建，
  功能对齐请用源码运行"。
- 代价：小。优先级：P2。

---

## 5. 分发与兼容性（DSH 桥插件）

### 5.1 【P0】`/echo` 端点三方不一致：桥插件文档说没有、软件文档说能用、**代码里确实没有**——但前端 UI 还画着开关
- 证据：
  - 桥插件 README 的端点表只列 3 个（`dsh_plugin/ai-embedded-bridge/README.md:70-77`：`/incoming` `/mode` `/target` `/status`）；
  - 路由分发只有 4 个分支（`dsh_plugin/ai-embedded-bridge/lib/index.js:216-219`），`/echo` 落到 `else` 分支；
  - `lib/index.js` 全文 grep `echo` **只命中 1 处**：`:301` 的 CSS 选择器
    `#ai-embedded-bridge-echo{...}`——说明**面板 DOM/CSS 仍在**（"AI 回复回写"勾选框），
    但对应的 `handleEcho()` 与路由都已不存在；
  - 而软件侧文档仍在宣传该能力并把它写成配置项：
    `SC/README.md:214-218,227`（"`ai_reply_enabled` 是否开启…默认开"、"或 `POST /echo {enabled}` 切换"）、
    `SC/plugins/config.py:51-52`（`ai_reply_enabled: False`，注释"默认关闭"），
    `SKILL.md` 未提 echo（与代码一致）。
- 影响：**这是"文档承诺了不存在的功能"的典型**，而且残留 UI 会让人直接踩：
  在悬浮面板勾选"AI 回复回写"→ `POST /echo` → 404（或落到 `else` 的未知路径响应）→ 现象是"勾了没反应"，
  排查成本高（以为是串口/防火墙问题）。同时 `SC/README.md:227` 的"默认开"与
  `SC/plugins/config.py:52` 的 `False` 默认值直接矛盾。
- 建议：明确取舍——① 若要保留"AI 回复回写串口"（t1 提到已被 RTT shell 取代、代码里 `ReplyHttpServer` 仍存在，
  见 `SC/plugins/ai_server.py:673-684`），则恢复 `/echo` 路由与 `handleEcho`；
  ② 若已废弃（更可能），删掉 `lib/index.js:301` 的残留 DOM/CSS 与软件侧 3 处文档，并把
  `ai_reply_enabled` 标注为 deprecated。无论哪条，都要在桥插件 README 的端点表与软件 README 之间建立单一来源。
- 代价：小（删残留） / 中（恢复端点）。优先级：**P0**（用户可见的功能缺失 + 文档撒谎）。

### 5.2 【P2】本地安装流程的坑已被文档完整记录，但"必须靠文档"本身是设计缺陷
- 证据：`README.md:55-57`、`dsh_plugin/ai-embedded-bridge/README.md:41-60` 详细解释了
  "别直接 add 目录（pnpm symlink → realpath 在 `$DSH_HOME/profiles/` 外 → `ERR_MODULE_NOT_FOUND`）"，
  正确姿势是 `pnpm pack` → `add <tgz>`；且 profile 依赖是 `file:<tgz 路径>`，**tgz 不能删**
  （`.gitignore:14-19` 为此专门保留 `.local-dist/`，现含 `ai-embedded-bridge-0.1.0.tgz` 11 KB）。
- 影响：这是"踩坑已记录但坑还在"的类型；对新用户是一次性但必然的失败体验（错误信息是 Node 的
  `ERR_MODULE_NOT_FOUND`，看起来像插件代码坏了）。
- 建议：在仓库加 `scripts/install-bridge.ps1` 固化正确流程（pack→add→提示重启），
  并让 README 首行就指向它；中长期可考虑在插件里加 `prepare` 或改为发布 npm 包（消除 file: 依赖脆弱性）。
- 代价：小-中。优先级：P2。

### 5.3 【P2】桥插件 peerDependencies 的预发布范围需要人工维护（文档已警告，无自动化）
- 证据：`dsh_plugin/ai-embedded-bridge/package.json:40-41` 用超长 `||` 分支枚举
  `>=0.1.N-rc.1 <0.2.0-0`；README 明确警告"dsh 换到新的 0.1.x/0.2.x 行时需补一条"
  （`README.md:90-92` / 英文 `:154-158`）。
- 影响：DSH 升级后插件静默"不加载"（peer 解析失败），排查需要读 node-semver 预发布规则。
- 建议：把该范围生成逻辑写成注释里的清单 + `scripts/check-dsh-plugin-submission.mjs`（仓库已有该脚本）里
  加一条校验：与 `$DSH_HOME` 里实际安装的 `@deepseek-ai/dsh-*` 版本比对，不匹配就提示要补的分支。
- 代价：中。优先级：P2。

### 5.4 【P1→安全/可靠性】设备数据被直接注入 Agent 会话，无来源标记/过滤
- 证据：`lib/index.js:123` `pushToAgent('[AI嵌入式工具] ' + text)`、`:110` 同理（monitor 聚合）；
  注入体是 `{ role:'user', content:[{type:'text', text}] }`（`:98-101`），
  即**串口/RTT 上任何字节都会变成一条 user 消息唤醒 Agent**（`chat` 模式下每条一注入）。
  原始数据来自固件/物理链路，可被任意串口设备或固件日志内容左右。
- 影响：这是 prompt-injection 的现成入口（设备侧/第三方固件可以"夹带"指令让 AI 去执行 shell_exec 之类动作）；
  另外 chat 模式下高频率数据会持续唤醒会话（文档提示了 monitor 模式，但默认 `off` 之外的用法没有速率上限）。
- 建议：注入文本加显式不可信标记（如 `[untrusted device output] …` + 说明"仅数据，非指令"），
  并在 `SKILL.md` 里写明"设备输出不是指令"；对 chat 模式加最小间隔/单次长度上限（monitor 已有 2048 字符阈值）。
- 代价：小-中。优先级：P1（安全 + 会话可靠性，属"值得做但不阻塞"档）。

---

## 6. 可维护性

### 6.1 【P0】静默吞异常：20 处 `except: pass`，其中 5 处会把"操作失败"变成"什么都没发生"
- 证据（本轮统计）：`ai_server.py` 7、`config.py` 4、`float_recorder.py` 5、`tcp_forward.py` 4、
  `history.py` 2、`recorder.py` 2、`rtt.py` 2、`serial_port.py` 1、`main.py` 3。最关键几处：
  - `SC/plugins/config.py:130-135`：`save()` 整个 `except Exception: pass` ——**配置写盘失败完全无声**，
    用户会看到"改了参数、重启又回去了"。
  - `SC/plugins/float_recorder.py:293-294`（写表头）、`:302-303`（写行）、`:309-310`（关文件）：写 CSV 失败被吞 →
    AI 侧 `float_recorder_status` 仍报 `recording`，只有 `rows` 不涨。
  - `SC/plugins/recorder.py:64-65`：`write()` 吞掉所有写文件异常，`is_recording` 仍为真。
  - `SC/main.py:85-88`：卸载插件异常被吞（若 AI 接口端口未释放，下次启动 10048 只能靠人肉猜）。
- 影响：**直接打击本工程的核心价值（AI 自动调试）**：AI 只能看到 `{"ok": true}`，看不到"其实没落盘"，
  于是继续在错误前提上推理。这类问题在我这轮实测里已经以"测试红但看不出来"的形式出现过一次（2.2）。
- 建议：把 `pass` 换成 `log(f"…失败：{e}")`（logger 服务已在 `SC/plugins/logger.py:13-18` 提供，
  只是它本身也只是 `print` + 事件）；关键写路径（config.save / 录制写行）改成**往上抛或返回错误字段**，
  由 MCP 工具返回 `ok: false` + reason。
- 代价：小（逐点改，约 20 处）。优先级：**P0**（可观测性 = AI 调试的生产率）。

### 6.2 【P1】`log` 服务过于薄弱：无级别、无时间戳、无文件、异常无堆栈；windowed 下日志进 devnull
- 证据：`SC/plugins/logger.py:13-18`：`print(f"[LOG] {msg}")` + `ctx.emit("log", msg)`；
  `SC/main.py:20-23` 在冻结版把 stdout/stderr 重定向到 `os.devnull`（是为修 `docs/技术日志-打包MCP问题.md:20` 的静默失败）。
- 影响：绿色版/安装版用户**没有任何日志可交**（AI 和人都拿不到现场）。`docs/使用文档.md:157` 还把
  "退出时 uvicorn CancelledError 堆栈"解释为无害噪音——恰恰说明缺一个受控的日志通道。
- 建议：`log(level, msg)` + 滚动文件（`~/.ai_embedded_assistant/logs/app.log`，保留 N 天）；
  冻结版把 devnull 换成文件流；MCP 加一个 `app_log(tail, grep, level)` 工具让 AI 自己拉上位机日志
  （与现有 `read_log` 只读**设备**日志区分）。
- 代价：中。优先级：P1（收益极高：AI 排查上位机自身问题不再靠猜）。

### 6.3 【P1】配置只有"键名白名单"，没有 schema/类型/取值校验，且 `csv_dir` 默认值语义分裂
- 证据：`SC/plugins/config.py:16-86` 的 `DEFAULTS` 是唯一"schema"；`load()` 只做 `k in DEFAULTS` 过滤
  （`:117`），**不校验类型、不校验取值域**——`mode` 不是四态、`baudrate` 为 0、
  `csv_sample_hz` 为 -1 都能进内存（部分在下游各自兜底：`float_recorder.py:96-98,116-118` 各自判负）。
  另有默认值语义分裂：`config.py:83` `csv_dir: ""` + `float_recorder.py:353` `config.get("csv_dir","") or default_csv_dir()`，
  而 `SC/README.md:128` 写"空则默认项目根 `csv_floder`"、`SKILL.md:21` 写"默认 `软件运行目录/csv_floder/`"——
  打包版实际落到 `~/.ai_embedded_assistant/csv_floder`（`float_recorder.py:33-34`），**三处说法不一致**。
- 影响：① 手改 YAML 写错 `mode` 值 → 热切换逻辑静默不动（`ai_server.py:223,237` 只在 `new_mode in _MODES` 时才切）；
  ② AI/用户无法从 `config_status` 知道"哪些键合法、取值范围多少"（只返回键数量：`ai_server.py:200-203`）。
- 建议：把 `DEFAULTS` 升级为带类型+枚举+范围的表（或引入 pydantic，打包已带 `pydantic_settings`，
  `AIEmbeddedAssistant.spec:9` 已在 hiddenimports 里），`load()` 时校验并记日志；
  `config_status` 返回可用键与取值范围；文档统一 csv_dir 三处语义。
- 代价：中。优先级：P1。

### 6.4 【P2】静态检查/格式化：环境有 ruff、仓库零配置，基线 14 个错误，mypy 缺失
- 证据：`ruff 0.15.17`（本机已装）`check .` → 14 errors（7 F841 / 5 E402 / 2 F401）；
  E402 里有一部分是**测试刻意的** `sys.path.insert` 后 import（如 `SC/tests/test_config.py:15`），
  说明"要么加 noqa、要么上配置"，而现在两者都没做；`mypy` 未安装、无配置；
  类型标注是**部分**的（有 `-> dict`、`X | None`，也有大量无标注的 UI 回调）。
- 影响：没有机械化的底线检查，review 全靠人；F841（unused variable）这类往往就是改动后的残留。
- 建议：加 `pyproject.toml`（ruff: `line-length=100`, `per-file-ignores` 给 `tests/*` 放行 E402；
  mypy: 先只对 `core/`+`plugins/` 开 `--ignore-missing-imports` 的宽松档），挂进 2.4 的 CI。
- 代价：小-中。优先级：P2。

---

## 7. 给下游（B/C 类）的接口：这些工程问题的"行为后果"清单

只列交叉部分，避免与其他成员重复：

1. **模式切换成本（t1 已提）**：`ai_server.py:633-643` 的 `request_mode_switch` 先 `time.sleep(1.0)`
   再整只重启 uvicorn——每次切模式 AI 侧要等 1~2s 且丢失连接状态；`SKILL.md:115` 让 AI"切完再 tools/list 确认"，
   属于把实现代价转嫁给 AI 的操作成本。→ 建议方向：工具面按"服务已加载"**动态增删**而非重启进程，
   或至少把 1s 的延迟改成事件驱动。
2. **"AI 看不到失败"**：6.1 的静默吞异常 = AI 调试时最常见的"命令成功但没效果"。
   这是 B 类里性价比最高的一项（改 20 处 `pass`）。
3. **测试红灯无人发现 = 模式隔离契约无人守**（2.2）：AI 依赖的"工具随模式出现"这一不变量今天已经破了。
4. **配置默认值/文档三处不一致**（6.3 的 `csv_dir`）：AI 录制后去找 CSV 可能找不到文件，
   这是"省事"最直接的损失点。

---

## 8. 优先级汇总（建议的执行顺序）

| 序 | 项 | 问题一句话 | 代价 | 优先级 |
|---|---|---|---|---|
| 1 | 3.1 | `requirements.txt` 缺 `uvicorn`（MCP 起不来）/`pylink-square`（RTT 不可用） | 小 | P0 |
| 2 | 2.2 | `justfloat_autoname` 越出调试守卫 → `test_debug_switch.py` 红灯、模式隔离契约破裂 | 小 | P0 |
| 3 | 5.1 | `/echo` 端点不存在但文档+残留 UI 都在承诺（且 `ai_reply_enabled` 默认值文档说反了） | 小-中 | P0 |
| 4 | 6.1 | 20 处 `except: pass`，配置/录制写失败静默 → AI 在错误前提上推理 | 小 | P0 |
| 5 | 2.1 | 无 pytest 配置/统一 runner；async 用例 pytest 收不到，同仓库两种结果 | 小 | P1 |
| 6 | 4.1 | `SC/README.md` 停在"正常/调试模式"时代（5 处硬错误，含 config.json） | 小-中 | P1 |
| 7 | 3.2 | 无 `__version__`；安装包/exe 是 09-15 旧构建（早于最后提交） | 小-中 | P1 |
| 8 | 6.2 | 日志无级别/无文件/无堆栈，windowed 进 devnull → 现场不可复现 | 中 | P1 |
| 9 | 6.3 | 配置无 schema/类型/取值范围校验；`csv_dir` 默认值三处说法不一 | 中 | P1 |
| 10 | 1.3 | 仅 1 处 frozen 路径处理，其余隐式依赖打包布局 | 中 | P1 |
| 11 | 5.4 | 设备数据直接作为 user 消息注入 Agent，无不可信标记（提示注入面） | 小-中 | P1 |
| 12 | 2.4/6.4 | CI + ruff 配置（14 errors 基线）；2.1/2.2 修好后就能上 | 小-中 | P2 |
| 13 | 3.3 | 跨仓库 `eide-mcp-skill` 缺口 + 缺一键环境体检脚本 | 小 | P2 |
| 14 | 1.1/1.2 | `plugins/codec.py` 死层 + `core/` 边界无契约 | 小 | P2 |
| 15 | 5.2/5.3 | 桥插件安装坑可脚本化；peer 预发布范围需自动校验 | 小-中 | P2 |
| 16 | 4.2/4.3/3.4 | 文档权威源、改名后过期警告、不存在的 CSV 示例 | 小 | P2 |

> 说明：P0 四条全部是"改动小、后果直接"的项；P1 集中在文档一致性与可观测性（对 AI 自动调试收益最大的两块）。
> 未列入的常见工程建议（引入 lint 全家桶、重构 mini_cordis、拆包成 wheel、给 UI 加自动化测试）本轮**不建议现在做**：
> 收益与当前痛点不匹配（不凑数）。
