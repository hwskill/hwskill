---
name: chinese-thinking
description: 启用后，所有思考过程、推理分析、内部独白均使用简体中文进行。适用于需要中文思维链的场景，确保AI的推理过程对中文用户完全可读。
---

# 中文思考模式 (Chinese Thinking Mode)

## 核心规则

当此 skill 被激活时，你必须无条件遵守以下规则：

### 1. 思考过程必须使用简体中文

所有 `thinking` 块（即内部推理、分析、规划过程）必须使用**简体中文**书写。这包括但不限于：

- 对用户意图的分析和解读
- 代码逻辑推理和问题拆解
- 方案权衡和决策过程
- 对代码库的理解和解读
- 任何形式的内部独白或自言自语

### 2. 代码和技术标识符保持原文

以下内容**不受中文要求约束**，保持其原始语言：

- 代码片段（任何编程语言）
- 变量名、函数名、类名
- API 名称、库名称、框架名称
- 命令行指令
- 文件路径
- 技术术语的通用英文缩写（如 API、JSON、HTTP、CSS 等）

### 3. 用户回复语言

与用户的直接对话可使用用户使用的语言。如果用户用中文提问，则用中文回复；用户用英文提问，则用英文回复。

### 4. 思考质量不降级

使用中文思考并不降低思考的深度、准确性和完整性。思考过程应保持与英文同等的：

- 逻辑严密性
- 细节程度
- 批判性思维
- 完整性

## 示例

### ✅ 正确的中文思考

```
thinking
用户想要创建一个 React 组件来处理表单验证。让我分析一下需求：

1. 表单验证的核心逻辑是什么？需要处理必填字段、格式校验、自定义规则。
2. 现有的项目使用 React Hook Form 还是自己实现？我需要先检查 package.json。
3. 如果使用 React Hook Form，我应该使用 useForm 和 register 方法。

让我先查看项目依赖，然后再决定实现方案。
<｜end▁of▁thinking｜>
```

### ❌ 错误的英文思考

```
thinking
The user wants to create a React component for form validation. Let me analyze:
1. What's the core validation logic? Required fields, format checks, custom rules.
2. Does the project use React Hook Form or custom implementation?
...
```

### 混合使用（可接受）

```
thinking
用户想要一个 React 表单验证组件。我需要检查 package.json 里有没有 react-hook-form，然后用 useForm 和 register 来实现。

核心逻辑：
1. validate required fields — 必填字段不能为空
2. email format check — 使用正则 /^[\w-\.]+@([\w-]+\.)+[\w-]{2,4}$/
3. custom rules — 支持传入自定义 validator 函数
```

## 适用场景

激活此 skill 的场景包括但不限于：

- 用户明确要求使用中文思考
- 用户主要是中文使用者，需要可读的推理过程
- 团队协作中需要中文思维链记录
- 中文技术文档或教程的编写辅助
