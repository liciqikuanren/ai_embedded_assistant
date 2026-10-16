# ai-embedded-bridge

把 **AI嵌入式助手**（本仓库的串口 / RTT（J-Link 或 DAPLink）上位机调试软件）接进 DeepSeek Harness（dsh）：
上位机把收到的设备数据经 HTTP 推给 dsh，插件把数据注入**指定的 Agent 会话**，并在 dsh Web UI
右下角挂一个悬浮面板（关闭 / 聊天 / 监听 + 会话选择）。

A dsh bundle that bridges the **AI Embedded Assistant** desktop debug tool into a live dsh session:
the tool POSTs device data over HTTP, the plugin injects it into a chosen agent session, and a
floating panel in the dsh web UI switches the bridge off / per-message (`chat`) / batched (`monitor`).

---

## 中文

### 它做什么

- **注入方向**：上位机 → dsh。串口 / RTT 收到的内容作为一条 user 消息 `followup` 给目标 Agent，
  会话因此被唤醒并回话，AI 可以据此分析设备输出。
- **总开关在 dsh 侧**：`mode` 只有 `off` / `chat` / `monitor`，默认 `off`。
  上位机推送里带的任何 mode 字段都被忽略，避免绕过 dsh 侧的开关。
- **`chat`**：每来一条数据就注入一条消息并唤醒 Agent。
- **`monitor`**：数据先进缓冲，缓冲达到 2048 字符时、或 5 秒定时器到点时合并成一条 `[AI嵌入式工具·监控] …` 注入。
  `monitorMs` 用于覆盖定时器间隔，但**定时器在挂载时创建**，之后通过 `/mode` 改 `monitorMs` 不会重设已在跑的那个定时器
  （重新加载插件即可生效）。
- **目标会话**：`targetAgentId` 指定；留空 = 自动选最新一个会话。

### 安装

前置：dsh 的 **web** profile（本插件注册在 `webServer` 上，并用 `webServer.tapIndex` 往 index.html 注入面板）。

正常安装（GitHub / npm 来源，也是市场里那条命令）：

```bash
dsh plugin --profile web add github:<owner>/<repo>
```

`dsh plugin add` 会在 profile 目录里跑一次 pnpm 安装，然后按 `dsh.bundle` 声明把这个包加进
profile 的 `dsh.profile.bundles` 分层列表。装完**重启 dsh** 生效。
本包没有 `prepare` / 构建脚本，所以不需要 `allowBuilds` 构建授权。

**本地源码/离线安装**：先打包再装，`pnpm pack` 出来的 tgz 会被 pnpm 展开成 profile 里的真实安装：

```bash
cd dsh_plugin/ai-embedded-bridge && pnpm pack
dsh plugin --profile web add /abs/path/ai-embedded-bridge-0.1.0.tgz
```

装进去的是 **tgz 的快照**，不是软链，所以改完 `lib/index.js` 要重新走一遍
`pnpm pack` → `dsh plugin --profile web add <tgz>` → 重启 dsh 才生效。
另外 profile 里那条依赖是 `file:<tgz 路径>`，**这个 tgz 不能删**，
否则之后在该 profile 跑任何 pnpm 安装都会解析失败。

> ⚠️ **不要**直接 `dsh plugin --profile web add <本目录的绝对路径>`。
> pnpm 对这种本地目录只建一个**符号链接**，而 Node 解析模块时用的是符号链接指向的**真实路径**；
> 真实路径在 `$DSH_HOME/profiles/` 之外，于是 dsh 维护的
> `$DSH_HOME/profiles/node_modules`（它镜像了整个 harness 依赖闭包，插件就是靠向上查找到
> `@deepseek-ai/*` 的）不在查找路径上，启动时会以
> `ERR_MODULE_NOT_FOUND: Cannot find package '@deepseek-ai/dsh-llm'` 直接失败。
> 手动安装同理：把目录**拷贝**（而不是链接）到 `$DSH_HOME/profiles/web/node_modules/ai-embedded-bridge`
> 是可以的，因为拷贝后的真实路径在 `profiles/` 里面。

市场 / GitHub 安装：本插件已按 [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)
的投稿要求声明了 `dsh.bundle` 清单，收录后可以直接在插件市场里一键安装。

### HTTP 端点

全部挂在 `http://127.0.0.1:3080/plugins/ai-embedded-bridge` 下，**只接受 POST**，
`Content-Type: application/json`，请求体上限 1 MiB。

| 端点 | 请求体 | 说明 |
|---|---|---|
| `/incoming` | `{ "text": "..." }` | 推送一条设备数据。`off` 直接丢弃；`chat` 立即注入；`monitor` 进缓冲 |
| `/mode` | `{ "mode": "off\|chat\|monitor", "monitorMs": 2000 }` | 切总开关 / 模式；`monitorMs` ≥ 500 才生效，默认 5000 |
| `/target` | `{ "agentId": "..." }` | 指定注入会话，`""` = 自动（最新会话）；未知 id 直接报错 |
| `/status` | `{}` | 返回 `mode` / `monitorMs` / `bufferedChars` / `targetAgentId` / `agentCount` / `agents[]` / `latestAgentId` |

上位机侧默认推送地址就是 `http://127.0.0.1:3080/plugins/ai-embedded-bridge/incoming`。

### 悬浮面板

`webServer.tapIndex` 往页面注入的纯 vanilla JS 面板（无前端构建、不依赖 dsh 前端源码）：
右下角胶囊显示状态（灰=关闭 / 绿=聊天 / 黄=监听），点开是模式三选一、目标会话下拉 + 刷新、
以及在线会话数 / 缓冲字符数；每 3 秒自动拉一次 `/status`。

### 兼容性

`peerDependencies` 里的官方 `@deepseek-ai/*` 包**必须由 dsh 本体提供**（不放进 `dependencies`，
否则会装出第二份运行时，插件的模块实例就和宿主对不上）。

范围写成显式的预发布分支，因为 node-semver 只有在范围里存在一个**元组相同且自身带预发布标签**的
比较符时才会放行预发布版本——`^0.1.0-rc.7`、`>=0.1.0-rc.1 <0.2.0` 这类看着很宽的范围都会静默排除
`0.1.5-rc.2`。**dsh 换到新的 `0.1.x` / `0.2.x` 行时，需要往这个分支列表里补一条。**

---

## English

### What it does

- **Direction**: desktop tool → dsh. Data received over serial / RTT (J-Link or DAPLink) becomes a user message
  `followup`-ed into the target agent session, so the session wakes up and the model can reason about
  the device output.
- **The switch lives on the dsh side.** `mode` is `off` (default) / `chat` / `monitor`. Any mode field
  in the pushed payload is ignored on purpose, so the desktop tool cannot bypass the switch.
- **`chat`** injects one message per push. **`monitor`** buffers and flushes either when the buffer
  reaches 2048 characters or on a 5-second timer. `monitorMs` overrides that interval, but the timer
  is created once at mount, so changing `monitorMs` through `/mode` does not retime the running timer
  (reload the plugin to apply it).
- **Target session**: `targetAgentId`, or empty for "the newest session".

### Install

Requires the dsh **web** profile — the plugin registers routes on `webServer` and taps `index.html`
via `webServer.tapIndex` for its panel.

```bash
dsh plugin --profile web add github:<owner>/<repo>
```

`dsh plugin add` runs a pnpm install inside the profile and then adds this package to the profile's
`dsh.profile.bundles` layer list because of the `dsh.bundle` declaration. **Restart dsh** afterwards.
There is no `prepare` or build script, so no `allowBuilds` approval is needed.

For a local checkout, pack first — pnpm expands the tarball into a real installation inside the profile:

```bash
cd dsh_plugin/ai-embedded-bridge && pnpm pack
dsh plugin --profile web add /abs/path/ai-embedded-bridge-0.1.0.tgz
```

What lands in the profile is a **snapshot of the tarball**, not a symlink to your checkout, so after
editing `lib/index.js` repeat `pnpm pack` → `dsh plugin --profile web add <tgz>` → restart dsh.
The profile's dependency is `file:<the tgz>`, so **keep the tarball**: removing it makes every later
pnpm install in that profile fail to resolve.

> ⚠️ Do **not** run `dsh plugin --profile web add <path to this directory>`. For a local directory
> pnpm only creates a **symlink**, and Node resolves modules through the symlink's **realpath**.
> That realpath sits outside `$DSH_HOME/profiles/`, so dsh's `$DSH_HOME/profiles/node_modules`
> — the mirror of the whole harness dependency closure that plugins rely on to resolve
> `@deepseek-ai/*` — is not on the lookup path, and startup fails with
> `ERR_MODULE_NOT_FOUND: Cannot find package '@deepseek-ai/dsh-llm'`.
> Manual installs have the same rule: **copy** (don't link) the directory to
> `$DSH_HOME/profiles/web/node_modules/ai-embedded-bridge`, where the realpath is inside `profiles/`.

### HTTP endpoints

All under `http://127.0.0.1:3080/plugins/ai-embedded-bridge`, **POST only**,
`Content-Type: application/json`, 1 MiB body limit: `/incoming`, `/mode`, `/target`, `/status`
(see the Chinese table above for payloads).

### Compatibility

The official `@deepseek-ai/*` packages are `peerDependencies`, never `dependencies` — installing a
second copy would give the plugin a different module instance than the host. The range is written as
explicit prerelease branches because node-semver only admits a prerelease when some comparator shares
its exact `major.minor.patch` tuple and carries a prerelease tag of its own; broad-looking ranges such
as `^0.1.0-rc.7` silently exclude `0.1.5-rc.2`. **Extend the branch list when dsh moves to a new
`0.1.x` / `0.2.x` line.**

## 许可证

MIT
