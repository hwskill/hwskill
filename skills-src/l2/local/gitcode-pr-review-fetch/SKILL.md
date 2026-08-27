---
name: gitcode-pr-review-fetch
description: Use when 调查 GitCode Pull Request /diffs、完整代码差异、diff/patch、变更文件、base/head SHA，或需要完整评论、diff_comment 行级检视意见、discussion 回复与 resolved 状态时。
---

# GitCode PR 差异与检视证据获取

## 概述

使用独立入口获取 GitCode PR 的完整代码 patch 或评论证据。本技能只负责获取和规范化证据，
不负责判断代码正确性或检视意见是否成立。

## 定位技能目录

先使用当前运行时为本技能提供的 `SKILL.md` 路径，将其所在目录记为 `<skill-dir>`。所有入口都从
`<skill-dir>/scripts/` 执行；不得从当前仓库或工作目录推导技能路径，也不得假设存在 `.codex/skills`。

## 入口选择

调查 PR `/diffs`、完整代码差异或 unified diff/patch 时，使用 patch 入口：

```bash
python3 <skill-dir>/scripts/fetch_gitcode_pr_patch.py \
  PR_URL --output /tmp/pr.patch
```

获取完整评论、行级检视意见、discussion 回复和 resolved 状态时，使用 review 入口：

```bash
python3 <skill-dir>/scripts/fetch_gitcode_pr_reviews.py \
  PR_URL --format json
```

两个入口也可使用 `owner/repo PR_NUMBER`。review 入口人工阅读时可改用 `--format markdown`；
仅需在 review JSON 中附带变更文件时增加 `--include-files`。运行对应入口的 `--help` 查看全部参数。

## Patch 契约

- 代码差异源自完整分页的 `GET /pulls/{number}/files` 响应中的 `files[].patch` 对象；
  `patch.diff` 是文件 hunk，不是评论位置附近的代码片段。
- 输出仅支持文本变更。任一二进制文件或 `too_large=true` 都会在写出前整体失败，不产生部分 patch。
- 非空成功 patch 只保证应用于 files payload 对应的 comparison preimage，并可用普通
  `git apply --check /tmp/pr.patch` 验证、`git apply /tmp/pr.patch` 应用。stderr 的 `state`、
  `api_base` 和 `head` 只是当前 PR API 证据，不标识 apply base；已合入或目标分支已前进的 PR
  可能需要从仓库历史定位更早的 preimage。
- 空 changed-files 是合法 no-op：返回 0 并输出零字节 patch，使用
  `git apply --check --allow-empty /tmp/pr.patch` 或 `git apply --allow-empty /tmp/pr.patch` 验证。
- patch 入口保持 API-only，只请求 PR metadata/files，不执行 git、fetch 或评论请求。

## Review 流程

1. 私有仓库只通过调用进程已有的 `GITCODE_TOKEN` 环境变量认证。不得把 token 放入命令参数、输出文件、
日志或回复正文。

2. 获取后核对：

- `pull_request.head.sha` 是否对应待检视版本；
- `summary.comment_count`、`review_comment_count`、`reply_count`；
- `summary.unresolved_count`；
- 每条 `diff_comment` 的 `position`、`discussion_id`、`resolved` 和 `replies`。

3. 需要分析检视意见时，先完整读取所有回复，再使用目标仓库适用的代码评审规则验证代码。本技能不评价
   检视意见是否成立，也不要求任何仓库专属技能。

## 输出约定

- patch 入口的 stdout 或 `--output` 文件只包含 patch；`state`、`api_base`、`head`、文件数和诊断写入 stderr。
- JSON 保留 GitCode 返回的未知字段，并统一增加 `position` 和 `replies`；GitCode 单条评论接口返回的
  `DiffNote` 会规范为 `comment_type=diff_comment`，原值保留在 `source_comment_type`。
- Markdown 按原始评论顺序输出正文、解决状态、代码位置和全部回复。
- 不设置 `comment_type` 过滤器；完整获取必须同时保留普通 PR 评论和行级意见。

## 常见错误

- 从当前仓库拼接 `.codex/skills/...`：切换仓库后会失效；始终使用 `<skill-dir>`。
- 直接拼接 `files[].patch`：该字段是包含路径、mode 和 `diff` 的对象，必须使用 patch 入口规范化。
- 只读取评论列表：行级意见可能缺少完整位置，必须让 review 入口按需补取单条评论详情。
- 把 `api_base` 当作 patch apply base：目标分支前进或 PR 合入后，两者可能不同。

## 只读边界

- 仅调用 GitCode `GET /api/v5` endpoint。
- 不回复 discussion，不修改 `resolved`，不编辑或删除评论。
- 如果需要 API 字段、分页和认证细节，读取 `references/gitcode-pr-api.md`。
