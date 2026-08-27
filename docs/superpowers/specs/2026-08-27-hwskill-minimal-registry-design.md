# hwskill 最小能力库与 Codex 验证设计

**日期：** 2026-08-27  
**状态：** 已完成会话内设计评审，等待书面规格复核

## 1. 目标

在空仓库中建立一个可重复验证的最小能力库：

- 原样归档《SKILL 能力库调查与落地建议》。
- 将当前 `~/.agents/skills` 下的 3 个技能和 Superpowers 6.3.0 的 15 个技能完整复制为仓库内快照。
- 保存来源、版本、许可证、导入路径和内容校验和，为后续基于 Git 的定期更新预留接口。
- 提供可运行的 `hwskill` CLI、静态 Registry、Profile 绑定、解析、搜索和按需加载。
- 以虚拟 Catalog 为主链路，不向业务仓库或用户目录投影受管 `.agents/skills`。
- 使用 Codex CLI、STDIO MCP、SessionStart Hook 和 Demo 业务仓库完成可观察的端到端验证。
- 提供 Docker 纯净环境验证资产；Docker 不可用时仍能运行宿主单元和集成测试。

## 2. 非目标

首版不实现：

- 远程 Registry API、静态站点和向量数据库。
- 自动定时同步或无人审查地跟随上游最新版。
- 完整依赖 DAG、冲突求解、权限和组织级策略系统。
- Codex 以外的深度 Agent Adapter。
- 将中央技能复制到 `.agents/skills` 的原生安装路径。
- 观察或断言模型的隐藏推理过程。

## 3. 方案选择

采用 Python Core + Codex Adapter 的单包原型：

- Python Core 负责导入、校验、Catalog 构建、Profile 解析、搜索、加载和审计。
- 同一个 Python 包提供 STDIO MCP Server。
- Codex Adapter 通过 SessionStart Hook 注入有效 Catalog，通过 MCP 提供 Search/Load。
- 使用 PyYAML 读取治理配置，使用官方 Python MCP SDK 实现协议；其余逻辑优先使用标准库。

首版不拆分独立 TypeScript Adapter，也不打包完整 Codex Plugin。代码边界应允许后续拆分或插件化。

## 4. 仓库结构

```text
hwskills/
├── docs/
│   ├── research/
│   │   └── SKILL能力库调查与落地建议.md
│   └── superpowers/
│       ├── specs/
│       └── plans/
├── skills-src/
│   ├── l1/
│   │   ├── chinese-thinking/
│   │   └── superpowers/<skill-id>/
│   └── l2/
│       ├── gitcode-discussion-fetch/
│       └── gitcode-pr-review-fetch/
├── sources/
│   ├── local-agents-skills.yaml
│   └── superpowers.yaml
├── profiles/
│   ├── personal-baseline.yaml
│   ├── superpowers.yaml
│   └── codex-demo.yaml
├── src/hwskill/
│   ├── cli.py
│   ├── importer.py
│   ├── registry.py
│   ├── resolver.py
│   ├── audit.py
│   ├── mcp_server.py
│   └── codex_adapter.py
├── registry/catalog.json
├── examples/codex-demo/
├── tests/
└── docker/
```

`skills-src/` 故意不使用任何 Agent 自动发现目录。调查文档归档时保持正文不变。

治理分类首版采用：

- `chinese-thinking`：L1。
- Superpowers 全部子技能：L1，并由一个上游 bundle 管理。
- 两个 GitCode 内容获取技能：L2。

分类只影响治理元数据，不改变运行时加载行为。

## 5. 元数据

### 5.1 Skill 元数据

每个快照目录增加 `skill.yaml`：

```yaml
schema_version: 1
id: superpowers/test-driven-development
name: test-driven-development
description: Use when implementing any feature or bugfix...
layer: l1
status: experimental
source:
  source_id: superpowers
  revision: 6.3.0
  upstream_path: skills/test-driven-development
  imported_at: 2026-08-27T00:00:00Z
license: MIT
content_digest: sha256:...
```

`content_digest` 覆盖 portable Skill 快照，不包含 `skill.yaml`，避免自引用。文件排序、路径分隔符和内容读取方式必须固定，使校验和跨重复构建保持稳定。

### 5.2 Source 元数据

Source manifest 至少记录：

- `source_id` 和来源类型（`local` 或 `git`）。
- 原始本地路径或 Git URL。
- revision、版本或 commit。
- Skill 发现根和包含规则。
- 许可证与来源说明。
- 导入目标层级和命名空间。

本地绝对路径是 provenance，不是运行时依赖。Docker 镜像不包含这些原始目录。

### 5.3 Profile 与 Lock

Profile 只组合 Skill ID：

```yaml
schema_version: 1
id: codex-demo
description: Codex 虚拟 Catalog 端到端验证
skills:
  - superpowers/systematic-debugging
  - superpowers/test-driven-development
  - local/gitcode-pr-review-fetch
```

业务项目提交：

```text
.hwskills/profile.yaml
.hwskills/lock.yaml
```

`profile.yaml` 记录期望 Profile；`lock.yaml` 记录实际解析出的 Skill revision 和 digest。

## 6. Catalog 分层

必须区分：

1. `registry/catalog.json`：中央库全量 Skill 元数据索引。
2. Resolved Profile Catalog：Profile 展开和锁版本后的集合。
3. Effective Skill Catalog：结合当前项目、Agent 和环境过滤后的候选集合。
4. Loaded Skill：一次任务中真正加载的单个 Skill 正文。

全量 Registry 不直接进入 Agent 上下文。首版 Effective Catalog 在内存中生成，只在审计日志保存 Profile ID、Skill ID 列表和 Catalog digest。

## 7. CLI 契约

### 7.1 命令树

```text
hwskill
├── setup
├── unsetup
├── doctor
├── profile
│   ├── bind
│   ├── unbind
│   ├── list
│   └── resolve
├── skill
│   ├── search
│   └── load
├── registry
│   ├── import
│   ├── validate
│   └── build
├── adapter
│   └── codex
└── serve-mcp
```

面向人的命令默认输出表格或可读文本；`--json` 输出稳定机器格式；警告写 stderr。MCP 始终使用结构化协议。

### 7.2 项目路径解析

未提供 `--project` 时按以下顺序定位：

1. 当前目录所属的 Git 根目录。
2. 无 Git 仓库时使用当前工作目录。

写操作在交互式终端显示目标并确认；非交互写操作要求显式 `--project` 和 `--yes`。只读命令不询问，但显示解析出的项目路径。

### 7.3 `setup` / `unsetup`

`hwskill setup codex` 为目标 scope 配置 MCP 和 SessionStart Hook，不安装具体 Skill，不创建受管 `.agents/skills`。配置修改必须结构化合并并记录所有权。

项目已有的非 `hwskill` `.agents/skills` 必须保留。`doctor` 对潜在同名或路由竞争发出警告；只有检测到 `hwskill` 自己生成的原生投影时才报错。`unsetup` 只删除自身拥有的配置。

### 7.4 Profile 命令

- `profile bind <id>`：写项目 Profile 绑定并生成 lock。
- `profile unbind <id>`：删除指定绑定并重算 lock。
- `profile list`：列出当前项目绑定。
- `profile resolve`：只读展示解析后的 Effective Catalog。

### 7.5 Skill 命令

`skill search <query>` 只搜索当前 Effective Catalog，默认返回表格，`--json` 返回元数据。无绑定时返回空结果，不自动扩大权限边界。

`skill load <id>` 只允许加载当前 Effective Catalog 中的 Skill，并在返回前重新校验 lock 和 digest。它只返回内容，不执行脚本。

### 7.6 Registry 命令

- `registry import --source <manifest>`：复制完整快照、拒绝越界链接、生成 provenance 和 digest。目标内容不同时默认展示差异并失败，显式 `--update` 才能替换。
- `registry validate`：校验 frontmatter、目录/ID、引用、provenance、digest、Profile 引用、重复 ID 和路径安全。
- `registry build`：校验后确定性生成全量 `registry/catalog.json`；`--check` 只检查生成物是否过期。

未来可增加 `source check` 和 `source sync`。未提供 `--source` 时的交互式 Source 引导也记录为后续能力：询问来源类型、路径/URL、revision、包含规则、ID 和许可证并生成 manifest。首版不猜测来源，也不实现该向导。

## 8. Loader 输出契约

默认 `skill load` 和 MCP 文本响应返回运行时增强后的 Markdown。Loader 保留原始 frontmatter，并动态加入保留字段：

```yaml
x-hwskill-runtime:
  id: superpowers/systematic-debugging
  revision: 6.3.0
  content_digest: sha256:...
  skill_dir: /opt/hwskills/skills-src/l1/superpowers/systematic-debugging
  skill_file: /opt/hwskills/skills-src/l1/superpowers/systematic-debugging/SKILL.md
  registry_root: /opt/hwskills
  resources:
    scripts: scripts/
    references: references/
    assets: null
```

规则：

- 动态字段不写回快照；`content_digest` 始终对应原始快照。
- 所有路径规范化并验证位于 Registry 根内。
- 原始 Skill 若已包含 `x-hwskill-runtime`，校验失败，不静默覆盖。
- 不加入时间戳等不稳定字段。
- `skill load <id> --raw` 返回原始文件。
- `--json` 同时返回结构化元数据和增强后的 `content`。
- MCP `content[]` 提供增强 Markdown，`structuredContent` 提供 ID、revision、digest、路径和资源清单。
- 未来远程 Registry 使用 `skill_uri`；物化到本地缓存后才提供 `skill_dir`。

## 9. Codex Adapter 与可观察性

主链路：

```text
项目 .hwskills/profile.yaml
  → Resolver + lock
  → SessionStart Hook 注入 Effective Catalog + digest
  → Codex 调用 hwskill_search / hwskill_load
  → MCP Loader 返回增强后的 SKILL.md
  → Codex 执行业务任务
```

`hwskill adapter codex session-start` 从 Hook 输入取得 cwd/session，解析有效目录并输出 Codex Hook JSON。Hook 只注入 Skill ID、name、description、digest 和 Search/Load 使用说明，不注入完整正文。

`hwskill serve-mcp` 首版提供：

- `hwskill_search(query, limit)`。
- `hwskill_load(skill_id, expected_digest)`。

MCP stdout 只能输出协议消息，诊断写 stderr，审计写 JSONL。Codex MCP 配置标记为 required，使初始化失败能够阻止 Live Eval 被误判为通过。

审计日志至少记录：时间、会话、cwd、Profile、Catalog digest、事件、Skill ID、revision、content digest、结果和耗时。默认不记录完整 Prompt 或 Skill 正文。

Codex `exec --json` 的 JSONL 与 `hwskill` 审计日志共同提供可观察证据。验收不依赖隐藏思维链。

## 10. Demo 业务仓库

Demo 是一个小型 Python 订单计价服务：

- 已有单元测试。
- 预置折扣边界错误。
- 任务要求定位原因、补充回归测试并修复。
- `codex-demo` Profile 包含 `systematic-debugging`、`test-driven-development` 和一个无关技能，以验证选择而不是硬编码唯一结果。

Demo 不包含受管 `.agents/skills`。如果用户把本方案应用到已有业务仓库，原有 Skill 不被删除或覆盖。

## 11. 验证策略

### 11.1 单元与普通集成测试

覆盖：

- 完整复制、provenance、路径安全和确定性 digest。
- frontmatter、引用、Profile、lock 和重复 ID 校验。
- Catalog 确定性构建和 `--check`。
- 项目路径解析及交互/非交互写入保护。
- Search 候选边界和 Load digest 校验。
- 动态 frontmatter 合并、保留字段冲突和 `--raw`。
- Hook JSON、MCP Search/Load、stdout/stderr 隔离和审计事件。
- 旧有 `.agents/skills` 保留及受管原生投影检测。

### 11.2 Docker 离线集成测试

容器使用全新 HOME：

- 中央库位于 `/opt/hwskills`，Demo 位于 `/workspace/demo`。
- 不挂载宿主 `~/.agents/skills`、`~/.codex` 或原始导入目录。
- 验证虚拟 Catalog、Hook、MCP、Search、Load 和审计链路。
- 验证篡改或删除快照时 fail-closed。
- Demo 环境断言不存在 `.agents/skills`，证明链路不依赖原生技能。

### 11.3 可选 Codex Live Eval

在隔离容器中运行：

```bash
codex exec --json --ephemeral --sandbox workspace-write <demo-task>
```

只向该进程注入 `CODEX_API_KEY`，不复制宿主认证文件。保存 Codex JSONL、`hwskill` 审计 JSONL、最终 diff 和测试结果。

通过条件：

1. Hook 日志包含预期 Catalog digest。
2. Codex JSONL 包含预期 Search/Load MCP 调用。
3. Load 日志指向仓库快照且 digest 与 Catalog 一致。
4. Demo 回归测试通过。
5. 未绑定 Profile 的负例不能通过 `hwskill` 获取 Skill；不要求 Codex 无法独立解决任务。

缺少 Hook 或 Load 证据时，即使 Codex 碰巧修复业务错误，Live Eval 仍判失败。

## 12. 错误与安全策略

- checksum、lock 或候选边界不匹配时 Load fail-closed。
- 导入使用临时目录和原子替换；默认不覆盖已有差异。
- 不跟随越界符号链接，不复制特殊文件。
- 配置修改保留用户已有内容，只操作 manifest 标记为自身拥有的条目。
- Hook stdout 保持严格 JSON；日志不得混入协议输出。
- 凭据不写入命令参数、镜像、仓库、审计或测试产物。
- `doctor` 只诊断，不自动修复或删除用户文件。

## 13. 当前环境限制

当前 WSL 中 Docker CLI 提示 Docker Desktop WSL integration 未启用。实现完成后必须先运行宿主测试并交付完整 Docker 资产；若届时 Docker 可用，再运行容器离线测试和带凭据的可选 Live Eval。未实际运行的验证不得声明为通过。

