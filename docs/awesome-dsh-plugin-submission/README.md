# awesome-dsh-plugin 投稿文件

这个目录里只有一样东西：给 [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)
提 PR 时用的**条目文件**。它不是本仓库的构建产物，拉下来也不会被本仓库的任何脚本读取。

## 怎么投

1. Fork `awesome-dsh-plugin/awesome-dsh-plugin`。
2. 把 `liciqikuanren__ai_embedded_assistant--dsh_plugin-ai-embedded-bridge.yml`
   **原样**放到 `data/plugins/` 下（文件名必须与 url 推导出来的 slug 一致，别改名）。
3. 提 PR。README 由维护者在 main 上重新生成，**不要手工改 README**，一个 PR 最多加 3 条。

## 这个文件名是怎么来的

`scripts/lib/entries.mjs` 的 `slugFor()`：`owner/repo` → `owner__repo`，
`/tree/<branch>/<sub/path>` 的 `<sub/path>` 里的 `/` 换成 `-`：

```
https://github.com/liciqikuanren/ai_embedded_assistant/tree/main/dsh_plugin/ai-embedded-bridge
  -> liciqikuanren__ai_embedded_assistant--dsh_plugin-ai-embedded-bridge
```

## url 为什么指向子目录

本仓库根目录没有 `package.json`，`dsh.bundle` 清单在 `dsh_plugin/ai-embedded-bridge/package.json` 里。
CI（`scripts/check-submission.mjs`）对指向仓库根的条目会扫描全树找到清单，然后**明确要求**把 url
改成那个子包——因为 `dsh plugin add github:owner/repo` 装的是根目录，指向子包才装得上。
monorepo 子包正是这样收录的。

## 还需人工做的两件事

- 给仓库打上 **`dsh-plugin`** topic（GitHub 仓库页 → About → Topics）。
- PR 里只加这一个文件；描述里的每一句话都会被对着源码核。
