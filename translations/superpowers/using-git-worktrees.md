---
schema_version: 1
skill_id: superpowers/using-git-worktrees
translated_at: 2026-09-21
---

# 使用 Git Worktree

## 概述

确保工作在隔离的工作区中进行。优先使用平台原生的 worktree 工具；只有没有原生工具时，才退回手动 Git worktree。

**核心原则：**先检测现有隔离，再使用原生工具，最后才退回 Git。绝不要与运行环境对抗。

**开始时说明：**“I'm using the using-git-worktrees skill to set up an isolated workspace.”

## 第 0 步：检测现有隔离

**创建任何内容前，检查是否已在隔离工作区中。**

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
BRANCH=$(git branch --show-current)
```

**子模块防护：**在 Git 子模块内，`GIT_DIR != GIT_COMMON` 同样成立。在判断“已经位于 worktree”前，先确认不是子模块：

```bash
# 如果返回路径，你位于子模块而不是 worktree——按普通仓库处理
git rev-parse --show-superproject-working-tree 2>/dev/null
```

**如果 `GIT_DIR != GIT_COMMON`（且不是子模块）：**已经位于链接 worktree。跳到第 2 步（项目设置），**不要**再创建 worktree。

报告分支状态：

- 位于分支：“Already in isolated workspace at `<path>` on branch `<name>`.”
- detached HEAD：“Already in isolated workspace at `<path>` (detached HEAD, externally managed). Branch creation needed at finish time.”

**如果 `GIT_DIR == GIT_COMMON`（或位于子模块）：**当前是普通仓库检出。

检查用户是否已在指令中表达 worktree 偏好。如果没有，创建 worktree 前先征求同意：

> “Would you like me to set up an isolated worktree? It protects your current branch from changes.”

如果已有明确偏好，直接遵循，无需再次询问。如果用户拒绝，在当前目录工作并跳到第 2 步。

## 第 1 步：创建隔离工作区

**按以下顺序尝试两种机制。**

### 1a. 原生 Worktree 工具（优先）

用户已经同意使用隔离工作区（第 0 步）。检查现有工具中是否有 `EnterWorktree`、`WorktreeCreate`、`/worktree` 命令或 `--worktree` 选项等 worktree 创建能力。如果有，使用它并跳到第 2 步。

原生工具会处理目录位置、分支创建和清理。存在原生工具时使用 `git worktree add`，会产生运行环境无法看到或管理的幽灵状态。

只有确实没有原生 worktree 工具时，才能进入 1b。

### 1b. Git Worktree 后备方式

**仅当 1a 不适用时使用**——也就是没有原生 worktree 工具。用 Git 手动创建 worktree。

#### 选择目录

按以下优先级处理。用户明确偏好始终高于观察到的文件系统状态。

1. **检查指令中声明的 worktree 目录偏好。**如果用户已经指定，直接使用，无需询问。
2. **检查现有的项目内 worktree 目录：**

   ```bash
   ls -d .worktrees 2>/dev/null     # 首选（隐藏目录）
   ls -d worktrees 2>/dev/null      # 备选
   ```

   如果存在就使用；两者都存在时，选择 `.worktrees`。
3. **没有其他指导时，**默认使用项目根目录的 `.worktrees/`。

#### 安全验证（仅适用于项目内目录）

创建前**必须**验证目录被 Git 忽略：

```bash
git check-ignore -q .worktrees 2>/dev/null || git check-ignore -q worktrees 2>/dev/null
```

**如果没有被忽略：**把目录加入 `.gitignore`，提交该变更，然后继续。

**这很关键：**避免把整个 worktree 内容意外提交进仓库。

#### 创建 Worktree

```bash
# 根据选定位置确定路径
path="$LOCATION/$BRANCH_NAME"

git worktree add "$path" -b "$BRANCH_NAME"
cd "$path"
```

**沙箱后备：**如果 `git worktree add` 因权限错误（沙箱拒绝）失败，告知用户沙箱阻止了 worktree 创建，改在当前目录工作；然后原地执行设置和基线测试。

## 第 2 步：项目设置

自动检测并运行适当的设置：

```bash
# Node.js
if [ -f package.json ]; then npm install; fi

# Rust
if [ -f Cargo.toml ]; then cargo build; fi

# Python
if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
if [ -f pyproject.toml ]; then poetry install; fi

# Go
if [ -f go.mod ]; then go mod download; fi
```

## 第 3 步：验证干净基线

运行测试，确保工作区起点干净：

```bash
# 使用适合项目的命令
npm test / cargo test / pytest / go test ./...
```

**如果测试失败：**报告失败，并询问是继续还是调查。

**如果测试通过：**进入第 2 步之后的工作。

### 报告

```
Worktree ready at <full-path>
Tests passing (<N> tests, 0 failures)
Ready to implement <feature-name>
```

## 快速参考

| 情况 | 行动 |
|-----------|--------|
| 已在链接 worktree 中 | 跳过创建（第 0 步） |
| 位于子模块 | 按普通仓库处理（第 0 步防护） |
| 有原生 worktree 工具 | 使用它（1a） |
| 没有原生工具 | 使用 Git worktree 后备（1b） |
| 存在 `.worktrees/` | 使用它（先验证已忽略） |
| 存在 `worktrees/` | 使用它（先验证已忽略） |
| 两者都存在 | 使用 `.worktrees/` |
| 两者都不存在 | 检查指令，然后默认 `.worktrees/` |
| 目录未被忽略 | 加入 `.gitignore` 并提交 |
| 创建时权限错误 | 退回沙箱方案，在当前目录工作 |
| 基线测试失败 | 报告失败并询问 |
| 没有 package.json/Cargo.toml | 跳过依赖安装 |

## 常见借口

| 借口 | 事实 |
|---------|---------|
| “显然不在 worktree，无需检查” | 运行第 0 步。运行环境创建的隔离和子模块都可能误导肉眼判断，命令才是依据。 |
| “`git worktree add` 比找原生工具快” | 原生工具负责位置、分支和清理。绕过它是最常见的错误，会产生运行环境无法管理的幽灵状态。 |
| “worktree 目录肯定已经被忽略” | 运行 `git check-ignore`。未忽略的目录会把整棵工作树纳入仓库。 |
| “目录名随便用” | 明确指令优先，其次是已有项目内目录，最后才是 `.worktrees/` 默认值。 |
| “工作区很新，基线测试可以以后跑” | 脏基线会让之后每个失败都含义不明。现在运行测试；是否带着失败继续由人类协作者决定。 |
