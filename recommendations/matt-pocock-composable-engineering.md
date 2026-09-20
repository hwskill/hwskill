---
schema_version: 1
id: matt-pocock-composable-engineering
title: Matt Pocock：可组合的工程与沟通工具箱
summary: 用可独立组合的访谈、领域建模、规格、实现、评审和交接技能补强具体环节。
author: hwskill-maintainers
topics:
- agent-workflow
- software-engineering
- domain-modeling
evidence:
- url: https://github.com/mattpocock/skills
  observed_at: '2026-09-20T00:00:00+08:00'
status: ready
skills:
- id: mattpocock/ask-matt
- id: mattpocock/code-review
- id: mattpocock/codebase-design
- id: mattpocock/diagnosing-bugs
- id: mattpocock/domain-modeling
- id: mattpocock/grill-me
- id: mattpocock/grill-with-docs
- id: mattpocock/grilling
- id: mattpocock/handoff
- id: mattpocock/implement
- id: mattpocock/improve-codebase-architecture
- id: mattpocock/prototype
- id: mattpocock/research
- id: mattpocock/resolving-merge-conflicts
- id: mattpocock/setup-matt-pocock-skills
- id: mattpocock/tdd
- id: mattpocock/teach
- id: mattpocock/to-questionnaire
- id: mattpocock/to-spec
- id: mattpocock/to-tickets
- id: mattpocock/triage
- id: mattpocock/wait-what
- id: mattpocock/wayfinder
- id: mattpocock/wizard
- id: mattpocock/writing-for-agents
---

## 定位

Matt Pocock 技能栈是一组可单独调用、也可串联使用的工程与沟通工具。它覆盖需求访谈、文档追问、领域建模、规格与工单生成、实现、测试、评审、调试、调研、原型和会话交接。

其中一部分技能天然适合用户明确点名，例如 `grill-me`、`teach`、`to-questionnaire` 和 `handoff`；另一部分可以由 Agent 根据任务阶段选择，例如 `domain-modeling`、`triage`、`implement` 和 `code-review`。它更像工具箱，允许团队在已有工作流中替换或增加一个具体环节。

## 工作流

可以按任务需要组合出以下路径：

1. 用 `setup-matt-pocock-skills` 检查和准备技能环境。
2. 需求尚不明确时，用 `grill-me` 持续追问隐含决策；已有材料时用 `grill-with-docs`，通用追问方式由 `grilling` 提供。
3. 用 `domain-modeling` 建立统一领域语言，或以 `codebase-design`、`improve-codebase-architecture` 分析代码边界。
4. 用 `to-spec` 形成实现规格，再用 `to-tickets` 拆成可跟踪任务；需要结构化收集信息时可用 `to-questionnaire`。
5. 用 `implement` 执行方案，或用 `tdd` 以测试驱动实现；冲突处理可调用 `resolving-merge-conflicts`。
6. 用 `code-review` 审查变更，出现故障时调用 `diagnosing-bugs`。
7. 对未知方向，`triage` 先判定问题类型，`research` 收集证据，`prototype` 快速验证方案，`wayfinder` 帮助定位代码路径。
8. 用 `handoff` 把状态交给下一位执行者；`writing-for-agents` 改善给 Agent 的材料，`teach` 用于讲解，`wait-what` 用于停下来核对理解，`ask-matt` 和 `wizard` 提供更具引导性的交互入口。

这条路径不是固定流水线。团队可以只复制一个技能的提示词，或者根据网站引导生成包含当前目标、仓库信息和输出要求的动态提示词。

## 适用场景

- 需求描述短、隐含选择多，需要系统追问后再写规格。
- 复杂业务需要先统一实体、状态、事件和边界语言。
- 已有工程流程，但缺少调研、原型、代码定位或交接模板。
- 希望将规格转成工单，并让后续 Agent 获得足够上下文。
- 需要用不同入口服务产品讨论、工程实现和知识讲解。

如果团队需要严格统一的端到端研发阶段和完成门槛，需要另行指定主流程，不能只依赖工具箱自行组合。

## 成本与限制

追问、领域建模、规格和工单拆分会增加准备时间，也要求用户提供真实约束。部分技能预期能读取仓库、文档或任务跟踪信息；具体可用性取决于 Agent 宿主、连接器和本地命令权限。

把多个相似技能同时用于同一阶段，可能造成重复访谈、重复测试或互相覆盖的输出。上游目录还包含 in-progress、misc 和 deprecated 分类，本次只收录稳定的 engineering 与 productivity 目录，不把实验性或弃用技能包装成稳定推荐。

## 与另一个系列的比较

| 维度 | Matt Pocock 技能栈 | Superpowers |
| --- | --- | --- |
| 组织方式 | 独立能力，可按问题自由组合 | 按研发阶段组织，并设置完成条件 |
| 主要价值 | 深入追问、领域建模、调研、原型和交接 | 设计、计划、隔离实现、测试、验证与评审闭环 |
| 默认入口 | 用户点名和 Agent 场景选择并存 | Agent 先识别并遵循适用流程 |
| 适合团队 | 已有流程，想补强局部工具 | 想建立一致的端到端执行纪律 |

Matt Pocock 系列覆盖的沟通与探索入口更多；Superpowers 对任务推进顺序、验证证据和分支完成的约束更完整。

## 组合边界

推荐以 Superpowers 作为主流程时，选择 Matt Pocock 的 `grill-me`、`grill-with-docs`、`domain-modeling`、`research`、`triage` 或 `handoff` 补充对应阶段。若现有团队已经有成熟流程，也可以直接选择 Matt Pocock 的单项技能。

测试驱动、调试、代码评审等重叠职责应明确唯一主规则。例如选择 `mattpocock/tdd` 后，不要在同一任务中再要求另一套 TDD 技能重复启动；组合结果需要写清输入、产物和何时结束。

## 验证状态

本推荐基于 Matt Pocock skills 仓库固定提交 `c55ee46073ed923f86ce59a5eb3b6d895095d1b7` 的稳定目录、README 和 MIT 许可证核验，共收录 engineering 18 个、productivity 7 个技能。当前没有逐一安装和运行这些技能，也没有验证外部文档、任务跟踪器或所有 Agent 宿主集成。

未完成行为验证不会阻止用户复制安装提示词或继续安装。网站应把验证层级、固定版本和潜在外部依赖放在操作入口附近，让用户知情后继续，而不是将“未验证”处理为不可安装。
