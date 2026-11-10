---
schema_version: 1
skill_id: vercel-labs/agent-browser
translated_at: 2026-10-27
---

# agent-browser

面向 AI Agent 的快速浏览器自动化 CLI。通过 CDP 控制 Chrome/Chromium，提供无障碍树快照和紧凑的 `@eN` 元素引用。

安装：`npm i -g agent-browser && agent-browser install`

## 从这里开始

本文件是一个发现用的引导文件，并非使用指南。运行任何 `agent-browser` 命令前，先通过 CLI 加载实际工作流内容：

```bash
agent-browser skills get core             # 从这里开始：工作流、常见模式、故障排查
agent-browser skills get core --full      # 包含完整命令参考和模板
```

CLI 提供的技能内容始终与已安装版本匹配，因此指令不会过时。这个引导文件的内容不能在不同发行版之间变动，所以它只指向 `skills get core`。

## 专项技能

当任务超出浏览器网页的范围时，加载专项技能：

```bash
agent-browser skills get electron          # Electron 桌面应用（VS Code、Slack、Discord、Figma 等）
agent-browser skills get slack             # Slack 工作区自动化
agent-browser skills get dogfood           # 探索性测试 / QA / 查找缺陷
agent-browser skills get derive-client     # 记录 HAR，为网站生成独立 API 客户端
agent-browser skills get vercel-sandbox    # 在 Vercel Sandbox microVM 中使用 agent-browser
agent-browser skills get protected-vercel-deployments  # 访问受保护的 Vercel 部署
agent-browser skills get agentcore         # AWS Bedrock AgentCore 云浏览器
```

运行 `agent-browser skills list` 查看已安装版本中所有可用内容。

## 为什么使用 agent-browser

- 快速的原生 Rust CLI，而非 Node.js 包装器
- 可与任何 AI Agent 配合使用（Cursor、Claude Code、Codex、Continue、Windsurf 等）
- 通过 CDP 控制 Chrome/Chromium，不依赖 Playwright 或 Puppeteer
- 带元素引用的无障碍树快照，可实现可靠交互
- 会话、认证保险库、状态持久化、视频录制
- 面向 Electron 应用、Slack、探索性测试和云服务提供商的专项技能

## 可观测性仪表板

仪表板独立于浏览器会话运行，端口为 4848，也可以通过代理或转发的 URL 打开，例如 `https://dashboard.agent-browser.localhost`。Agent 应停留在仪表板的源上：会话标签页、状态和流量在内部代理，因此无需暴露会话端口。
