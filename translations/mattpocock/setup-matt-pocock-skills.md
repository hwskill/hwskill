---
schema_version: 1
skill_id: mattpocock/setup-matt-pocock-skills
translated_at: 2026-09-21
---

# 设置 Matt Pocock 技能

为工程技能建立它们假定存在的仓库级配置：

- **Issue 跟踪器**：issue 位于哪里（默认 GitHub；也原生支持本地 Markdown）
- **分诊标签**：五种规范分诊角色使用的字符串
- **领域文档**：`CONTEXT.md` 与 ADR 的位置，以及读取它们的规则

这是提示词驱动的技能，不是确定性脚本。先探索并展示发现，向用户确认，然后写入。

## 流程

### 1. 探索

查看当前仓库以了解起点。读取已有内容，不要作假设：

- `git remote -v` 和 `.git/config`：这是 GitHub 仓库吗？具体是哪一个？
- 仓库根目录的 `AGENTS.md` 和 `CLAUDE.md`：是否存在？其中是否已有 `## Agent skills` 一节？
- 根目录的 `CONTEXT.md` 和 `CONTEXT-MAP.md`
- `docs/adr/` 以及所有 `src/*/docs/adr/` 目录
- `docs/agents/`：本技能之前是否已经生成过内容？
- `.scratch/`：它说明仓库可能已在使用本地 Markdown issue 跟踪约定
- 是否安装了 `triage` 技能（本技能旁边存在 `triage` 技能目录，或可用技能中有 `triage`）。这决定是否执行 B 节。
- Monorepo 信号：`pnpm-workspace.yaml`、`package.json` 中的 `workspaces` 字段，或包含自身 `src/` 的有效 `packages/*`。只有真正大型的多包仓库才有这些信号；没有信号就意味着单上下文，这适用于几乎所有仓库。

### 2. 展示发现并询问

概述现有内容和缺失内容，然后按顺序处理各节。一次处理一节、取得一个答案，再进入下一节。

每节先给出推荐答案，让用户可以用一个词接受。只有选择确实会形成分支时才给一行解释；探索已经确定的章节应直接跳过（未安装 `triage` 时跳过 B 节，没有 monorepo 时跳过 C 节）。

**A 节：Issue 跟踪器。**

> 解释：“Issue 跟踪器”是该仓库存放 issue 的位置。`to-tickets`、`triage` 和 `to-spec` 等技能会读取和写入它。它们需要知道应调用 `gh issue create`、在 `.scratch/` 下写 Markdown，还是遵循你说明的其他流程。选择该仓库实际跟踪工作的地方。

默认倾向：这些技能为 GitHub 设计。如果 `git remote` 指向 GitHub，就建议 GitHub；指向 GitLab（`gitlab.com` 或自托管地址）就建议 GitLab。其他情况或用户另有偏好时，提供：

- **GitHub**：issue 位于仓库 GitHub Issues 中（使用 `gh` CLI）
- **GitLab**：issue 位于仓库 GitLab Issues 中（使用 [`glab`](https://gitlab.com/gitlab-org/cli) CLI）
- **本地 Markdown**：issue 作为文件保存在仓库 `.scratch/<feature>/` 下，适合个人项目或没有远端的仓库
- **其他**（Jira、Linear 等）：让用户用一个段落描述流程；技能会把它记录为自由文本

把选择写入 `docs/agents/issue-tracker.md`。GitHub 和 GitLab 模板包含“将 PR 作为请求入口”的开关，默认关闭。保持关闭且不要主动提出；希望把外部 PR 加入分诊队列的用户以后可以自行修改文件。

**B 节：分诊标签词汇。**如果未安装 `triage` 技能，完全跳过本节，因为未安装的技能不需要标签。

如果已经安装，只问一个问题：

> 是否保留默认分诊标签？（推荐：**是**）

默认标签对应五种规范角色，标签字符串与角色名相同：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。用户回答“是”时原样写入。只有用户回答“否”时才收集覆盖值，通常是因为跟踪器已经使用其他名称（如用 `bug:triage` 表示 `needs-triage`）；这样 `triage` 会使用既有标签而不是创建重复项。

**C 节：领域文档。**默认采用**单上下文**（根目录一份 `CONTEXT.md` 加 `docs/adr/`），适合几乎所有仓库，无需询问即可写入。

只有探索发现 monorepo 信号时才提供**多上下文**选项，即根目录 `CONTEXT-MAP.md` 指向各上下文的 `CONTEXT.md`。此时确认用户选择哪种布局。

### 3. 确认并编辑

向用户展示以下草稿：

- 要加入所选 `CLAUDE.md` 或 `AGENTS.md` 的 `## Agent skills` 块（选择规则见步骤 4）
- `docs/agents/issue-tracker.md`、`docs/agents/domain.md` 以及 `docs/agents/triage-labels.md` 的内容（最后一项仅在安装了 `triage` 时出现）

写入前允许用户修改。

### 4. 写入

**选择要编辑的文件：**

- 如果存在 `CLAUDE.md`，编辑它。
- 否则如果存在 `AGENTS.md`，编辑它。
- 两者都不存在时，询问用户要创建哪一个；不要替用户选择。

已有 `CLAUDE.md` 时绝不创建 `AGENTS.md`，反之亦然；始终编辑现有文件。

如果所选文件已包含 `## Agent skills` 块，就原位更新内容，不要追加重复章节。不要覆盖周边章节的用户修改。

该块如下：

```markdown
## Agent skills

### Issue tracker

[用一行概述 issue 跟踪位置]。参见 `docs/agents/issue-tracker.md`。

### Triage labels

[用一行概述标签词汇]。参见 `docs/agents/triage-labels.md`。

### Domain docs

[用一行概述布局：“single-context”或“multi-context”]。参见 `docs/agents/domain.md`。
```

只有安装了 `triage` 且执行过 B 节时，才包含 `### Triage labels` 子块并写入 `docs/agents/triage-labels.md`；否则两者都省略。

随后以本技能目录中的种子模板为起点编写文档：

- [issue-tracker-github.md](./issue-tracker-github.md)：GitHub issue 跟踪器
- [issue-tracker-gitlab.md](./issue-tracker-gitlab.md)：GitLab issue 跟踪器
- [issue-tracker-local.md](./issue-tracker-local.md)：本地 Markdown issue 跟踪器
- [triage-labels.md](./triage-labels.md)：标签映射（仅在安装 `triage` 时）
- [domain.md](./domain.md)：领域文档读取规则和布局

对于“其他”issue 跟踪器，根据用户描述从头编写 `docs/agents/issue-tracker.md`。

### 5. 完成

告知用户设置已经完成，并说明哪些工程技能会读取这些文件。提示用户以后可以直接编辑 `docs/agents/*.md`；只有切换 issue 跟踪器或希望从头设置时才需要重新运行本技能。
