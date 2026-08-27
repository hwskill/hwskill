# hwskill 最小能力库

本仓库验证以 Git 快照为事实源、以 Profile 限定候选范围、通过 Hook 注入虚拟 Catalog、再由 MCP 按需加载 Skill 的最小链路。中央源码位于 `skills-src/`，不会被 Agent 当成项目原生技能目录。

## 当前内容

- 3 个 `~/.agents/skills` 快照。
- Superpowers 6.3.0 的 14 个 Skill 快照。
- 每个快照都有来源、revision、许可证状态和 SHA-256 内容摘要。
- `personal-baseline`、`superpowers`、`codex-demo` 三个 Profile。
- Codex SessionStart Hook、STDIO MCP Search/Load 和 JSONL 审计。
- 一个故意包含折扣阈值缺陷的 Demo 仓库。

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
hwskill profile bind codex-demo --project /path/to/project --repo-root /path/to/hwskills --yes
hwskill setup codex --project /path/to/project --repo-root /path/to/hwskills --yes
hwskill doctor codex --project /path/to/project --repo-root /path/to/hwskills
~~~

setup 只合并 `.codex/config.toml` 中的 hwskill Hook/MCP 配置，不创建 `.agents/skills`，也不删除项目旧有 Skill。

## Agent 链路

1. SessionStart 根据 cwd 解析 `.hwskills/profile.yaml` 和 lock。
2. Hook 只注入 Effective Catalog 的 ID、description 和 digest。
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

## 本次环境验证状态

| 检查 | 状态 | 说明 |
|---|---|---|
| Python 单元/集成测试 | 已运行 | 见最终交付记录 |
| Registry validate/build check | 已运行 | 见最终交付记录 |
| FastMCP 接口与测试 | 已运行 | 使用本机 uv 缓存的 MCP 1.28.1 |
| pip 可编辑安装 | 已运行 | Docker 全新 Python 3.11 环境成功构建 wheel 并安装 |
| Docker smoke | 已运行 | 容器执行阶段 `--network none`，离线烟测通过 |
| Codex Live Eval | 已运行 | Codex 0.147.0 经 Hook/MCP 完成 Search、Load、修复和 3/3 测试，审计事件齐全 |
