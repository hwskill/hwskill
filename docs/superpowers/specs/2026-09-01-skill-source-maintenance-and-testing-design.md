# Skill 来源维护与测试门禁设计

**日期：** 2026-09-01
**状态：** 已完成会话内设计评审，等待书面规格复核

## 1. 背景

当前仓库通过 `sources/*.yaml` 中的 `kind: local` 和本地绝对 `root` 复制 Skill。即使 Skill 实际存在公开上游仓库，`upstream_url` 也只是说明字段，无法单独复现检查或更新；仓库原生自研 Skill 同样被建模成本地快照。

新设计将维护方式分为两类：

- `manual`：没有外部上游，由维护者直接在本仓库修改并通过提交或 PR 更新。
- `upstream`：跟踪 Git 仓库中的 branch、固定 tag 或固定 commit；检查和更新仍只产生本仓库工作树变更，后续由维护者提交或发起 PR。

两类 Skill 都禁止从维护者机器上的本地路径同步。

## 2. 本期目标与非目标

### 2.1 目标

- 建立可复现的 manual/upstream 来源模型。
- 支持 upstream source 的新增、检查、整体更新、track 切换和技能清单变化处理。
- 支持 Skill 的创建、仓库内更新、移动、重命名、删除、manualize 和 adopt。
- 支持显式维护 upstream ignore，包括提前忽略尚未出现的 Skill。
- 保证 source、Skill、Catalog、Profile 和测试定义的组织一致性。
- 建立 `tests/core`、`tests/skills`、`tests/profiles` 三类测试及标准 Docker 运行环境。
- 支持脚本动作和 Agent 动作，并把 Agent 可观察过程传给 post-check。
- 所有多文件写操作采用候选目录、校验和回滚，避免部分更新。

### 2.2 非目标

本期不实现：

- 定时 CI、Bot、平台认证或自动创建 PR。
- 自动执行 `git add`、commit、push 或 PR 创建。
- 自动选择新的 tag；tag track 始终指向维护者指定的固定 tag。
- 自动从任意本地目录导入或同步 Skill。
- 远程 Registry 服务、依赖求解或组织级权限系统。
- 观察或断言 Agent 的隐藏推理过程。

## 3. 方案选择

采用 source manifest 驱动的整体更新：

- manual Skill 不使用 source manifest。
- 一个 upstream source manifest 可以原子维护多个 Skill。
- 同一 source 的 Skill 共享 track 和 resolved revision。
- source manifest 同时保存上游跟踪意图、ignore 策略和已解析 Skill 清单。
- Skill 快照仍保存在 `skills-src/`，运行时不依赖网络或上游仓库。

不采用每 Skill 独立跟踪，因为这会允许同一上游的 Skill 落在不同 revision。也不采用 submodule/subtree，因为它会引入完整上游仓库结构，却仍不能替代 Skill 发现、筛选和治理。

## 4. 数据模型

### 4.1 Upstream source manifest

```yaml
schema_version: 2
source_id: superpowers
kind: upstream

upstream:
  repository: https://github.com/obra/superpowers.git
  track: refs/tags/v6.3.0
  skills_path: skills
  ignore:
    - path: unwanted-skill
      reason: not used
    - path: brainstorming
      reason: manualized

defaults:
  namespace: superpowers
  layer: l1
  license: MIT

resolved:
  revision: 0123456789abcdef0123456789abcdef01234567
  skills:
    - path: systematic-debugging
      id: superpowers/systematic-debugging
      layer: l1
      content_digest: sha256:...
```

字段语义：

- `source_id` 在仓库内全局唯一。
- `upstream.repository` 是 Git URL，不允许本地文件路径。
- `upstream.track` 使用规范 Git ref 或完整 commit SHA。
- `upstream.skills_path` 指向包含一个或多个 Skill 目录的上游相对路径。
- `upstream.ignore` 是长期维护意图，可包含当前 revision 尚不存在的安全相对路径。
- `defaults` 用于新发现 Skill 的 ID、layer 和 license 建议值。
- `resolved.revision` 是本次快照对应的完整 commit SHA。
- `resolved.skills` 是该 revision 下实际纳管的 Skill 集合。
- `resolved.skills[].layer` 和 `content_digest` 始终显式落盘，不运行时继承默认值。

### 4.2 Track

允许三种形式：

```yaml
track: refs/heads/main
track: refs/tags/v6.3.0
track: 0123456789abcdef0123456789abcdef01234567
```

- branch：检查远端分支头是否产生新 SHA。
- tag：跟踪固定 tag，不自动查找新 tag。若同名 tag 移动，报告异常，不静默更新。
- commit：固定完整 SHA，不自动漂移。

无论 track 类型如何，实际内容都由 `resolved.revision` 的完整 commit SHA 锁定。Annotated tag 使用 peeled commit。

### 4.3 Manual Skill

manual Skill 直接存在于 `skills-src/<layer>/<namespace>/<name>/`，其 `skill.yaml` 声明：

```yaml
source:
  kind: manual
```

manual Skill 不出现在任何 source manifest 中，也不记录本地同步路径。它的版本由本仓库 Git 历史表达。

### 4.4 Upstream Skill

upstream Skill 的 `skill.yaml` 至少记录：

```yaml
source:
  kind: upstream
  source_id: superpowers
  revision: 0123456789abcdef0123456789abcdef01234567
  upstream_path: systematic-debugging
```

其 ID、layer、revision 和 digest 必须与 source manifest 及 Catalog 一致。

### 4.5 组织不变量

- manual Skill 不能出现在任何 `resolved.skills`。
- upstream Skill 必须恰好出现在一个 source 的 `resolved.skills`。
- 同一 source 中 `resolved.skills[].path` 与 `upstream.ignore[].path` 互斥。
- 同一 upstream path 不能映射到多个 Skill ID。
- source、Skill 和 Catalog 的 kind、source ID、revision、layer、路径与 digest 必须一致。
- Profile 只能引用存在的 Skill ID。
- `manualize` 必须同时保留 Skill、移出 resolved 并加入 ignore。
- `adopt` 必须同时移出 ignore、加入 resolved 并切换 Skill 来源。

## 5. 命令模型

### 5.1 新增与变更命令

下面只展示本设计新增或改变的命令，不是 `hwskill` 的完整命令树。未列出的现有命令按兼容性表继续保留。

```text
hwskill
├── integrity-check
├── source
│   ├── add
│   ├── check
│   ├── update
│   ├── adopt
│   ├── ignore
│   │   ├── list
│   │   ├── add
│   │   └── remove
│   └── delete
├── skill
│   ├── create
│   ├── update
│   ├── move
│   ├── rename
│   ├── delete
│   └── manualize
└── test
    ├── setup
    ├── all
    ├── skills
    ├── profiles
    ├── affected
    └── <test-path>
```

现有命令兼容性：

| 现有命令 | 处理方式 |
|---|---|
| `info` | 保留，不改变职责 |
| `setup`、`unsetup`、`doctor` | 保留；`test setup` 不替代宿主集成配置 |
| `adapter`、`serve-mcp` | 保留 |
| `profile list/set/show/unset/resolve` | 保留 |
| `profile bind/unbind` | 保持当前兼容及弃用状态 |
| `skill list/search/load/dump/dump-profile` | 保留，并与新增的 Skill 维护命令共存 |
| `registry build` | 保留，继续确定性生成 Catalog |
| `registry validate` | 保留为 Registry 基础校验；作为 `integrity-check` 的子集复用 |
| `registry import` | 移除；本地路径导入与新来源模型冲突 |

职责边界：

```text
registry validate  ⊂  integrity-check
registry build        生成 Catalog
source/skill           修改仓库维护内容
test                   执行行为验证
```

### 5.2 新增 manual Skill

```bash
hwskill skill create team/code-review --layer l2
```

交互询问 description 和 license，直接在仓库 `skills-src/` 下创建 `SKILL.md` 与 `skill.yaml`。不提供 `--from <local-path>`。

维护者也可以直接在仓库创建文件；`integrity-check` 负责发现缺失或不一致的治理信息。

### 5.3 更新 manual Skill

维护者直接修改仓库内文件，然后执行：

```bash
hwskill skill update team/code-review
```

该命令只接受 manual Skill，校验内容和资源、同步 description、重算 digest，并生成新 Catalog。对 upstream Skill 调用时失败并提示使用 `source update`。

### 5.4 新增 upstream source

默认交互入口：

```bash
hwskill source add
```

向导顺序：

1. 询问 Git repository URL。
2. 用 repository 名预填 source ID，并校验仓库内不重名。
3. 查询远端默认分支，用规范 `refs/heads/<name>` 预填 track。
4. 获取目标 revision，扫描包含 `SKILL.md` 的目录。
5. 优先用 `skills` 或检测出的共同父目录预填 `upstream.skills_path`；不唯一时让维护者选择。
6. 展示发现的 Skill，默认全选。
7. 未选 Skill 明确写入 ignore。
8. 确认 namespace、默认 layer 和 license。
9. 展示完整摘要，确认后写入。

保留完整参数形式供脚本和测试使用。非交互创建必须用 `--include all` 或重复的 `--include <path>` 明确纳管范围，其余发现项进入 ignore。

### 5.5 检查和更新 source

```bash
hwskill source check superpowers
hwskill source check --all
```

`check` 只读报告：

- track 和 resolved revision 状态；
- 已纳管 Skill 的内容变化；
- 新增、删除和 ignored Skill；
- source、快照与 Catalog 的内部漂移。

更新命令：

```bash
hwskill source update superpowers
hwskill source update superpowers --select-track
hwskill source update superpowers --track refs/tags/v6.4.0
hwskill source update --all
```

- `--select-track` 交互列出 branch、tag，并允许输入完整 commit SHA。
- source ID 与 `--all` 互斥。
- `--all` 先完成全部 source 的获取、分析和选择，全部通过后统一写入。
- 新增或删除 Skill 时交互逐项处理。
- 非交互模式使用 `--on-added include|ignore|fail` 与 `--on-removed remove|manualize|fail`。
- 新增 Skill 使用 source 默认 layer；需要例外时由维护者在合并前修改 resolved 条目。
- 本期不自动 commit。

### 5.6 Ignore

```bash
hwskill source ignore list superpowers
hwskill source ignore add superpowers future-skill
hwskill source ignore remove superpowers unwanted-skill
```

- `add` 允许添加当前 revision 尚不存在的 Skill path。
- path 必须相对于 `upstream.skills_path`，不能绝对、越界或重复。
- 已纳管 Skill 不能直接 ignore，必须先 manualize 或删除。
- `remove` 只解除忽略，不立即纳管。
- 当前上游已存在且会与 manual Skill ID 冲突时，`remove` 拒绝并提示使用 `source adopt`。
- 当前尚不存在时允许解除；未来出现冲突时，`source update` 停止并提示 adopt。

### 5.7 Manualize 与 adopt

```bash
hwskill skill manualize superpowers/brainstorming
```

该命令保留内容和 ID，将 Skill 改为 manual，从 `resolved.skills` 移除，并把原 upstream path 加入 ignore。三项变化属于同一事务。

```bash
hwskill source adopt superpowers team/brainstorming --path brainstorming
```

- 本地不存在对应 ID 时，作为新 upstream Skill 纳管。
- manual 内容与上游完全相同时，只确认维护归属变化，不提示覆盖内容。
- 内容不同时展示 diff 并询问是否用上游覆盖。
- path 在 ignore 中时，确认 adopt 后自动移除 ignore。
- 非交互模式遇到已有 manual Skill 时默认失败，不能隐式改变归属。

### 5.8 移动与重命名

```bash
hwskill skill move team/code-review --layer l1
```

`move` 只改变 layer 和物理位置，Skill ID 不变。

```bash
hwskill skill rename team/code-review team/review
```

`rename` 显式改变 ID，并迁移仓库内 Profile、source resolved、测试定义和 Catalog 引用。仓库外用户或项目 lock 在下次解析时要求重新生成。

### 5.9 删除

单 Skill：

```bash
hwskill skill delete team/code-review
```

- manual：删除 Skill 目录。
- upstream：删除目录、移出 resolved，并将 upstream path 加入 ignore。
- 被 Profile 或测试引用时默认失败并列出引用。
- 只有显式 `--remove-from-profiles` 才修改 Profile。
- 删除前显示完整影响范围并二次确认。

整个 source：

```bash
hwskill source delete superpowers
hwskill source delete superpowers --skills delete
hwskill source delete superpowers --skills manualize
```

交互选择删除全部 Skill、全部 manualize 或取消。删除模式遇到 Profile 引用默认失败；manualize 模式删除 source manifest 后无需保留原 source ignore。

### 5.10 人类输出

人类可读输出使用标题、缩进和对齐表格，不使用全大写字段堆叠：

```text
Source update: superpowers

Source
  Repository   https://github.com/obra/superpowers.git
  Track        refs/tags/v6.4.0
  Revision     abc1234 → def5678

Skills
  Added        2
  Updated      5
  Removed      1

  Status    Skill                                  Layer
  added     superpowers/new-workflow               l1
  updated   superpowers/systematic-debugging       l1

Profiles
  superpowers    +superpowers/new-workflow

Tests
  Skills         6 affected
  Profiles       2 affected
```

摘要在前、明细在后。TTY 可使用克制颜色，重定向输出不包含颜色控制符。`--json` 提供稳定结构，不要求调用者解析人类文本。

## 6. 事务与安全

所有写命令遵循：

```text
读取当前状态
→ 生成变更计划
→ 在临时候选目录生成完整结果
→ 校验候选目录
→ 展示摘要与 diff
→ 用户确认
→ 替换真实工作树目标文件
```

- 网络获取、Skill 复制和 Catalog 构建均在候选目录完成。
- `source update --all` 是跨 source 的单个批次事务。
- 写入前检查目标文件是否存在重叠的未提交修改；重叠时停止。
- 不相关 dirty 文件不阻止操作，也不被触碰。
- 多文件替换使用备份清单；任一步失败都回滚原文件。
- 非交互写操作必须显式 `--yes`。
- 未提供处理策略的增删、归属冲突、Profile 删除或 manual 覆盖均失败。
- 删除和覆盖操作显示完整影响范围。

Upstream 获取规则：

- `source check` 查询远端 ref 和必要元数据。
- `source add/update/adopt` 在临时 Git 仓库获取精确对象。
- 不执行上游 hook、安装脚本或任意代码。
- 拒绝越界符号链接、设备文件、FIFO 和 socket。

Git ref 查询与获取分别基于 `git ls-remote` 和精确 ref fetch；相关语义以 [Git ls-remote](https://git-scm.com/docs/git-ls-remote.html) 与 [Git fetch](https://git-scm.com/docs/git-fetch) 官方文档为准。

## 7. 组织门禁

```bash
hwskill integrity-check
hwskill integrity-check --json
```

该命令只检查、不修复，也不运行行为测试。它复用并包含 `registry validate` 的基础 Registry 校验，再离线验证：

- 每个 `SKILL.md` 都有且只有一个 `skill.yaml`；
- ID、name、namespace、layer 和物理路径一致；
- manual/upstream 归属符合组织不变量；
- source resolved、Skill 和 Catalog 三方一致；
- resolved 与 ignore 互斥；
- Catalog 是当前仓库内容的确定性生成结果；
- Profile 和示例 lock 引用有效；
- 测试定义只引用存在的 Skill 与 Profile；
- 不存在孤立 Skill、source 条目或 Catalog 项。

Catalog 过期时失败并提示运行 `hwskill registry build`。联网的上游真实性检查属于 `source check`，不属于 `integrity-check`。

## 8. 测试体系

### 8.1 目录

```text
tests/
├── core/
├── skills/
│   └── <namespace>/<name>/
│       ├── test.yaml
│       ├── fixtures/
│       └── scripts/
└── profiles/
    └── <profile-id>/
        ├── test.yaml
        ├── fixtures/
        └── scripts/

docker/test/
├── Dockerfile
├── entrypoint.sh
├── requirements.lock
└── README.md
```

- `tests/core` 测试 hwskill 框架能力。
- `tests/skills` 测试单个 Skill 独立使用效果。
- `tests/profiles` 测试 Profile 中 Skill 集合的组合效果。
- 测试资产独立于 upstream Skill 快照，避免更新时被覆盖。

### 8.2 Test collection 与 case

每个 `test.yaml` 定义当前 Skill 或 Profile 的测试集合：

```yaml
schema_version: 1
target:
  kind: skill
  id: local/gitcode-pr-review-fetch

cases:
  - id: fetch-pull-request
    description: Agent 能定位并执行技能脚本获取 PR patch
    workdir: workspace

    prepare:
      type: command
      command: python3 prepare_fixture.py

    steps:
      - id: run-agent
        type: agent
        prompt: |
          获取指定 Pull Request 的完整 patch，并保存到 pr.patch。

      - id: run-business-tests
        type: command
        workdir: workspace/project
        command: python3 -m unittest discover -s tests -v

    post_check:
      type: command
      command: python3 validate_result.py
```

每个 case 包含：

- 可选的 `prepare: action_obj`；
- 至少一个 `steps: action_obj[]`；
- 必需的 `post_check: action_obj`。

执行顺序固定为 prepare、全部 steps、post-check。Prepare 失败时 case 为 BLOCKED。Step 的退出状态和 Agent 结果作为证据记录；只要环境允许，仍运行 post-check。Post-check 是业务通过与否的最终判定者。

### 8.3 Action

公共字段：

```yaml
id: optional-action-id
type: command|agent
workdir: optional-relative-path
```

工作目录优先级：

```text
action.workdir > case.workdir > case workspace root
```

所有 workdir 都相对于 case 的隔离 workspace 根解析，不层层拼接；拒绝绝对路径、`..` 越界和符号链接逃逸。

Command action：

```yaml
type: command
command: python3 validate_result.py
```

在标准 Linux 测试环境中通过 `/bin/bash -lc` 执行，保存命令、退出码、stdout 和 stderr。

Agent action：

```yaml
type: agent
workdir: workspace
prompt: |
  完成指定任务。
```

使用 `hwskill test setup` 配置的宿主、模型和凭据，记录可观察工具调用、命令执行、最终响应和工作树变化；不记录隐藏推理过程。

### 8.4 Case context 与 post-check

Runner 为每个 action 生成标准证据：

```text
artifacts/tests/<run-id>/<case-id>/
├── context.json
├── actions/
│   ├── prepare/
│   │   ├── result.json
│   │   ├── stdout.log
│   │   └── stderr.log
│   ├── run-agent/
│   │   ├── result.json
│   │   ├── events.jsonl
│   │   ├── final-response.md
│   │   ├── stdout.log
│   │   └── stderr.log
│   └── run-business-tests/
│       ├── result.json
│       ├── stdout.log
│       └── stderr.log
└── workspace.diff
```

`context.json` 索引所有 action 状态和 artifact。运行 post-check 时注入：

```text
HWSKILL_TEST_CONTEXT=<absolute-path>/context.json
HWSKILL_TEST_ARTIFACTS=<absolute-path>/artifacts/<case-id>
HWSKILL_TEST_WORKSPACE=<absolute-path>/workspace
```

Command post-check 可读取 Agent `events.jsonl`，检查工具顺序、参数、运行时路径、文件范围和业务结果。退出码 `0` 为 PASS、`1` 为 FAIL，其他退出码为 BLOCKED。

Agent post-check 获得只读 context 与 artifacts，最终必须返回符合 schema 的 JSON：

```json
{
  "result": "pass",
  "evidence": ["业务测试全部通过", "目标文件已生成"]
}
```

`pass` 为 PASS，`fail` 为 FAIL，缺失或无法解析为 BLOCKED。确定性断言优先使用 command post-check；Agent post-check 只用于难以程序化表达的语义判断。

### 8.5 Docker 与模型准备

标准门禁默认在 `docker/test` 维护的镜像中运行：

- 安装固定版本 Python、hwskill、宿主 CLI 和测试依赖；
- 为每个 case 创建隔离 fixture；
- 只读挂载 Registry 和测试定义；
- script 默认断网，Agent action 按声明启用所需网络；
- 凭据只在容器启动时注入，不写入镜像、仓库或 artifact。

准备命令：

```bash
hwskill test setup
hwskill test setup --check
```

交互 setup 检查 Docker、镜像、宿主版本、当前模型、reasoning effort、凭据来源与可用状态；配置可用时询问保留或替换，不可用时引导选择并做最小可用性探测。`--check` 只检查。

模型与 reasoning effort 写入用户级配置。仓库只声明测试需要的能力；token、API key 和登录文件不写入配置。

### 8.6 测试命令

```bash
hwskill test all
hwskill test skills
hwskill test skills superpowers/systematic-debugging
hwskill test profiles
hwskill test profiles codex-demo
hwskill test affected
hwskill test affected --base origin/main
hwskill test tests/skills/local/gitcode-pr-review-fetch/test.yaml
```

- `test all` 运行 core、Skill 和 Profile 测试。
- `test skills`、`test profiles` 可运行全部或指定目标。
- `test affected` 根据工作树或 Git base 选择受影响用例。
- `test <path>` 只接受 `tests/core`、`tests/skills`、`tests/profiles` 内路径。
- `hwskill test` 不隐式运行 `integrity-check`；两者职责独立。
- 标准门禁默认 `--runner docker`，`--runner local` 只作为调试方式。

### 8.7 受影响测试

| 变化 | 必须运行 |
|---|---|
| Skill 正文、脚本或资源变化 | 该 Skill 全部用例；所有包含它的 Profile 用例 |
| 新增 Skill，未加入 Profile | 新 Skill 用例 |
| Profile 增加或删除 Skill | 该 Profile 全部用例 |
| Skill rename | Skill 用例；所有迁移后的 Profile 用例 |
| 仅改变 layer | 不运行行为测试 |
| manualize/adopt 且内容和 ID 不变 | 不运行行为测试 |
| source revision 变化但 Skill digest 不变 | 不运行行为测试 |
| ignore 变化但 resolved 不变 | 不运行行为测试 |
| loader、search、Catalog 注入或 adapter 变化 | 全部 Skill 与 Profile 用例 |
| 单个测试或 fixture 变化 | 对应 Skill 或 Profile 用例 |

只要 Profile 的 Skill ID 集合或任一成员 digest 改变，就运行该 Profile 的全部用例，不能只运行看似直接相关的部分用例。

## 9. 写命令与门禁关系

写命令在候选目录中自动运行 `integrity-check`。内容或 Skill 集合发生变化时，生成受影响测试计划；行为测试与组织检查保持独立命令，但写命令默认要求受影响的必需用例通过后才应用候选变更。

显式 `--skip-tests` 可生成待验证的工作树修改，但必须在当前 Git 仓库的 gitdir 下写入 `hwskill/pending-verification.json`。该本地状态记录变更路径、候选 digest 和受影响测试计划，不进入工作树或提交。只有针对同一组 digest 的 `hwskill test affected` 成功才能删除该状态；`integrity-check` 成功不能清除行为未验证状态。新环境中没有该本地状态时，`test affected` 仍根据工作树与指定 Git base 重新计算测试范围，因此后续 CI 不依赖维护者机器的 gitdir 状态。

## 10. 旧机制迁移

- 删除 `kind: local` 和绝对 `root` 支持。
- 删除 `sources/local-agents-skills.yaml`，将其中三个 Skill 转为 manual。
- 将 `sources/superpowers.yaml` 转为 schema 2 upstream source。
- 用 Superpowers tag 对应的完整 commit SHA 填写 resolved revision。
- 现有 Superpowers Skill 写入 resolved；明确未纳管项写入 ignore。
- 升级 Skill metadata 与 Catalog schema，保留现有 Skill ID 和 Profile ID。
- 移除 `registry import` 命令和对应的本地路径 manifest 解析入口；CLI 帮助与迁移说明统一提示使用 `source add/update`。
- 将当前框架测试迁入 `tests/core`，Skill 测试迁入 `tests/skills`，Demo/Profile 组合测试迁入 `tests/profiles`。
- 用 `docker/test` 统一现有重复的宿主 eval 环境。

## 11. 退出状态与结构化结果

- `0`：成功，或检查无变化。
- `1`：业务校验失败、测试 FAIL，或 `source check` 发现可用更新。
- `2`：参数或 manifest 错误。
- `3`：网络、模型、凭据或外部环境导致 BLOCKED。
- `4`：本地冲突或候选变更无法安全应用。

所有检查和测试命令支持稳定 `--json` 结果，明确区分 current、update available、failed 和 blocked。

## 12. 验收标准

- 仓库、文档和测试不再使用本地绝对路径同步 Skill。
- manual Skill 只能通过仓库内容修改。
- upstream branch、固定 tag 和固定 commit 均可检查和更新。
- source 新增、批量更新、ignore、manualize、adopt 和删除符合本文交互。
- Skill 创建、更新、移动、重命名和删除保持引用一致。
- 任一维护命令失败不会留下部分写入。
- `integrity-check` 能检测 source、Skill、Catalog、Profile 和测试引用漂移。
- `test setup --check` 能报告 Docker、宿主、模型和凭据状态。
- `test all/skills/profiles/affected/<path>` 按 collection、case、action 和 post-check 协议运行。
- Agent 可观察过程能作为结构化 artifact 传给 post-check。
- 标准 Docker 环境能执行 core、Skill 和 Profile 测试集合。
- 未实际执行的 Docker 或 Agent eval 不会被声明为通过。
