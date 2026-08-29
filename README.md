# hwskill 最小能力库

本仓库验证以 Git 快照为事实源、以 Profile 限定候选范围、通过 Hook 注入虚拟 Catalog、再由 MCP 按需加载 Skill 的最小链路。中央源码位于 `skills-src/`，不会被 Agent 当成项目原生技能目录。

## 当前内容

- 3 个 `~/.agents/skills` 快照。
- Superpowers 6.3.0 的 14 个 Skill 快照。
- 每个快照都有来源、revision、许可证状态和 SHA-256 内容摘要。
- `personal-baseline`、`superpowers`、`codex-demo` 三个 Profile。
- Codex、Claude Code、OpenCode 的用户级与项目级 Catalog 注入，及共用的 STDIO MCP Search/Load 和 JSONL 审计。
- 一个故意包含折扣阈值缺陷的 Demo 仓库。

## 用户安装

### 一键安装

```bash
curl -fsSL https://raw.gitcode.com/linkeo2012/hwskills/raw/main/install.sh | bash
```

默认克隆到 `~/.local/share/hwskill`，可通过参数指定安装位置：

```bash
curl -fsSL https://raw.gitcode.com/linkeo2012/hwskills/raw/main/install.sh \
  | bash -s -- --install-path=/path/to/hwskill
```

### 手动安装

本地已经有仓库时，直接按当前 checkout 安装：

```bash
git clone https://gitcode.com/linkeo2012/hwskills.git
cd hwskills
./install.sh
```

### 安装后验证

安装完成后，按照引导重启终端或者手动加载环境变量，执行以下命令可以查看当前状态

```bash
hwskill info
```

## 使用指南

能力库为多个不同场景收录了技能，不会直接安装到各个宿主工具的技能发现目录下，而是通过宿主工具集成的方式，按照使用场景去注入技能信息。

开始使用前，需要进行一些准备：

### 1. 为你的宿主工具安装 hwskill 集成

为当前用户安装集成（当前用户在所有项目中可使用 hwskill 能力）：

```bash
hwskill setup claude-code --user
```

为特定项目单独安装集成（只在特定项目中启用 hwskill 能力）：

```bash
# 方式1：通过参数指定项目
hwskill setup claude-code --project <project-root>

# 方式2：在项目中执行
cd <project-root>
hwskill setup claude-code --project
```

诊断集成安装情况：

```bash
hwskill doctor claude-code --user
hwskill doctor claude-code --project # in-project
hwskill doctor claude-code --project <project-root>
```

当前已支持集成的宿主工具如下。宿主标识用于 `hwskill setup`、`doctor` 和
`unsetup`；可执行命令用于启动宿主：

| 宿主标识    | 可执行命令 | 已验证版本 |
| ----------- | ---------- | ---------- |
| claude-code | `claude`   | 2.1.141    |
| codex       | `codex`    | 0.147.0    |
| opencode    | `opencode` | 1.14.48    |

### 2. 选择你的使用场景

查看可用的场景：

```bash
hwskill profile list
```

为当前用户设置全局默认场景：

```bash
hwskill profile set <profile-name> --user
hwskill profile set <profile-name,profile-name,...> --user
```

为特定项目设置使用场景：

```bash
# 方式1：通过参数指定项目
hwskill profile set <profile-name> --project <project-root>
hwskill profile set <profile-name,profile-name,...> --project <project-root>

# 方式2：在项目中执行
cd <project-root>
hwskill profile set <profile-name> --project
```

查看当前的设置情况：

```bash
hwskill profile show --user
hwskill profile show --project # in project
hwskill profile show --project <project-root>
```

项目设置优先于用户设置；项目没有显式设置时会回退到用户设置。显式设置空场景可以阻止
项目回退到用户设置，删除项目设置后则会恢复回退：

```bash
hwskill profile set --empty --project <project-root>
hwskill profile unset --project <project-root>
```

### 3. 开始使用

在项目中直接启动宿主工具，会话开始时会自动按场景注入技能信息，就如同直接安装在技能目录那样。

```bash
cd <your-project>
claude
```

### 4. 去中心化安装方式

项目推荐使用上述中心化维护方式，可以方便地更新技能，同时支持直接安装技能，可以在尚未支持的宿主工具中使用。

安装场景定义的技能集合：

```bash
hwskill skill dump-profile <profile-name> <skills-dir>
hwskill skill dump-profile <profile-name,profile-name,...> <skills-dir>
# skills-dir example: ~/.agents/skills
```

查看当前注册的全部技能：

```bash
hwskill skill list
hwskill skill list --json
```

安装特定的技能。选择器支持完整 skill ID，也支持没有二义性的 skill name；多个选择器
使用逗号分隔：

```bash
hwskill skill dump <skill-id,skill-name,...> <skills-dir>
# skills-dir example: ~/.agents/skills
```

## 维护者流程

> TODO: 待补充完善

```bash
hwskill registry import --source sources/local-agents-skills.yaml --repo-root .
hwskill registry import --source sources/superpowers.yaml --repo-root .
hwskill registry validate --repo-root .
hwskill registry build --repo-root .
hwskill registry build --repo-root . --check
```

导入默认不覆盖有差异的快照；审阅上游差异后才使用 `--update`。

## Agent 链路

1. SessionStart 根据 cwd 优先解析项目 `.hwskills/profile.yaml` 和 lock；项目未设置时回退到用户配置，显式空设置不回退。
2. Hook/插件只注入 Effective Catalog 的 ID、description、digest 和强制 Search→Load 协议。
3. Agent 调用 `hwskill_search` 搜索当前候选集。
4. Agent 调用 `hwskill_load` 加载一个 Skill。
5. Loader 在原 frontmatter 中动态增加 `x-hwskill-runtime`，提供 `skill_dir`、`skill_file` 和 digest；仓库快照不被修改。

CLI 默认输出表格或 Markdown。传 `--json` 获取机器格式；`skill load --raw` 返回原始 `SKILL.md`。

## 验证

宿主测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m hwskill registry validate --repo-root .
PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check
```

Docker 离线运行验证：

```bash
bash scripts/run_docker_smoke.sh
```

构建阶段需要下载 Python/npm 依赖；容器执行阶段使用 `--network none` 和全新 HOME，验证
三种宿主的用户级 setup/doctor、用户 Profile 回退与 adapter 注入，同时保留项目级集成验证。

可选 Codex Live Eval（使用已有 `codex login` 登录态，或显式 API key）：

```bash
CODEX_API_KEY=... bash scripts/run_codex_live_eval.sh
```

也可直接复用 `codex login` 的现有登录态。凭据不写入镜像、仓库或审计日志。

GitCode PR 脚本定位与执行的真实 Agent 验证：

```bash
bash scripts/run_gitcode_pr_agent_eval.sh
```

该验证在联网 Docker 容器内运行 Codex，让 Agent 自主搜索并加载
`local/gitcode-pr-review-fetch`，获取 `openeuler/OmniStream#587` 的完整 patch。
验证器事后分析 Codex JSONL，确认脚本命令直接锚定到 Load 返回的运行时 Skill 路径，
并分别报告直接定位与执行成功状态、脚本尝试次数，以及执行前是否出现技能目录搜索；
不会向 Agent 禁用 `find`、`rg` 或 `ls`。验证任务使用 Codex 0.147.0、
`gpt-5.6-sol` 和 medium reasoning。每次运行的证据写入独立的
`artifacts/gitcode-pr-agent-eval/<run-id>/` 目录，失败运行不会覆盖或冒充先前结果。

Claude Code 与 OpenCode 使用同一用例和相同观察标准：

```bash
bash scripts/run_claude_code_gitcode_pr_agent_eval.sh
bash scripts/run_opencode_gitcode_pr_agent_eval.sh
```

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

| 检查                          | 状态   | 说明                                                                                  |
| ----------------------------- | ------ | ------------------------------------------------------------------------------------- |
| Python 单元/集成测试          | 已运行 | 见最终交付记录                                                                        |
| Registry validate/build check | 已运行 | 见最终交付记录                                                                        |
| FastMCP 接口与测试            | 已运行 | 使用本机 uv 缓存的 MCP 1.28.1                                                         |
| pip 可编辑安装                | 已运行 | Docker 全新 Python 3.11 环境成功构建 wheel 并安装                                     |
| Docker smoke                  | 已运行 | 容器执行阶段 `--network none`，离线烟测通过                                           |
| Codex Live Eval               | 已运行 | Codex 0.147.0 经 Hook/MCP 完成 Search、Load、修复和 3/3 测试，审计事件齐全            |
| GitCode PR Agent Eval         | 已运行 | Agent 直接使用 Load 返回路径执行脚本，无目录搜索；PR #587 patch 获取成功              |
| Claude Code GitCode PR Eval   | 已运行 | Claude Code 2.1.141 + MiniMax-M3 完成 Search、Load 和一次直接脚本执行；2 个 diff 文件 |
| OpenCode GitCode PR Eval      | 已运行 | OpenCode 1.14.48 + MiniMax-M2.5 完成 Search、Load 和一次直接脚本执行；2 个 diff 文件  |
