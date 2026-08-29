# hwskill 用户/项目双作用域与技能导出设计

## 背景

README 的“用户安装”和“使用指南”已经给出了新的目标命令形式。当前实现只支持项目级 Profile 和宿主集成，且 `profile list`、安装路径参数、技能导出等行为与 README 不一致。本设计统一用户级与项目级作用域，并补齐 Registry 查询、批量技能导出、诊断和隔离验证。

本设计确认以下产品语义：

- Profile 始终是复数集合，命令行使用逗号分隔。
- 项目存在 Profile 配置时完全覆盖用户 Profile；项目未配置时才回退用户 Profile。
- 用户级宿主集成从任意项目启动时动态解析当前项目，不能在配置中固定安装时的 cwd。
- `skill dump` 支持逗号分隔的多个 Skill ID，也支持不具歧义的 Skill Name。
- 用户级与项目级宿主集成允许共存，但运行时只注入一次 Catalog。

## 目标与非目标

### 目标

- 实现 `--user` 与 `--project [path]` 的一致作用域语义。
- 实现用户/项目两级 Profile 存储、覆盖解析和 Lock 校验。
- 实现三个宿主的用户级 Setup、Doctor 和 Unsetup。
- 实现 `profile list/set/show/unset` 与 `skill list/dump/dump-profile`。
- 让安装脚本接受 `--install-path`。
- 在全新 HOME、普通用户、离线 Docker 环境中验证用户级集成。
- 保留现有项目运行链路、内容摘要和配置所有权保护。

### 非目标

- 不自动合并用户和项目 Profile 集合。
- 不提供 Skill 导出的强制覆盖选项。
- 不把技能直接安装作为中心化 Hook/MCP 注入的替代实现。
- 不承诺未验证宿主版本一定兼容或一定不兼容。
- 不重构 Registry 的导入和构建格式。

## 命令接口

### 安装

```bash
curl -fsSL https://raw.gitcode.com/linkeo2012/hwskills/raw/main/install.sh | bash

curl -fsSL https://raw.gitcode.com/linkeo2012/hwskills/raw/main/install.sh \
  | bash -s -- --install-path=/path/to/hwskill
```

安装脚本同时接受 `--install-path=/path` 和 `--install-path /path`。路径优先级为命令参数、`HWSKILL_HOME`、`~/.local/share/hwskill`。未知参数或缺失参数值直接失败。

从已有 checkout 运行 `./install.sh` 时，指定的安装路径必须解析为当前 checkout；否则脚本拒绝执行并说明本地安装不会搬迁仓库。

### 宿主集成

```bash
hwskill setup <host> --user
hwskill setup <host> --project
hwskill setup <host> --project <project-root>

hwskill doctor <host> --user
hwskill doctor <host> --project
hwskill doctor <host> --project <project-root>

hwskill unsetup <host> --user
hwskill unsetup <host> --project
hwskill unsetup <host> --project <project-root>
```

`--user` 与 `--project` 互斥，且这些命令必须显式选择一个作用域。裸 `--project` 从 cwd 向上寻找 Git 根；找不到时使用 cwd，并在 stderr 输出最终目标。交互写入保留确认提示，`--yes` 可跳过提示；非交互写入要求 `--yes`。

### Profile

```bash
hwskill profile list
hwskill profile list --json

hwskill profile set personal-baseline,superpowers --user
hwskill profile set codex-demo,superpowers --project
hwskill profile set codex-demo,superpowers --project <project-root>

hwskill profile show --user
hwskill profile show --project
hwskill profile show --project <project-root>

# 删除该作用域的显式配置；项目删除后恢复用户回退
hwskill profile unset --user
hwskill profile unset --project
hwskill profile unset --project <project-root>
```

`profile list` 列出仓库 `profiles/*.yaml` 中的可用 Profile，包括 ID、描述和技能数。`profile show` 同时显示请求作用域的显式值，以及对项目请求生效的 Profile、来源和是否发生用户回退。`profile unset` 删除所选作用域的 Profile 与 Lock；删除项目配置后，解析恢复到用户回退。

现有 `profile bind/unbind` 保留一个版本，继续执行项目集合的增量修改并在 stderr 输出弃用提示。`profile resolve` 保留为有效 Catalog 诊断命令。

### Skill

```bash
hwskill skill list
hwskill skill list --json

hwskill skill dump \
  local/chinese-thinking,systematic-debugging \
  ~/.agents/skills

hwskill skill dump-profile \
  personal-baseline,superpowers \
  ~/.agents/skills
```

`skill list` 列出 Registry 中全部技能，不受 Effective Profile 限制。`skill search/load` 仍只访问当前项目的 Effective Catalog。`skill dump` 直接从 Registry 导出；`dump-profile` 导出指定 Profile 的技能并集。

## 作用域与路径模型

新增 `ScopeTarget`：

```python
ScopeTarget(
    kind="user" | "project",
    project_root=Path | None,
    config_root=Path,
    state_root=Path,
)
```

CLI 把参数一次性解析成该对象。Profile、配置 Adapter、Doctor 和 Info 接受 `ScopeTarget`，不再各自推导路径。

用户级路径：

```text
${XDG_CONFIG_HOME:-~/.config}/hwskill/profile.yaml
${XDG_CONFIG_HOME:-~/.config}/hwskill/lock.yaml
${XDG_STATE_HOME:-~/.local/state}/hwskill/setup-<host>.json
```

项目级路径保持：

```text
<project>/.hwskills/profile.yaml
<project>/.hwskills/lock.yaml
<project>/.hwskills/state/setup-<host>.json
```

测试通过临时 HOME、`XDG_CONFIG_HOME`、`XDG_STATE_HOME`、`CODEX_HOME` 和 `CLAUDE_CONFIG_DIR` 注入路径，不能访问开发机真实配置。

## Profile 存储与有效解析

两个作用域采用相同格式：

```yaml
schema_version: 1
profiles:
  - personal-baseline
  - superpowers
```

逗号分隔输入按以下规则解析：

1. 切分并去除两侧空白。
2. 拒绝空项。
3. 按首次出现顺序去重。
4. 在写入前验证全部 Profile 存在。
5. 生成新的 Effective Catalog 和 Lock。
6. 只有 Profile 与 Lock 都可生成时才进入替换阶段。

Profile 与 Lock 是两个独立文件，文件系统不能保证跨文件原子提交。实现先在同目录写入并校验两个临时文件，再依次 `replace`；任何中断最多产生可检测的 Profile/Lock 不一致，后续解析失败关闭，不会静默使用错误 Catalog。`profile unset` 同样先验证目标作用域，再删除 Lock 和 Profile；中断遗留的单个文件由解析和 Doctor 明确报告。

有效解析遵循：

1. 项目 `profile.yaml` 存在时使用项目 Profile 和项目 Lock。
2. 项目文件不存在时使用用户 Profile 和用户 Lock。
3. 两者都不存在时返回空 Catalog。
4. 项目文件中的空集合是有效的显式覆盖，不回退用户集合。
5. Lock 存在但与选中的 Profile 不一致时拒绝运行并要求重新执行 `profile set`。

解析结果增加 `effective_scope` 和 `profile_source`，使 Hook、MCP、Doctor、Info 和 JSON 输出共享同一事实源。

## 宿主集成

### Codex

用户配置位于 `${CODEX_HOME:-~/.codex}/config.toml`。用户级托管块只固定 Registry 和 Audit 参数，不带固定项目：

```toml
[mcp_servers.hwskill]
command = "hwskill"
args = ["serve-mcp", "--repo-root", "/path/to/hwskill"]
required = true
```

用户 Hook 调用 `hwskill adapter codex session-start --scope user`。Adapter 优先从事件 JSON 获取 cwd；MCP Server 使用启动 cwd。项目级配置继续写 `<project>/.codex/config.toml`。

### Claude Code

`claude-code` 是 hwskill 的规范宿主 ID，`claude_code` 只作为输入别名。产品名是 Claude Code，npm 包是 `@anthropic-ai/claude-code`，官方可执行命令是 `claude`。README 的宿主参数使用 `claude-code`，启动示例使用 `claude`，Doctor 只探测 `claude`。

用户 Hook 写入 `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`。用户 MCP 通过 Claude 官方 CLI 管理：

```bash
claude mcp add-json hwskill '<json>' --scope user
```

hwskill 不直接编辑 Claude 自行维护的 `~/.claude.json`。Setup 在同名 MCP 已存在但不属于 hwskill 时拒绝覆盖；Doctor 使用 `claude mcp get hwskill` 验证宿主实际识别到的配置；Unsetup 通过 Claude CLI 删除用户级 MCP。项目级配置继续直接维护公开格式 `.claude/settings.json` 和 `.mcp.json`。

### OpenCode

用户配置路径为：

```text
${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json
${XDG_CONFIG_HOME:-~/.config}/opencode/plugins/hwskill.js
```

全局 Plugin 在运行时取得当前工作目录，再调用带 `--scope user` 的 Catalog Adapter。项目级配置继续使用 `<project>/opencode.json` 和 `<project>/.opencode/plugins/hwskill.js`。

### 重叠集成

用户级和项目级集成允许共存。项目级同名 MCP 按宿主优先级覆盖用户 MCP。用户级 Hook/Plugin 每次调用时检查当前项目中同宿主的 Setup 状态；若项目配置完整且所有权校验通过，用户 Adapter 输出空结果，项目 Adapter 负责注入。OpenCode Plugin 只在 Adapter 输出非空时添加 System Prompt。

损坏、外部修改或缺少所有权状态的项目集成不视为有效覆盖，Doctor 报告 `incomplete`。Doctor 还检查一次会话是否可能产生重复注入。

## Registry 查询与 Skill 选择器

`skill list` 输出 ID、Name、Description、Layer、Source、Revision 和 Digest。普通输出至少显示 ID、Name、Layer、Revision；JSON 输出保留全部字段。

`skill dump` 的每个逗号分隔选择器按以下顺序解析：

1. 按完整 Skill ID 精确匹配。
2. ID 未命中时按 Skill Name 精确匹配。
3. Name 只有一个匹配时接受。
4. Name 匹配多个 Skill 时失败，列出候选 ID，并要求使用规范 ID。
5. 所有结果规范化为 Skill ID，并按首次出现顺序去重。

ID 与 Name 可以混用。空项、未知选择器或任一歧义使整个命令在写入前失败。`dump-profile` 对 Profile 使用相同的逗号解析规则，并对技能并集去重。

## Skill 导出

新增 `skill_export.py`。导出整个 Registry 快照目录，包括 `SKILL.md`、`scripts/`、`references/`、`agents/` 和其他快照文件。目标目录使用 Skill Name：

```text
<skills-dir>/<skill-name>/
```

导出流程：

1. 解析并验证全部 Skill 或 Profile。
2. 校验每个 Registry 内容摘要。
3. 预检不同 Skill 是否映射到相同目标 Name。
4. 预检全部已有目标。
5. 任一预检失败时不写文件。
6. 把新增目录复制到目标父目录中的临时目录。
7. 校验临时目录摘要。
8. 原子改名为最终目录。

目标存在且摘要一致时报告 `UNCHANGED`；内容不同时拒绝覆盖。首版不提供 `--force`。多 Skill 和多 Profile 导出必须保持命令级预检原子性，不能在发现后续冲突前先写入一部分目标。

## Doctor、Info 与宿主版本

`doctor --user` 检查用户 Profile/Lock、宿主用户配置、Hook/Plugin、MCP、所有权状态、可执行文件和宿主版本。`doctor --project` 检查项目显式 Profile 或用户回退、项目宿主配置、重叠集成、Effective Catalog 和 Lock 一致性。

`info` 同时报告用户 Profile、项目 Profile、有效集合、有效来源，以及每个宿主的用户/项目/有效集成状态。JSON 输出使用独立的 `user`、`project`、`effective` 和 `effective_scope` 字段。

宿主版本集中为 `HostSpec` 元数据：

```text
claude-code  executable=claude    verified=2.1.141
codex        executable=codex     verified=0.147.0
opencode     executable=opencode  verified=1.14.48
```

README 表头改为“已验证版本”。Doctor 对其他版本报告 `WARN` 和实际版本，而不是断言不兼容。

## 所有权、错误与兼容性

- Setup 继续只修改 hwskill 管理的块或条目。
- 同名但无所有权状态的配置拒绝覆盖。
- 托管内容被外部修改时，Setup/Unsetup 拒绝覆盖或删除。
- `--user` 与 `--project` 同时出现、均未出现或项目参数无效时在解析阶段失败。
- `profile bind/unbind` 保留一版并弃用；`profile list` 的旧“项目绑定列表”语义由 `profile show --project` 替代。
- `claude_code` 作为输入别名保留，所有输出规范化为 `claude-code`。
- 安装脚本继续支持 `HWSKILL_HOME`，但命令参数优先。

## 验证策略

### 单元与集成测试

覆盖：

- Scope 参数互斥、缺失、裸项目和显式路径。
- HOME/XDG/Codex/Claude 路径覆盖。
- Profile CSV、去重、空项、未知项、项目覆盖、用户回退、显式空集合和 Unset 恢复回退。
- Profile 与 Lock 的预生成、失败关闭和不一致错误。
- Skill ID、Name、混合输入、多个输入、Name 歧义和目标 Name 冲突。
- 多 Skill/多 Profile 导出、完整资源目录、摘要一致、已有目录和命令级预检原子性。
- 三个宿主的用户/项目配置、Doctor、Unsetup、外部修改保护和重复抑制。
- `info` 的用户、项目和有效状态。
- 安装参数形式、优先级、未知参数和本地 checkout 限制。

### Docker 离线隔离烟测

扩展现有固定版本镜像和 `--network none` 运行：

1. 以普通用户和全新 HOME 启动，断言没有宿主配置或技能投影。
2. 设置用户 Profile `personal-baseline,superpowers`。
3. 对 Codex、Claude Code 和 OpenCode 执行 `setup --user` 与 `doctor --user`。
4. 用宿主自身的配置查询能力确认全局 MCP 被识别；Claude 至少执行 `claude mcp get hwskill`。
5. 在没有项目 Profile 的工作区验证用户回退。
6. 在具有 `codex-demo` 项目 Profile 的工作区验证项目完全覆盖用户集合。
7. 同时存在用户和项目集成时验证 Catalog 只注入一次。
8. 通过真实 STDIO MCP Client 执行 Search 和 Load。
9. 验证 `profile list`、`skill list`、批量 `dump` 和 `dump-profile`，包括辅助资源与摘要。
10. 在新进程中复查持久化配置，排除只对当前 shell 生效的假阳性。
11. 全程保持运行阶段无网络。

已有真实 Agent Eval 继续覆盖项目级 Search→Load 行为；新增离线 Smoke 专门证明用户级路径、动态项目解析和宿主配置发现。

## 代码边界与实施顺序

新增或重点调整：

```text
install.sh
README.md
src/hwskill/cli.py
src/hwskill/scopes.py
src/hwskill/profiles.py
src/hwskill/skill_export.py
src/hwskill/configuration.py
src/hwskill/json_configuration.py
src/hwskill/doctor.py
src/hwskill/info.py
src/hwskill/paths.py
src/hwskill/models.py
src/hwskill/mcp_server.py
src/hwskill/*_adapter.py
tests/
docker/entrypoint.sh
scripts/run_docker_smoke.sh
```

实施顺序：

1. 建立 Scope、用户路径和 HostSpec。
2. 实现 Profile 双作用域存储、Lock 和有效解析。
3. 实现 `profile list/set/show/unset`。
4. 实现 `skill list`、选择器和安全导出。
5. Scope 化三个宿主的 Setup/Doctor/Unsetup。
6. 实现用户/项目重叠抑制。
7. 调整 Info 与安装脚本。
8. 完成单元和集成测试。
9. 扩展 Docker 离线隔离烟测。
10. 更新 README、版本表和兼容说明。
