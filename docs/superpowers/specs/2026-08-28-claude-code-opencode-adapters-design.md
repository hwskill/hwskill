# Claude Code 与 OpenCode 虚拟技能目录接入设计

**日期：** 2026-08-28  
**状态：** 已批准实施

## 1. 目标

在现有 Codex 验证链路上增加 Claude Code 和 OpenCode 两种真实 Agent 覆盖，使三种宿主都通过 `hwskill` 虚拟目录获得项目级 Effective Skill Catalog，并通过同一 STDIO MCP 按需 Search/Load Skill。继续使用 `gitcode-pr-review-fetch` 和 GitCode `openeuler/OmniStream#587` 验证 Agent 能从 Load 返回的运行时锚点直接定位并执行技能脚本。

完成标准：

- `hwskill setup/unsetup/doctor` 支持 `claude-code` 和 `opencode`；CLI 兼容输入别名 `claude_code`。
- Claude Code 通过项目 SessionStart Hook 注入 Catalog，通过项目 MCP 配置连接 `hwskill`。
- OpenCode 通过项目插件动态注入 Catalog，通过项目 MCP 配置连接 `hwskill`。
- 两种接入都不创建或删除 `.agents/skills`、`.claude/skills`、`.opencode/skills`；项目原有技能保持不变。
- 配置合并保留宿主已有设置，卸载只移除 `hwskill` 拥有且未被外部修改的条目。
- Docker 中用真实 Claude Code 和 OpenCode Agent 执行同一 GitCode PR 用例，并形成可比较的结构化观察报告。

## 2. 非目标

- 不把技能正文或 `registry/catalog.json` 全量内容直接注入 Agent。
- 不为 Skill 推断或枚举 `scripts/`、`references/`、`assets/` 等非标准资源。
- 不禁止 Agent 使用 `find`、`rg`、`ls` 等工具；这些工具调用只作为验证观察信号。
- 不观察或声称观察模型隐藏推理，只记录宿主公开的 Hook、MCP、命令执行和最终输出事件。
- 不实现 Claude Code/OpenCode 全版本兼容层、远程 Registry 或自动凭据迁移。

## 3. 架构

```text
项目 Profile + Lock
       │
       ▼
共享 Catalog Renderer ───────► Catalog digest + ID + description
       │                                  │
       ├─ Codex SessionStart Hook          ├─ Agent 调用 hwskill_search
       ├─ Claude SessionStart Hook         └─ Agent 调用 hwskill_load
       └─ OpenCode system transform                    │
                                                       ▼
                                           动态 frontmatter 中的
                                           skill_dir / skill_file
                                                       │
                                                       ▼
                                              Agent 直接执行脚本
```

`catalog.py` 只解析项目 Profile 并渲染宿主无关的目录文本。Codex、Claude Code 和 OpenCode adapter 只包装各自协议，不复制 Profile 解析逻辑。`mcp_server.py` 继续作为所有宿主唯一的 Search/Load 实现和审计入口。

## 4. Effective Catalog 契约

目录文本包含：

- 标题、`catalog_digest`。
- 使用说明：先 `hwskill_search`，再按需 `hwskill_load`。
- 当前项目允许的 Skill ID 和 description。

目录不包含 Skill 正文、来源仓库全量记录或资源清单。`hwskill_load` 保持现有 Markdown + frontmatter 输出；运行时动态字段只提供 `id`、`revision`、`content_digest`、`skill_dir`、`skill_file`、`registry_root`。Skill 正文可基于 `skill_dir` 定位自身声明的相对资源。

## 5. Claude Code 接入

### 5.1 项目配置

`hwskill setup claude-code` 结构化合并：

- `.claude/settings.json` 的 `hooks.SessionStart`：command hook 调用 `hwskill adapter claude-code session-start`。
- `.mcp.json` 的 `mcpServers.hwskill`：stdio command 为 `hwskill serve-mcp`。

Hook 从 stdin 接收 Claude Code 事件 JSON，按事件的 `cwd` 解析 Profile，stdout 返回：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "<Effective Catalog>"
  }
}
```

配置中使用绝对 Registry 路径和可选审计路径。项目 MCP 的信任/启用由评测临时 settings 显式批准，生产项目仍遵循 Claude Code 自身信任模型。

### 5.2 所有权

`.hwskills/state/setup-claude-code.json` 保存两个受管条目的规范化值和摘要。若同名 `hwskill` MCP 或相同 hook 已存在但不属于当前 state，setup 拒绝覆盖。unsetup 在摘要一致时只删除受管 hook 和 MCP 条目；外部修改后拒绝删除。空容器对象可清理，其他键和数组顺序保持不变。

## 6. OpenCode 接入

### 6.1 项目配置与插件

`hwskill setup opencode` 结构化合并：

- `opencode.json` 的 `mcp.hwskill`：`type: local`，command 数组运行 `hwskill serve-mcp`。
- `.opencode/plugins/hwskill.js`：受管项目插件。

插件实现 OpenCode 当前公开的 `experimental.chat.system.transform` hook。每次模型调用时，它执行 `hwskill adapter opencode catalog`，将返回的纯文本 Effective Catalog 追加到 `output.system`。这保证 Profile/lock 改变后无需重新 setup。插件不包含技能正文、资源清单或业务判断。

该 hook 在 OpenCode 1.14.48 仍标记为 experimental，因此首版将 1.14.48 作为验证版本。`doctor opencode` 对该版本报告 PASS，对未安装或其他版本报告 WARN，而不是静默宣称兼容。

### 6.2 所有权

`.hwskills/state/setup-opencode.json` 保存 MCP 条目、插件内容和摘要。setup 保留 `opencode.json` 其他内容；同名非受管 MCP 或插件拒绝覆盖。unsetup 只在摘要匹配时删除两项受管内容。

## 7. CLI 与 Doctor

宿主规范名为 `codex`、`claude-code`、`opencode`。命令行输入 `claude_code` 会归一化为 `claude-code`，输出和 state 文件只使用规范名。

```text
hwskill setup <host> --project <path> --repo-root <path> --yes
hwskill unsetup <host> --project <path> --yes
hwskill doctor <host> --project <path> --repo-root <path> [--json]
hwskill adapter claude-code session-start --repo-root <path>
hwskill adapter opencode catalog --project <path> --repo-root <path>
```

Doctor 的共享检查为 Registry、Profile、审计目录和“无受管原生技能投影”。宿主检查分别验证配置、MCP、Hook/插件、CLI 和受支持版本。项目原本存在的非受管 skills 只报告 WARN，不删除也不视为 hwskill 违规。

## 8. 可观察性与真实 Agent 评测

### 8.1 用例

两种宿主收到相同任务：获取 `https://gitcode.com/openeuler/OmniStream/pull/587` 的完整 patch，写到固定输出文件，并汇报结果。提示词不包含 Skill ID、脚本名或脚本路径，也不禁止任何工具。

期望公开事件序列：

1. Catalog 注入成功。
2. `hwskill_search` 的结果包含 `local/gitcode-pr-review-fetch`。
3. `hwskill_load` 成功并返回 `skill_file`。
4. Agent 在 Load 之后执行 `${dirname(skill_file)}/scripts/fetch_gitcode_pr_patch.py`。
5. 命令包含目标 PR URL 和输出参数，退出码为 0，patch 诊断可解析。

“直接定位”定义为：Load 之前没有执行目标脚本，Load 到首次脚本执行之间没有针对 Skill 目录、`SKILL.md` 或目标脚本名的目录发现命令，并且执行 argv 使用由 `skill_file` 派生的绝对路径。目录发现不被禁止；Load 前的发现会记录但不影响本指标，Load 后到首次执行之间一旦发生发现，报告 `direct_resolution: false`。

### 8.2 事件归一化

观察器为每个宿主提供输入 normalizer，把公开的 stream-json/JSON 事件转换为公共事件：MCP search、MCP load、command execution。公共判定器沿用现有严格 argv 解析，拒绝只在 echo/文本中提及脚本路径的假阳性。

报告至少包含：宿主、Agent 版本、模型、search/load/首次脚本事件位置、Load 返回的 `skill_file`、派生脚本路径、解析后的 argv、执行次数、成功状态、patch 诊断、执行前发现命令、`used_runtime_anchored_path` 和 `direct_resolution`。

### 8.3 Docker 与凭据

每次评测创建临时 HOME 和独立 artifact 目录。镜像固定安装 Claude Code 2.1.141；OpenCode 使用固定的 1.14.48 二进制。OpenCode 复用现有 MiniMax provider 登录文件的只读副本。Claude Code 在容器进程内从同一登录文件读取 MiniMax token，使用 MiniMax 的 Anthropic-compatible endpoint；token 不进入镜像层、Git、命令行参数、评测输出或审计日志。

容器需要网络访问真实 GitCode 和 MiniMax API。若登录态、模型名、网络或供应商服务不可用，评测必须失败并保存明确诊断，不得用合成事件冒充通过。

## 9. 测试策略

- 单元测试：共享 Catalog 文本、Claude Hook JSON、OpenCode Catalog 输出。
- 配置测试：空项目、保留既有设置、幂等 setup、所有权冲突、外部修改后拒绝 unsetup、原有 skills 保留。
- Doctor 测试：每宿主配置检查和 OpenCode 版本检查。
- Observer 测试：三种宿主 fixture 归一化、正确序列、目录发现、提前执行、echo 假阳性、失败重试。
- 集成测试：CLI setup/doctor/unsetup，MCP Search/Load，插件 JavaScript 语法。
- 真实评测：Claude Code 和 OpenCode 分别在 Docker 中跑 PR #587 用例；结果不作为无网络单元测试的前置条件。

## 10. 交付边界

本轮交付代码、测试、Docker/运行脚本、使用说明和实际运行证据。若外部凭据或服务阻止某个真实评测，代码与离线验证仍需完成，并单独报告外部阻塞及可复现命令。
