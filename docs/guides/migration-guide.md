# 旧版迁移与回退指南

## 不自动迁移

新版不会读取、改写或删除旧 `$HOME/.hwskills`、Profile、Hook、MCP 配置或已经安装的技能。迁移 preview 只读取指定旧仓库并在新目录生成候选与 `unconvertible.json`；它不修改旧树，也不替用户决定公开性、许可证或未知安装方式。

## 从固定旧版生成 preview

旧版历史固定在 tag `hwskill-legacy-v0.1.0`，期望 commit 为 `45825a59489e96160ad73b7074717ddedd292ef5`。先在独立 checkout 核对：

```sh
git clone --no-checkout https://gitcode.com/linkeo2012/hwskills.git /tmp/hwskill-legacy-source
git -C /tmp/hwskill-legacy-source checkout --detach hwskill-legacy-v0.1.0
test "$(git -C /tmp/hwskill-legacy-source rev-parse HEAD)" = \
  45825a59489e96160ad73b7074717ddedd292ef5
```

再由新版命令读取旧 checkout，输出路径必须不存在、不能与输入重叠：

```sh
hwskill-directory migration-preview \
  --repo-root /tmp/hwskill-legacy-source \
  --out /tmp/hwskill-migration-preview \
  --json
```

逐项审阅 candidates 和异常清单。没有明确 publicity 证据、正文缺失、来源 SHA/摘要不一致、未知 locator/install 的项目都需要人工处理，不能直接发布。

## 旧版运行回退

仅在确实需要旧 Profile/Hook/MCP 行为时，使用新版安装器的显式 legacy 分支和独立目标：

```sh
./install.sh --legacy \
  --install-path="$HOME/.local/share/hwskill-legacy-v0.1.0"
```

不要把该路径设为新版 checkout 或新版安装目录。安装器必须在执行旧脚本前 detached checkout 固定 tag 并核对完整 SHA。旧版不承诺兼容未来 Codex、Claude Code 或 OpenCode 版本。

回退不会卸载新版，也不会恢复或改写用户旧配置。删除任何旧 checkout、配置、Hook、MCP 或技能目录前，必须另行备份并取得用户明确授权。
