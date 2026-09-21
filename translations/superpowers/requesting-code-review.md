---
schema_version: 1
skill_id: superpowers/requesting-code-review
translated_at: 2026-09-21
---

# 请求代码评审

派遣代码评审子 Agent，在问题层层扩散前发现它们。评审者会收到为评估精确组织的上下文，而不是你的会话历史。

**核心原则：**尽早评审，经常评审。

## 何时请求评审

**必须：**

- 子 Agent 驱动开发中的每项任务完成后
- 完成重大功能后
- 合并到 main 之前

**可选但很有价值：**

- 卡住时（获得新的视角）
- 重构前（建立基线）
- 修复复杂 bug 后

## 如何请求

**1. 获取 Git SHA：**

```bash
BASE_SHA=$(git rev-parse HEAD~1)  # or: git merge-base origin/main HEAD
HEAD_SHA=$(git rev-parse HEAD)
```

**2. 派遣代码评审子 Agent：**

派遣一个 `general-purpose` 子 Agent，并填写 [code-reviewer.md](code-reviewer.md) 模板。

**占位符：**

- `{DESCRIPTION}`——所构建内容的简要摘要
- `{PLAN_OR_REQUIREMENTS}`——它应该实现的行为
- `{BASE_SHA}`——起始提交
- `{HEAD_SHA}`——结束提交

**3. 处理反馈：**

- 立即修复 Critical 问题
- 继续前修复 Important 问题
- 记录 Minor 问题以便以后处理
- 如果评审者判断错误，用理由反驳

## 示例

```
[刚完成任务 2：添加验证函数]

你：继续前先请求代码评审。

BASE_SHA=$(git log --oneline | grep "Task 1" | head -1 | awk '{print $1}')
HEAD_SHA=$(git rev-parse HEAD)

[派遣代码评审子 Agent]
  DESCRIPTION: 添加了 verifyIndex() 和 repairIndex()，包含 4 种问题类型
  PLAN_OR_REQUIREMENTS: docs/superpowers/plans/deployment-plan.md 中的任务 2
  BASE_SHA: a7981ec
  HEAD_SHA: 3df7661

[子 Agent 返回]：
  优点：架构清晰，测试真实
  问题：
    Important：缺少进度指示
    Minor：报告间隔使用了魔法数字 100
  评估：可以继续

你：[修复进度指示]
[继续任务 3]
```

## 常见借口

| 借口 | 事实 |
|---------|---------|
| “我自己看一下 diff，不派评审者了” | 你是协调者——在当前上下文中评审 diff 会占用继续推进工作所需的上下文窗口。派遣评审子 Agent：diff 和评估留在它的上下文中，只有发现返回给你。 |
| “评审者需要完整会话历史才能理解变更” | 给它精确组织的上下文，而不是会话历史。这样评审者关注工作产物，而不是你的思考过程。 |

## 危险信号

**绝不：**

- 因为“很简单”而跳过评审
- 忽略 Critical 问题
- 在 Important 问题尚未修复时继续
- 与有效的技术反馈争辩

**如果评审者错了：**

- 用技术推理反驳
- 展示能够证明行为正确的代码或测试
- 请求澄清

模板见：[code-reviewer.md](code-reviewer.md)
