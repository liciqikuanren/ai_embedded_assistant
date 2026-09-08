# 技术日志：PyInstaller windowed 打包版 MCP 服务无法启动

- 日期：2026-09-08
- 场景：AI嵌入式助手（原串口助手）改名重组后重新打包，发现 **windowed（无控制台）绿色版/安装版启动后 GUI 显示“服务运行中 http://127.0.0.1:8765/mcp”，但 8765 端口实际无监听**，MCP 工具全部不可用。
- 环境：Windows 10，Python 3.14.2，PyInstaller 6.20.0，mcp 1.27.1（fastmcp），uvicorn 0.32.0，pyserial 3.5。

## 1. 现象

| 启动方式 | 8765 MCP | 说明 |
|---|---|---|
| 源码 `python main.py` | ✅ ~0.8s 就绪，`tools/list` 10 个工具 | 正常 |
| 安装版 / windowed 绿色版 exe | ❌ 无监听 | GUI 状态为“乐观显示”，并非真实监听 |

## 2. 排查过程

1. **端口探测**：`netstat -ano`、`Get-NetTCPConnection` 均无 8765；`127.0.0.1 / localhost / ::1 / 0.0.0.0` 全连不上 → 服务确实没起。
2. **源码对照**：`python main.py` 0.8s 起 MCP 且工具齐全 → 应用代码本身没问题。
3. **误判记录（教训）**：一开始查看 `_internal` 目录认为“缺少 mcp/serial 等依赖”——**此判断不成立**：PyInstaller onedir 的纯 Python 模块打进 exe 内部归档（PYZ），`_internal` 只放含二进制/数据的包（如 PyQt6、pydantic_core），不能凭目录判断依赖是否打包。
4. **console 诊断版**：临时把 spec 的 `console=False` 改 `console=True` 重打，运行后 **8765 正常、10 个工具可用** → 问题只出在 windowed 形态。
5. **定位根因**：PyInstaller windowed（`console=False`）下 `sys.stdout` / `sys.stderr` 为 `None`。FastMCP/uvicorn 等库启动时的日志写入（logging 的 lastResort 等）遇到 None 流会抛错，且 windowed 无控制台、错误被吞 → MCP 服务静默启动失败。
6. **修复并验证**：应用启动时把 None 的 stdout/stderr 兜底重定向到 `os.devnull`；重新打包 windowed 版后 **8765 ~1s 就绪，`tools/list` 返回 10 个工具**。

## 3. 修复内容

### 3.1 `software/code/main.py`（stdout/stderr 守卫）

```python
# PyInstaller windowed（无控制台）下 stdout/stderr 为 None，会让日志/uvicorn 静默失败：
# 兜底重定向到 devnull，保证冻结版里 MCP 等后台服务可正常启动。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
```

### 3.2 `software/code/AIEmbeddedAssistant.spec`（hiddenimports 补全）

运行时装 `plugins/` 是按文件动态 import 的，PyInstaller 静态分析看不到它们引用的第三方库，需手动收集：

```python
hiddenimports=[
    # 业务插件
    'plugins.recorder', 'plugins.serial_port', 'plugins.transmitter', 'plugins.history',
    'plugins.justfloat', 'plugins.float_recorder', 'plugins.tcp_forward', 'plugins.rtt',
    'plugins.logger', 'plugins.codec', 'plugins.commands', 'plugins.config', 'plugins.ai_server',
    # MCP 运行栈（ai_server 运行时 import，静态分析不可见）
    'serial', 'uvicorn',
    'mcp.server.fastmcp', 'mcp.server.streamable_http',
    'pydantic_settings', 'sse_starlette',
]
```

## 4. 经验教训 / 避坑

1. **windowed 应用先兜底 stdout/stderr**：冻结版里它们可能是 `None`，任何写流操作都会静默崩；打包 GUI+后台服务（MCP/uvicorn）的应用必加守卫。
2. **“依赖没打进去”不能只看 `_internal`**：纯 Python 包在 exe 内部归档里；确认真实情况应跑起来测（端口/`tools/list`），或临时 console 版抓日志。
3. **动态加载插件的依赖要进 hiddenimports**：`load_dir` / 运行时 `import_module` 引用的第三方包（此处 `mcp`/`uvicorn`/`pyserial`）不会被静态分析收集。
4. **诊断窗口化应用**：把 spec `console=False→True` 出个 console 版抓真实异常，是最快的定位手段（用后删除）。
5. **验证标准**：exe 启动 → 快速 TCP 探测 `127.0.0.1:8765` → `tools/list` 能返回工具列表 → 通过后关进程确认端口释放。

## 5. 复现 / 验证命令

```powershell
# 打包（在 software/code 下）
python -m PyInstaller --noconfirm AIEmbeddedAssistant.spec

# 验证：启动 exe 后用快速 TCP 探测 + tools/list
$exe = "...\software\dist\AIEmbeddedAssistant\AIEmbeddedAssistant.exe"
$p = Start-Process $exe -PassThru
# 等待 8765 就绪后请求：
# POST http://127.0.0.1:8765/mcp  Accept: application/json, text/event-stream
# {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
Stop-Process -Id $p.Id -Force
```

> 备注：旧的安装版/绿色版均为修复前构建，MCP 不可用；请使用重新打包的 `software/dist/AIEmbeddedAssistant/`。
