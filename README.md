# hwskill 最小能力库

本仓库验证以 Git 快照为事实源、以 Profile 限定候选范围、通过 Hook 注入虚拟 Catalog、再由 MCP 按需加载 Skill 的最小链路。中央源码位于 `skills-src/`，不会被 Agent 当成项目原生技能目录。

## 当前内容

- 3 个 `~/.agents/skills` 快照。
- Superpowers 6.3.0 的 14 个 Skill 快照。
- 每个快照都有来源、revision、许可证状态和 SHA-256 内容摘要。
- `personal-baseline`、`superpowers`、`codex-demo` 三个 Profile。
- Codex、Claude Code、OpenCode 的项目级 Catalog 注入，及共用的 STDIO MCP Search/Load 和 JSONL 审计。
- 一个故意包含折扣阈值缺陷的 Demo 仓库。

## 用户安装

本地已经有仓库时，直接按当前 checkout 安装：

~~~bash
git clone https://gitcode.com/linkeo2012/hwskills.git
cd hwskills
./install.sh
~~~

本地没有仓库时，可用同一个脚本自举安装：

~~~bash
curl -fsSL https://raw.gitcode.com/linkeo2012/hwskills/raw/main/install.sh | bash
~~~

管道安装默认克隆到 `~/.local/share/hwskill`；指定位置时，把环境变量传给管道右侧的
shell：

~~~bash
curl -fsSL https://raw.gitcode.com/linkeo2012/hwskills/raw/main/install.sh \
  | HWSKILL_HOME=/path/to/hwskill bash
~~~

两种方式最终都会在仓库内创建私有 `.venv`，把 `~/.local/bin/hwskill` 链接到仓库启动器，
并在当前 shell 的个人配置文件中幂等写入 `PATH` 与 `HWSKILL_HOME`。无需激活虚拟环境；
重启终端或按安装输出提示 source 配置文件即可。

CLI 解析技能库位置的顺序为：显式 `--repo-root`、`HWSKILL_HOME`、仓库启动脚本位置。
无法通过这三种方式确定时会明确报错，不再静默使用当前工作目录。

## 维护者流程

~~~bash
hwskill registry import --source sources/local-agents-skills.yaml --repo-root .
hwskill registry import --source sources/superpowers.yaml --repo-root .
hwskill registry validate --repo-root .
hwskill registry build --repo-root .
hwskill registry build --repo-root . --check
~~~

导入默认不覆盖有差异的快照；审阅上游差异后才使用 `--update`。

## 项目流程

~~~bash
hwskill profile bind codex-demo --project /path/to/project --yes
hwskill setup codex --project /path/to/project --yes
hwskill doctor codex --project /path/to/project

hwskill setup claude-code --project /path/to/project --yes
hwskill doctor claude-code --project /path/to/project

hwskill setup opencode --project /path/to/project --yes
hwskill doctor opencode --project /path/to/project
~~~

setup 只合并宿主的 hwskill Hook/插件和 MCP 配置：Codex 使用 `.codex/config.toml`，
Claude Code 使用 `.claude/settings.json` 与 `.mcp.json`，OpenCode 使用
`opencode.json` 与 `.opencode/plugins/hwskill.js`。三者都不创建 `.agents/skills`、
`.claude/skills` 或 `.opencode/skills`，也不删除项目旧有 Skill。`claude_code` 仅作为
CLI 输入别名，规范宿主名始终为 `claude-code`。

## Agent 链路

1. SessionStart 根据 cwd 解析 `.hwskills/profile.yaml` 和 lock。
2. Hook/插件只注入 Effective Catalog 的 ID、description、digest 和强制 Search→Load 协议。
3. Agent 调用 `hwskill_search` 搜索当前候选集。
4. Agent 调用 `hwskill_load` 加载一个 Skill。
5. Loader 在原 frontmatter 中动态增加 `x-hwskill-runtime`，提供 `skill_dir`、`skill_file` 和 digest；仓库快照不被修改。

CLI 默认输出表格或 Markdown。传 `--json` 获取机器格式；`skill load --raw` 返回原始 `SKILL.md`。

## 验证

宿主测试：

~~~bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m hwskill registry validate --repo-root .
PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check
~~~

Docker 离线运行验证：

~~~bash
bash scripts/run_docker_smoke.sh
~~~

构建阶段需要下载 Python/npm 依赖；容器执行阶段使用 `--network none`，并使用全新 HOME。

可选 Codex Live Eval（使用已有 `codex login` 登录态，或显式 API key）：

~~~bash
CODEX_API_KEY=... bash scripts/run_codex_live_eval.sh
~~~

也可直接复用 `codex login` 的现有登录态。凭据不写入镜像、仓库或审计日志。

GitCode PR 脚本定位与执行的真实 Agent 验证：

~~~bash
bash scripts/run_gitcode_pr_agent_eval.sh
~~~

该验证在联网 Docker 容器内运行 Codex，让 Agent 自主搜索并加载
`local/gitcode-pr-review-fetch`，获取 `openeuler/OmniStream#587` 的完整 patch。
验证器事后分析 Codex JSONL，确认脚本命令直接锚定到 Load 返回的运行时 Skill 路径，
并分别报告直接定位与执行成功状态、脚本尝试次数，以及执行前是否出现技能目录搜索；
不会向 Agent 禁用 `find`、`rg` 或 `ls`。验证任务使用 Codex 0.147.0、
`gpt-5.6-sol` 和 medium reasoning。每次运行的证据写入独立的
`artifacts/gitcode-pr-agent-eval/<run-id>/` 目录，失败运行不会覆盖或冒充先前结果。

Claude Code 与 OpenCode 使用同一用例和相同观察标准：

~~~bash
bash scripts/run_claude_code_gitcode_pr_agent_eval.sh
bash scripts/run_opencode_gitcode_pr_agent_eval.sh
~~~

两者都从现有 OpenCode `minimax-cn-coding-plan` 登录文件读取凭据。登录文件只读挂载到
Docker，token 在容器进程内解析，不进入镜像、Docker 命令行、仓库、artifact 或审计日志。
OpenCode 1.14.48 使用 `minimax-cn-coding-plan/MiniMax-M2.5`。Claude Code 2.1.141 的
初始化事件记录了命令行请求标签，但真实 assistant 事件返回的模型为 `MiniMax-M3`；模型
归属应以响应事件为准，凭据 provider 为 `minimax-cn-coding-plan`。

OpenCode 的 Catalog 注入依赖 1.14.48 的
`experimental.chat.system.transform`。`doctor opencode` 只对该验证版本报告 PASS，其他版本
报告 WARN。评测提示词不包含 Skill ID、脚本名或脚本路径，也不禁用 `find`、`rg`、`ls`；
observer 事后要求公开事件满足 Search→Load→运行时绝对脚本路径，且路径执行前没有技能目录发现。

## 本次环境验证状态

| 检查 | 状态 | 说明 |
|---|---|---|
| Python 单元/集成测试 | 已运行 | 见最终交付记录 |
| Registry validate/build check | 已运行 | 见最终交付记录 |
| FastMCP 接口与测试 | 已运行 | 使用本机 uv 缓存的 MCP 1.28.1 |
| pip 可编辑安装 | 已运行 | Docker 全新 Python 3.11 环境成功构建 wheel 并安装 |
| Docker smoke | 已运行 | 容器执行阶段 `--network none`，离线烟测通过 |
| Codex Live Eval | 已运行 | Codex 0.147.0 经 Hook/MCP 完成 Search、Load、修复和 3/3 测试，审计事件齐全 |
| GitCode PR Agent Eval | 已运行 | Agent 直接使用 Load 返回路径执行脚本，无目录搜索；PR #587 patch 获取成功 |
| Claude Code GitCode PR Eval | 已运行 | Claude Code 2.1.141 + MiniMax-M3 完成 Search、Load 和一次直接脚本执行；2 个 diff 文件 |
| OpenCode GitCode PR Eval | 已运行 | OpenCode 1.14.48 + MiniMax-M2.5 完成 Search、Load 和一次直接脚本执行；2 个 diff 文件 |
