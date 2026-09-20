# 员工使用指南

## 能做什么

HWSkill 公开目录提供技能详情、来源、兼容性、安装能力和推荐文章。目录信息不等于安装或行为已经验证：页面与机器数据中的 `verification_summary`、`install_capability` 和 `limitations` 才是判断依据。

本期没有群机器人或消息发送器。`hwskill-sharing` 只把增量写成 JSON 文件，由调用方决定如何展示或传递。

## 浏览与筛选

打开静态站首页后，可以按用途、主题、层级和兼容宿主筛选。主导航的 `/recommendations/`“技能推荐”页面列出全部 `ready` 推荐，文章说明工作流、适用场景、成本、系列差异、组合边界和验证状态。技能详情页给出来源与安装材料；external 条目不会在本站复制上游正文。撤回项保留机器 tombstone，但不再作为活跃推荐展示。

推荐中心和 `/contribute/` 都提供推荐文章的 Agent 提示词。补充主题、关联技能和公开依据后，Agent 会读取 Frontmatter Schema、Markdown 模板和贡献指引，完成校验并创建 PR。关联技能尚未收录时，提示词要求在同一个 PR 中补齐 Entry。

“安装和行为未运行”是风险提示，不会封锁安装提示词。继续前应核对固定来源、许可证、依赖和目标 Agent 权限；网站不会把来源核验展示为安装成功。

## 获取增量

首次使用必须明确选择基线或回放，不能隐式猜测游标：

```sh
hwskill-sharing prepare \
  --state-db "$HOME/.local/state/hwskill/updates.sqlite3" \
  --consumer-id employee-directory \
  --feed-url https://skills.example/feed \
  --output /tmp/hwskill-updates.json \
  --baseline
```

`--baseline` 从当前 head 开始，适合只看以后变化；需要历史时改用 `--replay-from 0`。可重复加入 `--purpose`、`--skill-id`、`--change-type`、`--lifecycle` 或 `--topic` 进行筛选。

`prepare` 不推进游标。调用方成功接收并持久化输出后，再从 JSON 读取 `batch_id` 并确认：

```sh
hwskill-sharing ack \
  --state-db "$HOME/.local/state/hwskill/updates.sqlite3" \
  --consumer-id employee-directory \
  --batch-id 'batch-<64位十六进制摘要>'
```

私有 feed 需要显式 `--private --token-env HWSKILL_FEED_TOKEN`。Token 只发送到 feed 同源 URL；不要把 Token 写入命令行、配置仓库或输出文件。

## 安装与验证边界

`hwskill-verify` 面向已经获取并审阅的完整技能目录。它不负责联网下载，也不会执行技能目录中的程序。宿主只允许 `codex`、`claude-code`、`opencode`，并实际核对对应命令的 `--version`；目标目录必须分别以 `.codex/skills`、`.claude/skills`、`.opencode/skills` 结尾。

```sh
hwskill-verify \
  --published-root /path/to/built-directory \
  --skill-id local/example \
  --source-dir /path/to/reviewed/source \
  --target-root /tmp/isolated-project/.codex/skills \
  --report-dir /tmp/hwskill-reports \
  --host codex \
  --host-version '<实际探测版本>' \
  --runner-identity local-review
```

报告中的 `blocked`、`fail`、`not_run` 都不是 `pass`。只有调用方审阅报告与安装目录后，才应把结果用于决策。

## 系列收录边界

Superpowers 系列按固定提交收录 15 个稳定技能；Matt Pocock 系列按固定提交收录 Engineering 18 个和 Productivity 7 个技能。Matt 仓库中的 `in-progress`、`misc` 和 `deprecated` 目录不属于稳定系列推荐。两套系列当前完成来源、目录、许可证和元数据核验，未逐项运行安装与行为测试。
