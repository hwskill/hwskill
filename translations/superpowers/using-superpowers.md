---
schema_version: 1
skill_id: superpowers/using-superpowers
translated_at: 2026-09-21
---

<SUBAGENT-STOP>
如果你是作为子 Agent 被派来执行某项具体任务，请忽略本技能。
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
如果你认为某项技能哪怕只有 1% 的可能适用于当前工作，你也**绝对必须**调用它。

如果某项技能适用于你的任务，你没有选择余地。你必须使用它。

这不可协商。你不能用任何理由规避它。
</EXTREMELY-IMPORTANT>

## 规则

**在作出任何回复或行动之前，调用相关或用户点名的技能**——包括提出澄清问题、探索代码库或检查文件。如果后来发现技能不适合当前情况，可以不继续使用。

**进入计划模式之前：**如果还没有做过头脑风暴，先调用 `brainstorming` 技能。

然后说明“Using [skill] to [purpose]”，并严格遵循技能。如果技能包含检查清单，为每一项创建 todo。

## 技能优先级

多项技能同时适用时，流程技能优先——它们先确定方法，再由实现技能（如 frontend-design）执行。`brainstorming` 和 `systematic-debugging` 是 Superpowers 中最常用的流程技能，但该规则适用于所有技能。

- “构建 X” → 先用 `superpowers:brainstorming`，再用实现技能。
- “修复这个 bug” → 先用 `superpowers:systematic-debugging`，再用领域技能。

## 危险信号

出现以下想法说明你在找借口，必须停止：

| 想法 | 事实 |
|---------|---------|
| “这只是个简单问题” | 问题也是任务。检查技能。 |
| “我得先了解更多上下文” | 技能检查发生在澄清问题**之前**。 |
| “先探索一下代码库” | 技能会告诉你**如何**探索。先检查技能。 |
| “我可以快速检查 Git 或文件” | 文件不包含对话上下文。先检查技能。 |
| “先收集信息” | 技能会告诉你**如何**收集信息。 |
| “这不需要正式技能” | 如果存在对应技能，就使用它。 |
| “我记得这项技能” | 技能会演进。阅读当前版本。 |
| “这不算任务” | 行动就是任务。检查技能。 |
| “用技能太重了” | 简单事情也会变复杂。使用技能。 |
| “先做这一件小事” | 做任何事**之前**先检查。 |
| “这样做感觉很有产出” | 没有纪律的行动浪费时间。技能用于避免这种情况。 |
| “我知道这是什么意思” | 理解概念不等于使用技能。调用它。 |

## 平台适配

如果下列列表包含你的运行环境，请阅读相应参考文件中的特殊说明：

- Claude Code：`references/claude-code-tools.md`
- Codex：`references/codex-tools.md`
- Pi：`references/pi-tools.md`
- Antigravity：`references/antigravity-tools.md`
- Hermes Agent：`references/hermes-tools.md`
- Muse：`references/muse-tools.md`

## 用户指令

用户指令（CLAUDE.md、AGENTS.md、GEMINI.md 等文件以及直接请求）优先于技能，技能又优先于默认行为。只有在人类协作者明确要求时，才能跳过技能工作流或指令。
