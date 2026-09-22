---
schema_version: 1
skill_id: superpowers/finishing-a-development-branch
translated_at: 2026-09-21
---

# 完成开发分支

## 概述

**核心原则：**验证测试 → 检测环境 → 展示选项 → 执行选择 → 清理。

**开始时说明：**“I'm using the finishing-a-development-branch skill to complete this work.”

## 第 1 步：验证测试

运行项目完整测试套件（`npm test` / `cargo test` / `pytest` / `go test ./...`）。

**如果测试失败，**报告失败并停止——绿色套件之后才能展示选项菜单：

```
Tests failing (<N> failures). Must fix before completing:

[Show failures]
```

**如果测试通过：**继续第 2 步。

## 第 2 步：检测环境

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
# 仍在工作区时立即记录；第 5 步会先切换目录，
# 第 6 步清理需要此值
WORKTREE_PATH=$(git rev-parse --show-toplevel)
```

这决定要展示哪个菜单，以及如何清理：

| 状态 | 菜单 | 清理 |
|-------|------|---------|
| `GIT_DIR == GIT_COMMON`（普通仓库） | 标准 3 个选项 | 没有 worktree 需要清理 |
| `GIT_DIR != GIT_COMMON`，具名分支 | 标准 3 个选项 | 基于来源判断（见第 6 步） |
| `GIT_DIR != GIT_COMMON`，detached HEAD | 精简为 2 个选项（不能合并） | 由外部管理——原地保留 |

## 第 3 步：确定基础分支

基础分支就是本次工作分叉自的分支，通常已写在计划、对话或分支 upstream 中。如果仍不清楚，询问：“This branch split from <your best guess> - is that correct?”

合并前必须确认；合并进错误基础分支的撤销成本很高。

## 第 4 步：展示选项

**普通仓库和具名分支 worktree——准确展示以下 3 个选项：**

```
Implementation complete. What would you like to do?

1. Merge back to <base-branch> locally
2. Push and create a Pull Request
3. Keep the branch as-is (I'll handle it later)

Which option?
```

**Detached HEAD——准确展示以下 2 个选项：**

```
Implementation complete. You're on a detached HEAD (externally managed workspace).

1. Push as new branch and create a Pull Request
2. Keep as-is (I'll handle it later)

Which option?
```

菜单必须按原文简洁展示，且只能包含以上列表中的选项。只有人类协作者明确要求丢弃工作时，才进入“丢弃”流程。等待对方选择；集成决策属于对方。

## 第 5 步：执行选择

### 选项 1：本地合并

```bash
# 为 CWD 安全获取主仓库根目录
MAIN_ROOT=$(git -C "$(git rev-parse --git-common-dir)/.." rev-parse --show-toplevel)
cd "$MAIN_ROOT"

# 先合并——确认成功后才能删除任何内容
git checkout <base-branch>
git pull
git merge <feature-branch>

# 在合并结果上验证测试
<test command>
```

如果合并结果测试失败：停止，保留 worktree 和分支并调查。尚未推送，因此本地合并仍可恢复。

合并结果变绿后：清理 worktree（第 6 步），再删除分支：

```bash
git branch -d <feature-branch>
```

### 选项 2：推送并创建 PR

```bash
git push -u origin <feature-branch>
# Detached HEAD 时，在远端命名新分支：
# git push origin HEAD:refs/heads/<new-branch>
```

随后使用代码托管平台工具针对 `<base-branch>` 创建 pull/merge request：优先使用可用 CLI，否则使用推送时输出的创建 URL；遵循仓库 PR 模板和约定，并向协作者报告 URL。

保留 worktree——PR 反馈要在其中继续修改。

### 选项 3：保持原状

报告：“Keeping branch <name>. Worktree preserved at <path>.”

### 如果人类协作者要求丢弃工作

这条路径只响应明确的丢弃请求。先确认：

```
This will permanently delete:
- Branch <name>
- All commits: <commit-list>
- Worktree at <path>

Type 'discard' to confirm.
```

等待对方准确输入 `discard`。收到后执行：

```bash
MAIN_ROOT=$(git -C "$(git rev-parse --git-common-dir)/.." rev-parse --show-toplevel)
cd "$MAIN_ROOT"
```

然后清理 worktree（第 6 步），强制删除分支：

```bash
git branch -D <feature-branch>
```

## 第 6 步：清理工作区

**仅用于选项 1 和已确认的丢弃。**选项 2、3 始终保留 worktree。两个调用方都已切换到主仓库根目录——删除 worktree 必须在其外部执行——并使用第 2 步在切换目录前记录的 `GIT_DIR`、`GIT_COMMON` 和 `WORKTREE_PATH`。

**如果 `GIT_DIR == GIT_COMMON`：**普通仓库，无需清理 worktree。结束。

**如果 `WORKTREE_PATH` 位于 `.worktrees/` 或 `worktrees/` 下：**Superpowers 创建了该 worktree，由我们负责清理：

```bash
git worktree remove "$WORKTREE_PATH"
git worktree prune  # 自愈：清理过期注册
```

**如果删除被拒绝**（`contains modified or untracked files`）：worktree 中存在没有保存在其他地方的文件——未提交计划、笔记或草稿。绝不要自行添加 `--force`。展示可能丢失的内容并询问：

```bash
git -C "$WORKTREE_PATH" status --porcelain -uall
```

```
Worktree removal refused — these files were never committed:

<file list>

1. Commit them to <branch> before cleanup
2. Move them into <main repo root>
3. Delete them (unrecoverable)

Which?
```

执行选择后，再删除 worktree。

**否则：**工作区属于宿主环境，原地保留。如果平台提供退出工作区工具，使用它。

## 快速参考

| 选项 | 合并 | 推送 | 保留 Worktree | 清理分支 |
|--------|-------|------|---------------|---------------|
| 1. 本地合并 | 是 | - | - | 是 |
| 2. 创建 PR | - | 是 | 是 | - |
| 3. 保持原状 | - | - | 是 | - |
| 丢弃（仅限明确请求） | - | - | - | 是（强制） |

## 常见借口

| 借口 | 事实 |
|---------|---------|
| “测试在本次会话早些时候通过了” | 在即将集成的树上重新运行。绿色运行只证明当时运行的那棵树。 |
| “他们显然想合并” | 集成由人类协作者决定。展示菜单并等待。 |
| “功能看起来做完了，我可以主动提供丢弃选项” | 菜单本身已经完整。只有对方明确要求时才丢弃。 |
| “‘行，删掉吧’算确认” | 只有准确输入 `discard` 才授权删除。 |
| “PR 已创建，worktree 只会碍事” | PR 反馈要在该 worktree 中修复，工作合入前一直保留。 |
| “另一个 worktree 看起来过期了，我也清理掉” | 只清理 `.worktrees/` 或 `worktrees/` 下属于本次流程的 worktree。其他工作区属于宿主。 |
| “删除被拒绝，`--force` 只是完成清理” | 拒绝意味着文件只存在于该 worktree。`--force` 会永久销毁它们。展示给协作者并询问。 |
| “合并后测试失败可能只是偶发” | 合并结果失败会停止一切。调查期间保留分支和 worktree。 |
| “基础分支显然是 main” | 确认分叉点或询问。合并进错误基础分支代价高昂。 |
| “推送被拒绝，强推就能解决” | 拒绝意味着远端已移动。先调查；只有人类协作者明确要求时才能强推。 |
