---
schema_version: 1
id: superpowers-engineering-workflow
title: Superpowers：把 Agent 研发变成可审查流程
summary: 以设计、计划、隔离实现、测试、验证和评审组成完整的 Agent 研发方法。
author: hwskill-maintainers
topics:
- agent-workflow
- software-engineering
- testing
evidence:
- url: https://github.com/obra/superpowers
  observed_at: '2026-09-20T00:00:00+08:00'
status: ready
skills:
- id: superpowers/brainstorming
- id: superpowers/diagnosing-superpowers
- id: superpowers/dispatching-parallel-agents
- id: superpowers/executing-plans
- id: superpowers/finishing-a-development-branch
- id: superpowers/receiving-code-review
- id: superpowers/requesting-code-review
- id: superpowers/subagent-driven-development
- id: superpowers/systematic-debugging
- id: superpowers/test-driven-development
- id: superpowers/using-git-worktrees
- id: superpowers/using-superpowers
- id: superpowers/verification-before-completion
- id: superpowers/writing-plans
- id: superpowers/writing-skills
---

## 定位

Superpowers 是一套带顺序约束的 Agent 软件研发方法。它把“先理解、再实现、最后用证据收口”拆成可以复用的技能，并要求 Agent 在进入下一阶段前满足当前阶段的检查条件。

这套技能更适合作为一次研发任务的主流程。它管理需求澄清、设计确认、实施计划、隔离工作区、测试、调试、代码评审和分支收尾之间的衔接，而不是只提供某个局部技巧。

## 工作流

一条典型路径如下：

1. [`using-superpowers`](/skills/superpowers/using-superpowers/) 先判断当前任务应调用哪些技能。
2. [`brainstorming`](/skills/superpowers/brainstorming/) 通过对话澄清目标、约束和设计，并把设计变成可审查的文档。
3. [`writing-plans`](/skills/superpowers/writing-plans/) 把设计拆成带文件路径、测试方式和提交边界的实施步骤。
4. [`using-git-worktrees`](/skills/superpowers/using-git-worktrees/) 为变更建立隔离工作区，避免干扰当前检出。
5. [`executing-plans`](/skills/superpowers/executing-plans/) 在当前会话逐项实现；任务足够独立时，可由 [`subagent-driven-development`](/skills/superpowers/subagent-driven-development/) 或 [`dispatching-parallel-agents`](/skills/superpowers/dispatching-parallel-agents/) 分派工作。
6. 实现阶段以 [`test-driven-development`](/skills/superpowers/test-driven-development/) 建立红绿循环；出现异常时使用 [`systematic-debugging`](/skills/superpowers/systematic-debugging/) 先定位根因。
7. [`verification-before-completion`](/skills/superpowers/verification-before-completion/) 要求在声明完成前重新执行验证并查看输出。
8. [`requesting-code-review`](/skills/superpowers/requesting-code-review/) 和 [`receiving-code-review`](/skills/superpowers/receiving-code-review/) 约束评审发起与反馈处理，[`finishing-a-development-branch`](/skills/superpowers/finishing-a-development-branch/) 负责合并、PR 或保留分支等收尾动作。
9. 如果技能机制本身表现异常，可用 [`diagnosing-superpowers`](/skills/superpowers/diagnosing-superpowers/) 检查触发与执行过程；需要编写或维护技能时使用 [`writing-skills`](/skills/superpowers/writing-skills/)。

设计确认、破坏性操作和最终集成仍由人决定。获得明确范围后，Agent 可以自主完成可逆的实现、验证和整理工作。

## 适用场景

- 跨多个文件、需要设计和实施计划的功能开发。
- 希望每项完成声明都有新鲜测试或构建输出支撑的团队。
- 需要用 worktree 隔离并行任务，或希望分阶段做代码评审的仓库。
- 容易因过早写代码、猜测根因或遗漏收尾步骤而返工的 Agent 工作流。
- 需要把团队工程习惯写成可重复执行技能的维护者。

对一次性文本编辑或风险很低的小改动，完整流程的文档和检查成本可能高于收益，可以只选与任务相关的技能。

## 成本与限制

Superpowers 的优势来自流程约束，这也会增加前期对话、设计文档、计划、测试和评审成本。多个 Agent 模式依赖宿主提供子 Agent 或等价能力；worktree 流程依赖 Git；测试与验证流程仍依赖项目自身有可执行的检查命令。

技能中的步骤不能代替仓库规则、产品判断和真实环境验证。严格同时启用多个职责重叠的流程技能，还可能导致重复提问或重复检查，使用时应保留一个清晰的主流程。

## 与另一个系列的比较

| 维度 | Superpowers | Matt Pocock 技能栈 |
| --- | --- | --- |
| 组织方式 | 强调阶段顺序和完成条件 | 强调可按问题组合的独立工具 |
| 主要价值 | 让完整研发任务可审查、可验证 | 补强访谈、建模、规格、调研和交接等具体环节 |
| 默认入口 | Agent 根据任务识别并执行流程技能 | 用户可点名调用，部分技能也可由 Agent 按场景选择 |
| 适合团队 | 希望统一端到端研发纪律 | 已有主流程、需要丰富局部能力 |

若任务需要稳定的端到端节奏，Superpowers 更适合作为骨架；若问题集中在需求追问、领域语言或既有代码理解，Matt Pocock 系列通常能提供更细的入口。

## 组合边界

可以用 Superpowers 管理整体阶段，再在设计阶段加入 `mattpocock/grill-me`、`mattpocock/grill-with-docs` 或 `mattpocock/domain-modeling`，在未知问题上加入 `mattpocock/triage`、`mattpocock/research` 或 `mattpocock/wayfinder`。

同一阶段只选择一个主技能：例如测试驱动在 `superpowers/test-driven-development` 与 `mattpocock/tdd` 中二选一，调试和代码评审也应明确由哪套规则主导。这样可以避免相互矛盾的完成条件和重复工作。

## 阅读入口

以上技能名称均链接到站内详情页。详情页保留用途、适用范围、示例、依赖、限制和许可证等元数据，并在正文开头标明中文译文，提供跳转到上游 `SKILL.md` 的“查看原文”链接。
