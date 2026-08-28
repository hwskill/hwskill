# Claude Code 与 OpenCode 虚拟技能目录接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 hwskill 虚拟目录上增加 Claude Code 与 OpenCode 项目级接入，并用真实 Agent 验证 GitCode PR 技能脚本的运行时路径直达能力。

**Architecture:** 抽取宿主无关 Catalog renderer，三个 adapter 只处理各自上下文注入协议，共用 Profile resolver、MCP Search/Load 和审计。JSON 宿主配置采用结构化合并与摘要所有权；真实评测把各宿主公开事件归一化后交给同一严格判定器。

**Tech Stack:** Python 3.10+、标准库 `json/pathlib/subprocess`、PyYAML、FastMCP、Claude Code 2.1.141、OpenCode 1.14.48、Docker。

**Spec:** `docs/superpowers/specs/2026-08-28-claude-code-opencode-adapters-design.md`

## Global Constraints

- 宿主规范名固定为 `codex`、`claude-code`、`opencode`，只把 CLI 输入 `claude_code` 视为别名。
- 不创建或删除 `.agents/skills`、`.claude/skills`、`.opencode/skills`，并保留项目原有 Skill。
- Effective Catalog 只包含 digest、ID、description 和 Search/Load 指引，不包含 Skill 正文或资源清单。
- OpenCode 首个验证版本固定为 1.14.48，其他版本由 doctor 报 WARN。
- 真实提示词不包含 Skill ID、脚本名或脚本路径，不禁用目录查找工具。
- MiniMax token 不进入镜像层、Git、命令行参数、artifact 或审计日志。

---

### Task 1: 共享 Catalog renderer 与宿主 adapter

**Files:**
- Create: `src/hwskill/catalog.py`
- Create: `src/hwskill/claude_code_adapter.py`
- Create: `src/hwskill/opencode_adapter.py`
- Modify: `src/hwskill/codex_adapter.py`
- Test: `tests/test_host_adapters.py`

**Interfaces:**
- Produces: `render_effective_catalog(project: Path, registry_root: Path, audit: AuditWriter, session_id: str | None = None) -> str`
- Produces: Claude `session_start(input_data, registry_root, audit) -> dict[str, Any]`
- Produces: OpenCode `render_catalog(project, registry_root, audit, session_id=None) -> str`

- [ ] **Step 1: 写失败测试，固定共享目录及两种协议输出**

```python
def test_all_hosts_share_catalog_without_skill_body(self):
    text = render_effective_catalog(project, ROOT, AuditWriter(audit))
    assert "local/gitcode-pr-review-fetch" in text
    assert "# GitCode PR Review Fetch" not in text

def test_claude_hook_wraps_catalog_as_additional_context(self):
    output = claude_session_start({"cwd": str(project), "session_id": "c1"}, ROOT, audit)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"

def test_opencode_returns_plain_catalog(self):
    assert "catalog_digest:" in render_opencode_catalog(project, ROOT, audit)
```

- [ ] **Step 2: 运行 `PYTHONPATH=src python3 -m unittest tests.test_host_adapters -v`，确认因模块不存在而失败**
- [ ] **Step 3: 实现最小 renderer 和 wrapper；Codex 改为调用 renderer，不改变现有 JSON 契约**
- [ ] **Step 4: 重跑目标测试和 `tests.test_codex_runtime`，确认全部通过**
- [ ] **Step 5: 提交 `feat: share effective catalog across host adapters`**

### Task 2: Claude Code / OpenCode 配置合并与所有权

**Files:**
- Create: `src/hwskill/json_configuration.py`
- Modify: `src/hwskill/configuration.py`
- Test: `tests/test_host_configuration.py`

**Interfaces:**
- Produces: `setup_claude_code(project, registry_root, audit_path=None) -> SetupResult`
- Produces: `unsetup_claude_code(project) -> SetupResult`
- Produces: `setup_opencode(project, registry_root, audit_path=None) -> SetupResult`
- Produces: `unsetup_opencode(project) -> SetupResult`

- [ ] **Step 1: 写失败测试，覆盖空项目、保留旧配置、幂等、冲突和外部修改拒绝卸载**

```python
def test_claude_setup_preserves_existing_json_and_native_skills(self):
    settings.write_text('{"permissions":{"allow":["Read"]}}')
    legacy_skill.write_text("---\nname: legacy\n---\n")
    setup_claude_code(project, ROOT)
    assert json.loads(settings.read_text())["permissions"]["allow"] == ["Read"]
    assert legacy_skill.exists()

def test_opencode_unsetup_refuses_modified_managed_plugin(self):
    setup_opencode(project, ROOT)
    plugin.write_text(plugin.read_text() + "// external\n")
    with self.assertRaisesRegex(ValueError, "modified outside hwskill"):
        unsetup_opencode(project)
```

- [ ] **Step 2: 运行 `PYTHONPATH=src python3 -m unittest tests.test_host_configuration -v`，确认缺少接口而失败**
- [ ] **Step 3: 实现 JSON 读写、规范化摘要、冲突检测、精确删除和受管 OpenCode 插件模板**
- [ ] **Step 4: 重跑配置测试；用 `node --check <临时插件路径>` 验证生成 JavaScript 语法**
- [ ] **Step 5: 提交 `feat: configure Claude Code and OpenCode projects`**

### Task 3: CLI 与分宿主 Doctor

**Files:**
- Modify: `src/hwskill/cli.py`
- Modify: `src/hwskill/doctor.py`
- Modify: `tests/test_cli_runtime.py`
- Create: `tests/test_host_doctor.py`

**Interfaces:**
- Consumes: Task 1 adapter 与 Task 2 setup/unsetup 函数。
- Produces: `run_doctor(host: str, project: Path, registry_root: Path) -> list[CheckResult]`
- Produces: CLI 的 `setup/unsetup/doctor/adapter` 多宿主路由。

- [ ] **Step 1: 写失败 CLI 测试，验证 `claude_code` 别名、两种 setup/unsetup、adapter 输出和 doctor JSON**
- [ ] **Step 2: 写失败 doctor 测试，以 patch 的 `shutil.which/subprocess.run` 固定 OpenCode 1.14.48 为 PASS、其他版本为 WARN**
- [ ] **Step 3: 运行两份目标测试，确认 parser choices/旧签名导致预期失败**
- [ ] **Step 4: 增加 host 归一化和路由；把 doctor 拆为共享检查 + Codex/Claude/OpenCode 检查，保持 `doctor codex` 兼容**
- [ ] **Step 5: 运行目标测试与完整单元测试，确认无回归**
- [ ] **Step 6: 提交 `feat: expose Claude Code and OpenCode host commands`**

### Task 4: 多宿主事件归一化与严格观察器

**Files:**
- Create: `src/hwskill/eval_events.py`
- Modify: `src/hwskill/eval_observer.py`
- Modify: `tests/test_eval_observer.py`
- Create: `tests/test_host_eval_events.py`

**Interfaces:**
- Produces: `normalize_events(path: Path, host: str) -> list[dict[str, Any]]`
- Extends: `observe_script_resolution(..., host: str = "codex") -> dict[str, Any]`

- [ ] **Step 1: 用脱敏的 Claude stream-json 与 OpenCode JSON fixture 写失败测试，分别产生公共 search/load/command 事件**
- [ ] **Step 2: 增加失败测试：echo 提及路径不算执行、Load 前执行不算直达、Load 后 find 被报告为发现行为**
- [ ] **Step 3: 运行两份 observer 测试，确认因 normalizer/host 参数缺失而失败**
- [ ] **Step 4: 实现宿主 normalizer；将当前 Codex 解析保留为默认路径，公共判定逻辑不放宽 argv 匹配**
- [ ] **Step 5: 运行 observer 测试和完整单元测试**
- [ ] **Step 6: 提交 `feat: observe skill resolution across agent hosts`**

### Task 5: Docker 真实 Agent 评测与文档

**Files:**
- Modify: `docker/Dockerfile`
- Create: `docker/claude-code-gitcode-agent-eval.sh`
- Create: `docker/opencode-gitcode-agent-eval.sh`
- Create: `scripts/run_claude_code_gitcode_pr_agent_eval.sh`
- Create: `scripts/run_opencode_gitcode_pr_agent_eval.sh`
- Modify: `docker/entrypoint.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: 多宿主 CLI、adapter、observer。
- Produces: `artifacts/<host>-gitcode-pr-agent-eval/<run-id>/` 下的原始事件、审计、patch 和 `script-resolution.json`。

- [ ] **Step 1: 先扩展离线 entrypoint，使其 setup/adapter/doctor 两种宿主；运行 `bash scripts/run_docker_smoke.sh` 确认镜像尚缺所需 Agent 或命令而失败**
- [ ] **Step 2: 固定安装 Claude Code 2.1.141 与 OpenCode 1.14.48，加入容器内凭据读取但不输出 token 的启动逻辑**
- [ ] **Step 3: 实现两个宿主评测脚本，使用相同无路径提示词，输出原始事件并调用公共 observer 生成报告**
- [ ] **Step 4: 更新 README，记录 setup 命令、版本边界、凭据来源、运行命令和直达判定语义**
- [ ] **Step 5: 运行离线全套：`PYTHONPATH=src python3 -m unittest discover -s tests -v`、registry validate/build check、Docker smoke**
- [ ] **Step 6: 运行 Claude Code 真实评测，核对 patch、audit 和报告中的 `direct_resolution/execution_succeeded`**
- [ ] **Step 7: 运行 OpenCode 真实评测，核对相同字段；外部失败时保留诊断且不得生成通过报告**
- [ ] **Step 8: 扫描仓库与 artifacts，确认没有 MiniMax token、受管原生 skill 目录或意外凭据文件**
- [ ] **Step 9: 提交 `test: cover Claude Code and OpenCode agent execution`**

### Task 6: 最终验证与审查

**Files:**
- Modify: 仅修复审查发现且有回归测试覆盖的问题。

**Interfaces:**
- Consumes: 全部前序交付。
- Produces: 可复现验证记录和清洁工作树。

- [ ] **Step 1: 使用 `superpowers:verification-before-completion` 重跑全部离线验证并记录准确输出**
- [ ] **Step 2: 使用 `superpowers:requesting-code-review` 检查规范覆盖、配置安全、凭据泄漏和观察器假阳性**
- [ ] **Step 3: 对审查问题先写失败测试再修复，重跑相关测试与全套测试**
- [ ] **Step 4: 检查 `git diff --check`、`git status --short` 和提交历史，确保仅包含本轮范围内文件**
