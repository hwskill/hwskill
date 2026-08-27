---
name: gitcode-discussion-fetch
description: Use when 需要获取或归档 GitCode 仓库 Discussion 的完整正文、顶层评论和嵌套回复，输入为 Discussion URL 或 owner/repo 加 Discussion 编号，且需要可阅读的 Markdown 内容。
---

# GitCode Discussion 内容获取

使用本技能将一个 GitCode Discussion 归档为单篇 Markdown。只负责读取和规范化内容；不回复、编辑、删除 Discussion 或评论。

## 定位技能目录

先使用当前运行时提供的 `SKILL.md` 路径，将所在目录记为 `<skill-dir>`。始终从
`<skill-dir>/scripts/` 执行，不得根据当前仓库猜测技能路径。

## 获取内容

私有仓库在调用进程中设置 `GITCODE_TOKEN`；不要把 token 写入命令行、URL、输出文件、日志或回复正文。

输入可以是完整页面 URL：

```bash
python3 <skill-dir>/scripts/fetch_gitcode_discussion.py \
  'https://gitcode.com/linkeo2012/bolt/discussions/3' \
  --output /tmp/discussion-3.md
```

也可以使用仓库与编号：

```bash
python3 <skill-dir>/scripts/fetch_gitcode_discussion.py \
  linkeo2012/bolt 3 > /tmp/discussion-3.md
```

`--output` 在确认完整抓取成功后原子替换目标文件；不加时 Markdown 只写入 stdout。诊断和失败信息写入 stderr。

## 输出契约

- 文件以 YAML frontmatter 记录来源、仓库、Discussion 编号和 ID、URL、标题、作者、时间、顶层评论数和回复数。
- 正文、顶层评论和回复使用原始 `md_content`，不混入服务器渲染的 HTML `content`。
- `## 评论 N` 按 GitCode API 返回的顶层评论顺序排列；该评论的 `### 回复 N.M` 必须紧跟在其正文后，保持网页的阅读顺序。
- 为避免正文原有标题破坏文档层级，正文标题会在相应章节下移；围栏代码块内的 `#` 保持原样。
- 脚本获取所有分页的评论与回复。若某条评论的 `reply_total` 缺失或大于零，会读取该评论全部回复。
- 脚本在抓取前后再次核验 Discussion 的 ID、编号、更新时间和顶层评论总数；抓取期间内容变化或计数不一致时整体失败，不输出部分归档。

## 只读边界和错误处理

- 仅调用 GitCode `/api/v5` 的 `GET` endpoint；不退回网页内部的 `/api/v1` POST 接口。
- 缺少 token、无权限或资源不存在时，GitCode 可能返回 `401`、`403` 或 `404`。检查 `GITCODE_TOKEN` 的权限与目标仓库可见性。
- HTTP 429 和 5xx 会短暂重试；错误文本会脱敏 `GITCODE_TOKEN`。
- 需要 endpoint、分页字段或认证细节时，读取 [references/gitcode-discussion-api.md](references/gitcode-discussion-api.md)。
