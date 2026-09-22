---
schema_version: 1
skill_id: superpowers/writing-plans
translated_at: 2026-09-21
---

# 编写计划

## 概述

编写完整实施计划时，假设工程师完全不了解代码库，并且需要非常明确的实现指导。记录他们需要知道的一切：每项任务要改哪些文件、代码、测试、可能需要查阅的文档，以及如何验证。把完整计划拆成小任务。遵循 DRY、YAGNI、TDD，并频繁提交。

假设他们是熟练开发者，但几乎不了解我们的工具集和问题领域；也假设他们不擅长设计测试。

**开始时说明：**“I'm using the writing-plans skill to create the implementation plan.”

**上下文：**如果工作位于隔离 worktree，应在执行时通过 `superpowers:using-git-worktrees` 创建。

**计划保存位置：**`docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`

- 用户对计划位置的偏好优先于此默认值。

## 范围检查

如果规格包含多个独立子系统，应该已经在头脑风暴期间拆成多个子项目规格。如果没有，建议拆成多份计划——每个子系统一份。每份计划都应单独产出可运行、可测试的软件。

## 文件结构

定义任务前，先列出要创建或修改的文件，以及每个文件的职责。分解决策在这里固定下来。

- 设计边界清晰、接口明确的单元。每个文件只承担一种明确职责。
- 你最适合推理能够一次放进上下文的代码，处理聚焦文件时编辑也更可靠。优先选择较小、聚焦的文件，避免一个大文件承担太多职责。
- 一起变化的文件应放在一起。按职责拆分，不要按技术层拆分。
- 在现有代码库中遵循既有模式。如果代码库惯用大文件，不要擅自重构；但正在修改的文件已经难以维护时，可以把拆分写进计划。

该结构决定任务如何拆分。每项任务都应产生可以独立理解的自包含变更。

## 任务大小

任务是能够完成自身测试循环、值得一次独立评审门的最小单元。划分边界时，把设置、配置、脚手架和文档步骤并入需要这些内容的交付任务；只有评审者可能批准其中一项却拒绝相邻项时才拆分。每项任务结束时都必须得到可独立测试的交付物。

## 小步骤粒度

**每一步只做一个动作（2–5 分钟）：**

- “编写失败测试”——一步
- “运行并确认失败”——一步
- “编写使测试通过的最小实现”——一步
- “运行测试并确认通过”——一步
- “提交”——一步

## 计划文档头部

**每份计划必须以此头部开始：**

```markdown
# [Feature Name] Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** [用一句话说明构建什么]

**Architecture:** [用 2–3 句话说明实现方式]

**Tech Stack:** [关键技术或库]

**Spec:** [本计划实现的规格或设计文档路径——计划从规格推导，执行者必须同时阅读]

## Global Constraints

[规格中适用于整个项目的要求——版本下限、依赖限制、命名与文案规则、平台要求；逐行精确复制规格中的值。每项任务都隐含包含本节要求。]

## Review Focus

[规格隐含、但没有任何任务测试覆盖，且最可能影响实际用户的五类输入或失败模式——每行一个，按风险从高到低排列。规格是愿景文档：它说明软件必须做什么，不会穷举全部场景；规格没有提到某个输入，不代表可以让它破坏程序。打开规格写一次此列表。随后为每一行补充测试，并放入拥有相应代码的任务，保持该任务自己的步骤格式。]

---
```

## 任务结构

````markdown
### Task N: [Component Name]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

**Interfaces:**
- Consumes: [本任务使用的早期任务产物——准确签名]
- Produces: [后续任务依赖的内容——准确函数名、参数和返回类型。任务实施者只看到自己的任务；这个区块告诉他们相邻任务使用的名称和类型。]

- [ ] **Step 1: Write the failing test**

```python
def test_specific_behavior():
    result = function(input)
    assert result == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/path/test.py::test_name -v`
Expected: FAIL with "function not defined"

- [ ] **Step 3: Write minimal implementation**

```python
def function(input):
    return expected
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/path/test.py::test_name -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/path/test.py src/path/file.py
git commit -m "feat: add specific feature"
```
````

## 不允许占位符

每一步都必须包含工程师实际需要的内容。以下情况都是**计划失败**，绝不能写：

- “TBD”“TODO”“稍后实现”“补充细节”
- “添加适当的错误处理” / “添加验证” / “处理边界情况”
- “为上述内容编写测试”（没有给出真实测试代码）
- “与任务 N 类似”（重复代码——工程师可能跳着阅读任务）
- 只描述要做什么，却不说明如何做的步骤（代码步骤必须有代码块）
- 引用任何任务都没有定义的类型、函数或方法

## 自我评审

完整计划写完后，以全新视角对照规格检查。这是由你自己执行的检查清单，不是子 Agent 派遣。

**1. 规格覆盖：**快速浏览规格中的每一节和要求。能否指出实现它的具体任务？列出缺口。

**2. 占位符扫描：**在计划中搜索“不允许占位符”一节列出的危险模式，修复它们。

**3. 类型一致性：**后续任务使用的类型、方法签名和属性名是否与早期任务的定义一致？任务 3 的 `clearLayers()` 到任务 7 变成 `clearFullLayers()` 就是 bug。

**4. 评审重点：**规格隐含的每类输入或失败模式是否由某个任务的测试覆盖？把最可能伤害用户的五个未覆盖项放入 Review Focus，并把对应测试加入拥有该代码的任务。空列表表示你检查过且没有发现，而不是跳过检查。

发现问题时直接修复，无需再次评审。如果规格要求没有任务覆盖，添加相应任务。

## 执行交接

保存并完成自我评审后，提供计划链接供人类协作者阅读。如果对方已经明确指定执行方式，请其确认计划是否符合预期并等待审阅，然后使用已保留的执行方式。否则，请其先审阅计划，再选择执行方式。

**未指定执行方式时：**

**“Plan complete and saved to `docs/superpowers/plans/<filename>.md`. Please review the plan. Which execution approach would you prefer?**

- **Subagent-driven**——每项任务由新的子 Agent 实现，再由新的评审者检查；全部任务结束后还会做一次全分支评审。最彻底；每个任务和每次评审都要使用新的上下文。
- **Native**——我在当前会话中自己实现每项任务，遵循此运行环境的工作方式，最后由能力最强模型上的新评审者检查整个分支。成本最低、速度最快；结束前没有独立评审。计划已经承载设计，因此中等能力模型也能很好执行。

**For this plan I recommend <one of the two>, because <从计划中提炼一句理由：任务接口依赖程度、任务数量、上线错误代价>. Does the plan capture what you want, and which approach should we use?”**

**已经指定执行方式时：**

**“Plan complete and saved to `docs/superpowers/plans/<filename>.md`. Please review the plan. Does it capture what you want?”**

**如果选择 Subagent-driven：**

- **必需子技能：**使用 `superpowers:subagent-driven-development`

**如果选择 Native：**

- **必需子技能：**使用 `superpowers:executing-plans`
