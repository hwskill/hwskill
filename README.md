# hwskill 最小能力库

本仓库验证以 Git 快照为事实源、以 Profile 限定候选范围、通过 Hook 注入虚拟 Catalog、再由 MCP 按需加载 Skill 的最小链路。中央源码位于 `skills-src/`，不会被 Agent 当成项目原生技能目录。

## 当前内容

- 3 个在本仓库直接维护的 manual Skill。
- 固定跟踪 Superpowers `refs/tags/v6.3.0`（完整 resolved SHA）的 14 个 Skill 快照。
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

manual Skill 直接在 `skills-src/` 中维护；修改内容后更新其治理信息。upstream
Skill 由 source manifest 记录远程 Git repository、track 和已解析 revision。仓库不支持
从本地路径导入或同步 Skill。

```bash
# 检查上游固定 track 与当前快照的状态（只读）
hwskill source check superpowers --repo-root .

# 按已声明的策略更新上游 source；不会自动提交
hwskill source update superpowers --on-added ignore --on-removed fail --yes --repo-root .

# 修改 manual Skill 的 payload 后，重算其治理信息
hwskill skill update local/chinese-thinking --repo-root .

# Registry 的定向校验：逐个 Skill 与其 registry 治理信息
hwskill registry validate --repo-root .

# 确认 Catalog 可由当前输入确定性重建，且无需改写已提交的 Catalog
hwskill registry build --repo-root . --check

# 完整的离线仓库组织门禁：source、Skill、Catalog 与 Profile 的一致性
hwskill integrity-check --repo-root .

# 对本次变更选出的行为验证
hwskill test affected --base HEAD^ --runner docker
```

`source update` 的新增、删除与覆盖策略必须显式选择；固定 tag 如果被移动会作为错误报告，不会静默更新。

维护流程依次为 `source check` / `source update`、离线完整性检查、再执行受影响的行为验证。
`registry validate` 是聚焦于逐个 Skill 及其 registry 治理信息的校验；它是
`integrity-check` 的子集。`registry build --check` 只确认 Catalog 能够由当前输入确定性
重建，而不改写文件。`integrity-check` 是完整的离线仓库组织门禁：不拉取上游，也不执行行为测试；
行为测试由 `hwskill test affected` 单独负责。

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
PYTHONPATH=src python3 -m unittest discover -s tests/core -t . -v
PYTHONPATH=src python3 -m hwskill registry validate --repo-root .
PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check
```

维护 source 或 Skill 后，先运行上述框架测试；单独运行 source-lifecycle 相关测试可使用：

```bash
PYTHONPATH=src python3 -m unittest tests.core.test_source_manifest tests.core.test_git_source \
  tests.core.test_maintenance_transaction tests.core.test_source_maintenance tests.core.test_skill_maintenance -v
```

Docker 离线运行验证：

```bash
bash scripts/run_docker_smoke.sh
```

构建阶段需要下载 Python/npm 依赖；容器执行阶段使用 `--network none` 和全新 HOME，验证
三种宿主的用户级 setup/doctor、用户 Profile 回退与 adapter 注入，同时保留项目级集成验证。

可选的 Agent collection 由统一测试运行器执行。先检查 Docker、已配置的宿主、模型和
凭据；结果会输出每次运行独立的 artifact 目录：

```bash
hwskill test setup --check
hwskill test tests/profiles/codex-demo/test.yaml --runner docker
hwskill test tests/skills/local/gitcode-pr-review-fetch/test.yaml --runner docker
```

`--runner local` 只用于宿主上的功能调试；即使显示 PASS，也不是不可变性或安全隔离证据，
不能替代标准 Docker 结果。原因包括本地 Agent 进程可通过 `setsid` 脱离其已知进程组；
runner 会尽力终止已知后代并对 post-check 使用快照，但可信门禁仍必须使用 Docker 的
文件系统隔离。

`codex-demo` Profile collection 在隔离 fixture 中修复订单折扣阈值边界，并运行业务
单元测试。GitCode collection 要求 Agent 通过 Search/Load 使用
`local/gitcode-pr-review-fetch`，取得 `openeuler/OmniStream#587` 的 patch；post-check
检查运行时脚本路径、patch 输出路径和 API 诊断。凭据不写入镜像、仓库或 artifact。

兼容入口仍保留，均是对精确 collection path 的 Docker 调用，且会保留原先的凭据
前置检查及原样传递附加参数：

```bash
bash scripts/run_codex_live_eval.sh
bash scripts/run_gitcode_pr_agent_eval.sh
bash scripts/run_claude_code_gitcode_pr_agent_eval.sh
bash scripts/run_opencode_gitcode_pr_agent_eval.sh
```

Codex wrapper 使用已有 `codex login` 或 `CODEX_API_KEY`；Claude Code 与 OpenCode
wrappers 要求现有的 OpenCode MiniMax 登录文件。实际 host/model/reasoning 统一从
`hwskill test setup` 的用户级配置读取；当 Docker、凭据或模型不可用时命令以
`BLOCKED`（退出码 3）结束，不会静默跳过或宣称 Agent 用例通过。

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
| 旧版 Codex/GitCode Live Eval  | 历史结果 | 迁移前 2026-08-27/28 的独立脚本记录；不能验证当前 collection runner                 |
| 当前 Agent collection         | BLOCKED | 本次 `test setup --check` 的模型最小可用性探测未通过；未运行真实 Agent 用例            |
