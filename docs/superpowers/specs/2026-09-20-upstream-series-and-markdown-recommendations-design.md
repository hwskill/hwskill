# Superpowers 与 Matt Pocock 系列收录及 Markdown 推荐设计

## 目标

HWSkill 收录 Superpowers 和 Matt Pocock 当前正式发布的完整技能系列，并为两个系列提供可维护的推荐文章。站点新增“技能推荐”一级入口，让读者能够浏览推荐、理解两个系列的侧重点，并复制 Agent 提示词贡献新的推荐文章。最终发布物继续保持来源可追溯、构建可重复、机器目录可校验。

本设计只收录上游明确列入正式参考范围的技能。实验中、杂项和废弃目录不进入正式目录。外部技能正文仍留在上游仓库，HWSkill 只保存元数据、固定来源、安装指引和验证状态。

## 固定上游范围

本次核对时间为 2026-09-20。所有 external 条目使用完整提交 SHA 作为 `requested_ref`。

### Superpowers

来源仓库为 `https://github.com/obra/superpowers.git`，固定提交为 `5bf4e78011075bcfc0dc295f0724994cd123ee71`，许可证为 MIT。正式范围是上游 `skills/` 下的 15 个技能：

1. `brainstorming`
2. `diagnosing-superpowers`
3. `dispatching-parallel-agents`
4. `executing-plans`
5. `finishing-a-development-branch`
6. `receiving-code-review`
7. `requesting-code-review`
8. `subagent-driven-development`
9. `systematic-debugging`
10. `test-driven-development`
11. `using-git-worktrees`
12. `using-superpowers`
13. `verification-before-completion`
14. `writing-plans`
15. `writing-skills`

### Matt Pocock Skills

来源仓库为 `https://github.com/mattpocock/skills.git`，固定提交为 `c55ee46073ed923f86ce59a5eb3b6d895095d1b7`，许可证为 MIT。正式范围以上游 README 的 Reference 为准，共 25 个技能。

Engineering：

1. `ask-matt`
2. `code-review`
3. `codebase-design`
4. `diagnosing-bugs`
5. `domain-modeling`
6. `grill-with-docs`
7. `implement`
8. `improve-codebase-architecture`
9. `prototype`
10. `research`
11. `resolving-merge-conflicts`
12. `setup-matt-pocock-skills`
13. `tdd`
14. `to-spec`
15. `to-tickets`
16. `triage`
17. `wayfinder`
18. `wizard`

Productivity：

1. `grill-me`
2. `grilling`
3. `handoff`
4. `teach`
5. `to-questionnaire`
6. `wait-what`
7. `writing-for-agents`

上游 `skills/in-progress/`、`skills/misc/` 和 `skills/deprecated/` 明确排除。后续如上游把其中某项提升到正式 Reference，应通过新的目录 PR 更新固定提交和完整性测试，不能由构建过程自动跟随默认分支。

## 技能条目模型

40 个技能分别保留为 `entries/<layer>/<namespace>/<name>.yaml`，继续使用现有 Entry Schema。这样每项技能都有独立稳定 ID、搜索结果、详情页、生命周期和验证状态，也不需要让现有目录消费者理解新的系列聚合格式。

层级按能力用途判断，而不是按作者统一归类：

- 通用访谈、规划、Agent 编排、写作和交接能力归入 L1。
- 代码设计、实现、测试、调试、Git、评审和研发交付能力归入 L2。
- 现有 `superpowers/systematic-debugging` 和 `superpowers/test-driven-development` 保持 ID 不变，但移动到符合分类定义的 L2 路径。

每个条目必须包含：

- 固定仓库、技能目录和完整提交 SHA；
- 从上游 frontmatter 和 README 整理的中文摘要、用途、调用示例和关键词；
- 上游明确提供的安装文档 URL；
- 明确的伴随技能或前置配置，例如 `grill-me` 依赖 `grilling`，Matt Pocock 工程流程依赖一次性项目配置；
- 上游声明支持的 Agent 与系统，不把“格式可读取”写成“行为已验证”；
- MIT 许可证与固定提交上的许可证链接；
- 未运行安装和行为验证时的限制说明。

external 条目继续是 `guidance_only` 安装能力。详情页仍提供可复制的安装提示词；提示词要求 Agent 在安装前核对固定来源、完整目录、依赖和目标宿主。未验证状态以警告和说明呈现，不阻止用户发起安装。

本次不增加构建时联网同步。目录测试保存两个系列的预期 ID、来源路径和提交 SHA，防止遗漏、误收录或版本混用。未来上游更新由维护者显式核对后修改。

## Markdown 推荐事实源

### 文件格式

推荐事实源从 `recommendations/<id>.yaml` 迁移为单文件 `recommendations/<id>.md`。文件使用 YAML Frontmatter 保存结构化字段，分隔线后的内容是 Markdown 正文：

```markdown
---
schema_version: 1
id: superpowers-engineering-workflow
skills:
  - id: superpowers/brainstorming
title: Superpowers：把 Agent 研发变成可审查流程
summary: 以设计、计划、实现、验证和评审组成完整的 Agent 研发方法。
author: hwskill-maintainers
topics: [engineering, workflow]
evidence:
  - url: https://github.com/obra/superpowers
    observed_at: 2026-09-20T00:00:00+08:00
status: ready
---

Markdown 正文。
```

一篇推荐只由一个文件维护，避免 sidecar YAML 与 Markdown 正文发生 ID、状态或生命周期漂移。现有推荐迁移到相同格式，仓库不长期兼容两套人工事实源。

### 校验与机器输出

新增 source schema 校验 Frontmatter，要求 `summary`，但不接受由作者填写的 `body` 或 `body_format`。加载器完成以下规范化：

1. 文件名必须等于 Frontmatter 的 `id`。
2. 分隔线和正文必须存在，正文不得仅含空白。
3. 正文原样规范化为 `body`，构建器写入 `body_format: markdown`。
4. `ready` 推荐引用的每个技能必须是当前可发布的 active 条目。
5. Markdown 文件和贡献模板都进入目录输入摘要。

公开 `recommendation.schema.json` 增加可选 `summary` 和 `body_format`。旧发布物没有这两个字段时继续按纯文本正文解释；新发布物设置 `body_format: markdown`。字段保持向后兼容，不提升机器文档的 `schema_version`。贡献页面改为链接 source schema 和 Markdown 模板，机器目录页面继续链接公开输出 schema。

发布、撤回和 feed 语义保持不变：新增 ready ID 产生发布事件，withdrawn 保留 tombstone，普通正文编辑不伪装成新的推荐发布事件。

### Markdown 安全边界

站点在构建时渲染 Markdown，关闭原始 HTML，并拒绝危险 URL 协议。允许标题、段落、强调、列表、引用、代码、代码块、表格和普通链接。外部链接增加安全的 `rel` 属性。页面不执行推荐正文中的 HTML、脚本或事件属性。

标题和页面描述从结构化 `title`、`summary` 生成，不从 Markdown 截断推断。首页和推荐列表使用 `summary`，避免把 Markdown 标记直接显示在摘要中。

## 推荐文章内容

新增两篇 ready 推荐：

### Superpowers：把 Agent 研发变成可审查流程

文章关联全部 15 个 Superpowers 技能，覆盖：

- 从需求澄清、设计确认、计划到执行的主流程；
- TDD、系统调试、完成前验证和代码评审的质量门槛；
- worktree、并行 Agent 和子 Agent 执行的协作方式；
- 适合长任务、强流程、需要稳定质量门槛和较长自治执行的场景；
- 强制触发规则、流程开销、宿主能力和团队适配成本；
- 当前目录只完成来源与元数据核验，安装和行为验证仍需用户或 Agent 在目标环境执行。

### Matt Pocock：可组合的工程与沟通工具箱

文章关联全部 25 个正式技能，覆盖：

- Engineering 与 Productivity 的划分；
- grilling、领域模型、规格、工单、实现、评审和交接之间的组合方式；
- 用户调用的编排技能和模型按场景调用的纪律技能之间的区别；
- 适合希望保留人工控制、按需替换步骤、补强沟通和领域知识的场景；
- issue tracker、项目初始化、文档目录和部分 Bash 工具的环境要求；
- 当前目录的验证边界。

两篇文章互相链接，并包含一致的对比维度：

| 维度 | Superpowers | Matt Pocock |
| --- | --- | --- |
| 核心形态 | 连贯的完整研发方法 | 可独立组合的工程与沟通技能 |
| 流程控制 | 自动触发和质量门槛较强 | 用户选择流程，模型复用底层纪律 |
| 主要侧重 | 从设计到交付的闭环 | 对齐、领域建模和具体工程操作 |
| 典型场景 | 长周期开发与一致质量标准 | 灵活流程与单环节补强 |

文章说明组合使用边界：可用 Superpowers 作为主交付流程，再按需加入 Matt Pocock 的 grilling、domain-modeling、triage 或 research；对于 TDD、调试和代码评审等重叠环节，应选择一个主技能，避免重复触发和冲突要求。

## 站点信息架构

### 导航与列表页

主导航在“找技能”之后增加“技能推荐”，指向 `/recommendations/`。页面包含：

- 页面定位和推荐数量；
- ready 推荐卡片，显示标题、摘要、作者、主题和关联技能数量；
- 两个系列推荐的清晰入口；
- 推荐文章应包含哪些事实和验证边界的简要说明；
- 可直接复制的推荐贡献提示词；
- Markdown 模板、Agent 贡献指南和完整贡献页链接。

首页只展示有限数量的 ready 推荐，并提供“查看全部推荐”入口。withdrawn 推荐不进入活跃列表，保留其历史详情和机器 tombstone。

### 推荐详情页

详情页展示结构化标题、摘要、作者、主题和验证说明，再渲染 Markdown 正文。关联技能改用紧凑列表，并按 namespace 和 layer 分组；25 个技能不渲染为 25 张完整大卡片。每项仍链接到对应技能详情页。

贡献提示词从共享模块生成，推荐列表页和贡献页使用同一个文本源。提示词继续要求 Agent：

- 先读取贡献指南、source schema、Markdown 模板和当前机器目录；
- 逐个检查文章引用的技能是否已收录；
- 未收录时在同一 PR 中补齐技能条目；
- 运行目录校验和站点构建；
- 创建分支、提交、推送并创建 PR，最终返回 PR URL；
- 不授权合并。

## 错误处理

目录校验使用明确错误码报告以下问题：

- Frontmatter 缺失、YAML 非法、重复键或未知字段；
- 推荐文件名与 ID 不一致；
- Markdown 正文为空；
- ready 推荐引用不存在、无效、已撤回或未发布的技能；
- 重复推荐 ID；
- 不安全的 evidence URL；
- 模板不符合 source schema。

站点构建对未知 `body_format` 直接失败，避免静默地把不受支持格式作为 HTML。Markdown 解析失败同样阻止发布。

## 测试与验收

### 目录测试

- 精确断言 Superpowers 15 个和 Matt Pocock 25 个正式 ID。
- 精确断言两个固定提交、仓库路径、MIT 许可证和排除目录。
- 覆盖 Frontmatter 解析、文件名匹配、空正文、重复键、未知字段和模板校验。
- 证明 Markdown 正文和模板变化会改变输入摘要。
- 证明 ready 推荐仍受技能引用完整性约束。
- 证明旧的无 `body_format` 机器文档仍可由共享与发布逻辑读取。

### 站点测试

- `/recommendations/`、两篇系列推荐和现有推荐均生成。
- 导航、首页入口、推荐卡片和关联技能链接正确。
- Markdown 标题、列表、表格、代码块和普通链接正确渲染。
- 原始 HTML、脚本、事件属性和危险协议不会进入生成页面。
- 贡献提示词在推荐页和贡献页内容一致，复制功能可用。
- 推荐详情使用结构化摘要作为页面 description。
- 移动端导航和长文章保持可读。

### 完整门禁

运行 Python 测试、目录 validate/build、Astro build、构建产物检查和 Pagefind 搜索评估。最终检查 Git 差异只包含本功能文件，既有未跟踪 `.superpowers/` 不纳入提交。

## 非目标

本次不复制两个上游的技能正文，不自动定时同步 GitHub，不收录实验或废弃技能，不执行批量安装，不把来源核验描述为安装或行为验证，也不改变 release/feed 的生命周期模型。
