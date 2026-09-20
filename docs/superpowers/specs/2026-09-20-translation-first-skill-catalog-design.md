# 译文优先的技能目录与独立 PR 验证设计

日期：2026-09-20

状态：设计已确认，待书面规格审阅；本文不代表实现已经完成。

## 1. 目标

HWSkill 从维护技能版本与长期验证状态的目录，调整为以技能发现、理解、贡献和当前来源安装指引为核心的目录。

本次调整要求：

1. external 技能可以选择跟随默认分支，也可以由贡献者指定 branch、tag 或 commit；平台不强制锁定版本，也不解析或长期维护实际 revision。
2. Catalog、网站和 feed 不维护或展示技能来源、安装与行为核验状态。
3. 每个可发布技能必须提供随 PR 提交和审阅的中文 Markdown 译文，详情页正文以该译文为核心。
4. 每个新增或修改技能的 PR 必须提供验证报告。验证报告只作为 PR 合入证据，不进入 Catalog、网站、feed 或长期验证数据库。
5. 安装与使用门禁由独立私有验证项目执行。首版由维护者初审后手动触发，不部署常驻服务。

## 2. 产品边界

网站继续帮助用户发现技能、理解适用范围、查看推荐用法、复制 Agent 安装提示词和贡献技能。平台仍维护名称、摘要、用途、示例、兼容环境、依赖、许可证、限制、生命周期和来源定位。

平台不再回答某个技能是否经过来源核验、安装验证或行为验证。PR Check 只说明该次变更在指定 head SHA 和当次获取的上游内容上得到什么结果，不能转写为网站上的长期技能状态。

安装提示词继续可用。它要求 Agent 读取当前来源、尊重可选 ref、检查完整文件、依赖、权限和目标目录，并报告实际结果。提示词不得使用“已验证版本”或类似表述。

群机器人、Webhook 消息发送和使用遥测仍不属于本次范围。

## 3. Entry Schema v2

### 3.1 External Git 来源

external Git locator 使用以下结构：

```yaml
source:
  kind: external
  locator:
    type: git
    repository: https://github.com/owner/repo
    path: skills/example
    ref: main # 可选：branch、tag 或 commit
```

规则：

- `repository` 和 `path` 必填。
- `ref` 可选。省略时跟随仓库默认分支。
- `ref` 可以是安全的 branch、tag 或 commit 字符串。
- 平台不要求 ref 为完整 commit。
- 构建器不解析 `resolved_revision`，也不把本次获取的 commit 写入 Catalog。
- GitHub 原文链接使用 `blob/<ref>/.../SKILL.md`；未指定 ref 时使用 `blob/HEAD/.../SKILL.md`。
- 非 GitHub 来源由来源适配器生成当前文件 URL；无法可靠生成时，locator 必须提供明确的技能文件 URL。

external Web 来源继续保存直接页面 URL，可选的人类可读版本说明不得被解释为核验结果。

### 3.2 保留的理解与安装字段

Entry 保留：

- 稳定 ID、名称、摘要与 L1-L5 分类；
- 用途、推荐用法、示例任务与预期结果；
- 兼容 Agent、操作系统、依赖、账号与工具要求；
- 安装方式、默认项目级范围、上游安装说明及整包安装内容；
- 作者或维护者、关键词、许可证、已知限制；
- active、deprecated、withdrawn 生命周期及替代关系。

安装字段表达作者提供的安装方法，不表达平台已经执行或验证该方法。

### 3.3 删除的长期状态

当前生成模型删除：

- `source_identity.requested_ref`，由 Entry 中可选的 `source.locator.ref` 直接表达贡献者选择；
- `source_identity.resolved_revision`；
- `source_identity.content_digest` 对 external 来源的版本含义；
- `verification_summary`；
- 由验证结果推导的 `install_capability`。

页面是否提供安装提示词只取决于生命周期和安装声明是否完整。安装方法未知时提供来源核对提示，不生成猜测命令。

## 4. 中文译文

### 4.1 文件结构

每个可发布技能必须包含：

```text
translations/<namespace>/<name>.md
```

文件格式：

```markdown
---
schema_version: 1
skill_id: superpowers/systematic-debugging
translated_at: 2026-09-20
---

# 系统化调试

完整中文译文……
```

`translated_at` 表示译文维护日期，不表示上游版本、安装验证或行为验证。文件路径和 `skill_id` 必须与唯一 Entry 对应。

译文在 PR 中作为人工事实源审阅。构建过程不联网抓取或自动翻译上游正文。

### 4.2 翻译授权

完整译文是原文的衍生内容。发布前必须确认许可证允许翻译与再分发，或取得明确授权。页面保留作者、许可证与原文链接。许可证未知、禁止衍生或授权不清楚时，条目保持 draft，不发布完整译文。

### 4.3 规范化输出

Catalog v2 每个条目包含规范化翻译：

```json
{
  "entry": {},
  "entry_digest": "sha256:...",
  "lifecycle": "active",
  "translation": {
    "body_format": "markdown",
    "body": "...",
    "translated_at": "2026-09-20",
    "source_url": "https://github.com/owner/repo/blob/HEAD/skills/example/SKILL.md"
  }
}
```

指定 ref 时，`source_url` 使用该 ref。Catalog 不生成实际解析 commit。

## 5. 技能详情页

详情页按以下顺序展示：

1. 名称、摘要、层级、来源类型和生命周期。
2. 适用范围、推荐用法、示例、兼容环境、依赖、限制和许可证。
3. 复制给 Agent 的安装提示词。
4. 中文译文提示、原文链接、译文维护日期和完整译文。
5. 关联推荐文章。

译文正文开头固定显示：

> 本文为技能原文的中文译文，可能滞后于上游内容，请以原文为准。

“查看原文”必须跳转到具体技能文件。external 指向上游文件；hosted 指向 HWSkill 主仓中的具体 `SKILL.md`。

页面不显示“已验证”“待验证”“核验通过”“来源版本待核验”等信息，也不展示 PR 验证报告。

## 6. 列表、搜索与推荐

- 技能卡片删除验证状态图标和文案。
- 筛选器删除验证状态维度，保留用途、层级、Agent 与来源。
- Pagefind 索引技能元数据与译文正文；名称、用途和示例的权重高于长篇译文。
- 推荐文章进入搜索索引。
- 推荐文章继续说明组合方式、适用场景、成本与边界，但不维护技能验证状态。
- 推荐涉及未收录技能时，必须在同一 PR 中增加 Entry 与译文。

## 7. PR 验证报告

新增或修改技能的 PR 必须包含：

```markdown
## 技能验证报告

- 验证环境：
- 获取的上游 commit：
- 安装目标：
- 安装步骤：
- 完整性检查：
- 使用任务：
- 实际结果：
- 未验证项：
- 已知限制：
```

“获取的上游 commit”只记录本次报告实际使用的内容，不写入 Entry、Catalog 或网站。指定 ref 时验证该 ref；未指定时验证当时的默认分支 HEAD。

公开目录仓库只执行确定性检查：Entry 与译文 Schema、交叉引用、原文 URL、Markdown 安全、PR 报告结构、目录构建、站点构建与搜索测试。

## 8. 私有验证项目

安装与使用验证代码由独立私有项目维护。公开目录仓库不能直接调用私有 reusable workflow，因此首版使用维护者手动 `workflow_dispatch`，由私有项目中的 GitHub App 身份读取 PR 并回写 Check。

输入固定为目标仓库、PR 编号、精确 head SHA 和本次技能 ID。工作流必须重新确认 PR head SHA，且只读取与本次技能有关的 Entry、译文和来源材料。

验证步骤：

1. 获取指定 ref 或默认分支当前内容，并在报告中记录实际 commit。
2. 把外部内容当作不可信数据，不执行上游脚本、Hook、工作流或依赖安装命令。
3. 在临时项目目录安装完整技能材料。
4. 检查目标 Agent 能否发现技能。
5. 使用受限、低成本模型执行一个固定示例任务。
6. 对照 Entry 中的预期结果生成结论。
7. 通过 GitHub App 回写 `hwskill-validation` Check。
8. 删除临时目录，不把报告写回目录仓库。

门禁结果为：

- `pass`：允许合并；
- `fail`：修改后重新运行；
- `not-applicable`：需要专用硬件、内部账号或特殊网络，必须说明原因并由维护者批准；
- `error`：验证基础设施失败，可在相同 head SHA 上重试。

这些结果只存在于 PR、Check 与 Actions 日志中。

## 9. 安全与费用

- 私有验证项目不接收外部 PR，不使用公开目录 PR 中的工作流代码。
- GitHub App 只获得读取目录仓内容和写入 Check 所需的最小权限。
- 模型凭据只存在于私有项目的受保护 Environment。
- 只有确定性 CI 通过并经维护者初审的 PR 才触发私有验证。
- 相同 head SHA 与技能 ID 不重复计费。
- 默认一个安装任务、一个使用任务、最多一次自动重试。
- 默认使用低成本模型，并设置单 PR 的 Token、时间和费用上限。
- 不保存安装目录；普通日志和 Check Summary 足以保留合入证据。
- 首版不部署常驻 Webhook 服务。投稿量证明自动化收益后，再评估 serverless GitHub App 触发器。

## 10. 迁移策略

当前 44 个技能统一迁移到 Entry/Catalog v2：

- 把 mandatory `requested_ref` 转换为可选 `ref`；维护者逐项决定保留 branch、tag、commit 或省略。
- 删除生成数据中的 revision、verification 和 capability 字段。
- 删除仅用于表达“未运行安装或行为验证”的 limitations，保留实际使用限制。
- 更新安装、许可证和来源链接，避免无意保留强制 commit 锁定。
- 为每个可发布技能增加中文译文。
- 更新推荐文章中对平台验证状态的描述。

Catalog v1 与旧验证报告作为历史发布保留，不再更新。当前构建只生成 v2。sharing reader 可以识别历史 v1，但新 feed 只输出 v2。网站、Catalog、安装材料和搜索索引必须在一次部署中同时切换。

现有公开仓中的验证包在私有验证项目具备等价的一次性报告能力后移除。迁移期间不得同时把其结果写入新 Catalog。

## 11. 验收标准

自动验收覆盖：

1. external Entry 省略 `ref` 时可以发布。
2. 安全的 branch、tag 和 commit ref 均可通过；危险 ref 被拒绝。
3. Catalog 可包含贡献者声明的 ref，但不能生成 `resolved_revision`。
4. 每个可发布 Entry 有且只有一个对应译文。
5. 译文路径、Frontmatter 和 Entry ID 一致。
6. 原文按钮跳转到具体技能文件，且使用指定 ref 或 HEAD。
7. 详情页正文开头显示译文提示和维护日期。
8. Catalog、页面、搜索筛选和 feed 不包含技能验证状态。
9. 安装提示词不声称使用已验证版本。
10. 译文 Markdown 经过安全过滤。
11. Pagefind 可以搜索译文和推荐文章。
12. PR 缺少结构化验证报告时确定性门禁失败。
13. Catalog v1 只作为历史输入读取，当前构建不能生成 v1。
14. 站点、Catalog、安装材料和搜索索引来自同一源码提交。

译文人工审阅至少检查章节完整性、约束词准确性、代码块与命令保真、链接目标，以及是否添加原文没有的能力或效果承诺。

## 12. 实施顺序

1. Entry/Catalog v2 与译文契约。
2. 当前 44 个技能的 Entry 迁移和译文补齐。
3. 详情页、卡片、筛选、搜索与推荐内容调整。
4. PR 模板和公开确定性 CI。
5. 私有验证项目、GitHub App 与 required check。
6. 完整目录、站点、搜索、历史读取和部署验证。
7. 更新用户指南、贡献指南、部署指南、实施状态与验收报告。

