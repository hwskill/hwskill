# hwskill 最小能力库实施计划

> **供 Agent 执行者使用：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，逐任务执行本计划。所有步骤使用复选框跟踪。

**目标：** 建立 Git 维护的 Skill 快照库、面向人的 CLI、Profile 限定的虚拟 Catalog、可观察的 Codex Hook/MCP 加载链路，以及隔离 Demo 验证。

**架构：** 单一 Python 包负责安全导入、校验、确定性 Catalog、Profile 解析、搜索、加载、审计和 Codex 适配。Codex 只通过 SessionStart 接收 Effective Skill Catalog，并通过必需的 STDIO MCP 按需读取完整 Markdown；主链路不投影原生 .agents/skills。

**技术栈：** Python 3.10+、PyYAML 6.x、Python MCP SDK 1.x、unittest、Codex CLI 0.147.x，以及可用时的 Docker。

**规格：** docs/superpowers/specs/2026-08-27-hwskill-minimal-registry-design.md

## 全局约束

- 导入后的 portable Skill 内容与选定源目录逐字节一致，治理信息只写入同级 skill.yaml。
- 受管 setup 不创建、不替换、不删除 .agents/skills。
- 保留已有 Codex 配置和非 hwskill 管理的 Skill。
- CLI 默认输出表格或 Markdown；--json 是稳定机器接口。
- registry/catalog.json 是全量元数据，绝不直接进入 Agent 上下文。
- content_digest 不包含 skill.yaml 和运行时 frontmatter。
- 候选边界、lock、路径包含关系或 digest 不一致时，Load 必须失败。
- 审计日志不记录 Prompt、Skill 正文、凭据或 token。
- 最低 Python 版本为 3.10。
- Docker 和 Live Eval 只有实际成功执行后才能声明通过。

---

### 任务 1：Python 包骨架与调查文档归档

**文件：**

- 新建：pyproject.toml
- 新建：src/hwskill/__init__.py
- 新建：src/hwskill/__main__.py
- 新建：src/hwskill/cli.py
- 新建：tests/test_cli_smoke.py
- 新建：docs/research/SKILL能力库调查与落地建议.md
- 新建：.gitignore

**接口：**

- 输入：/mnt/c/Users/linkeo/Documents/Codex/2026-08-17/d/outputs/SKILL能力库调查与落地建议.md。
- 输出：main(argv: Sequence[str] | None = None) -> int，以及 hwskill 控制台入口。

- [ ] **步骤 1：先写失败的 CLI 冒烟测试**

~~~python
class CliSmokeTest(unittest.TestCase):
    def test_version_is_printed(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertRegex(output.getvalue(), r"^hwskill 0\.1\.0\n$")
~~~

- [ ] **步骤 2：运行并确认预期失败**

命令：PYTHONPATH=src python3 -m unittest tests.test_cli_smoke -v

预期：失败，原因是 hwskill.cli 尚不存在。

- [ ] **步骤 3：实现最小包和 CLI**

声明 Python >=3.10、PyYAML >=6,<7、mcp >=1,<2、版本 0.1.0 和控制台入口；用 argparse 实现 --version。

- [ ] **步骤 4：原样归档调查文档**

用 apply_patch 创建目标文件，然后运行：

~~~bash
cmp '/mnt/c/Users/linkeo/Documents/Codex/2026-08-17/d/outputs/SKILL能力库调查与落地建议.md' \
  'docs/research/SKILL能力库调查与落地建议.md'
~~~

预期：退出码 0，无输出。

- [ ] **步骤 5：验证并提交**

命令：PYTHONPATH=src python3 -m unittest tests.test_cli_smoke -v

预期：通过。

~~~bash
git add pyproject.toml .gitignore src tests docs/research
git commit -m "chore: scaffold hwskill and archive research"
~~~

### 任务 2：安全快照导入与来源信息

**文件：**

- 新建：src/hwskill/models.py
- 新建：src/hwskill/frontmatter.py
- 新建：src/hwskill/digest.py
- 新建：src/hwskill/importer.py
- 新建：tests/test_importer.py
- 新建：sources/local-agents-skills.yaml
- 新建：sources/superpowers.yaml
- 新建：skills-src/l1 和 skills-src/l2 快照树

**接口：**

- SourceSpec.from_mapping(data) -> SourceSpec
- parse_skill_markdown(text) -> tuple[dict, str]
- content_digest(skill_dir: Path) -> str
- import_source(spec, repo_root, update=False, imported_at=None) -> list[SkillRecord]

- [ ] **步骤 1：先写失败的导入测试**

~~~python
def test_digest_excludes_governance(self):
    records = import_source(self.spec, self.repo, imported_at="2026-08-27T00:00:00Z")
    target = self.repo / "skills-src/l1/local/example"
    before = content_digest(target)
    (target / "skill.yaml").write_text((target / "skill.yaml").read_text() + "\n")
    self.assertEqual(content_digest(target), before)
    self.assertEqual(records[0].content_digest, before)

def test_escape_symlink_is_rejected(self):
    (self.source / "example/escape").symlink_to(self.tempdir / "outside")
    with self.assertRaisesRegex(ImportError, "outside the skill directory"):
        import_source(self.spec, self.repo)

def test_existing_difference_requires_update(self):
    import_source(self.spec, self.repo)
    self.rewrite_source_body("changed")
    with self.assertRaisesRegex(ImportConflictError, "--update"):
        import_source(self.spec, self.repo)
~~~

- [ ] **步骤 2：运行并确认预期失败**

命令：PYTHONPATH=src python3 -m unittest tests.test_importer -v

预期：失败，原因是导入接口尚不存在。

- [ ] **步骤 3：实现 frontmatter、digest 和原子导入**

按照排序后的 POSIX 相对路径和文件字节计算 digest，只排除根 skill.yaml。拒绝绝对/越界链接、socket、设备和 FIFO。先复制到同级临时目录，校验并写 provenance，最后原子替换。源 frontmatter 含 x-hwskill-runtime 时拒绝导入。

- [ ] **步骤 4：验证导入测试**

命令：PYTHONPATH=src python3 -m unittest tests.test_importer -v

预期：通过。

- [ ] **步骤 5：添加 Source manifest 并导入真实技能**

local-agents-skills 只选择 chinese-thinking、gitcode-discussion-fetch、gitcode-pr-review-fetch。superpowers 选择 6.3.0 skills 根目录下所有直接包含 SKILL.md 的子目录，并记录 https://github.com/obra/superpowers 与 MIT。

~~~bash
PYTHONPATH=src python3 -m hwskill registry import --source sources/local-agents-skills.yaml
PYTHONPATH=src python3 -m hwskill registry import --source sources/superpowers.yaml
~~~

预期：共导入 18 个 Skill。

- [ ] **步骤 6：验证源与快照逐字节一致并提交**

递归比较所有选定源 Skill 与目标目录，只忽略新增 skill.yaml；18 个技能全部通过。

~~~bash
git add src/hwskill tests/test_importer.py sources skills-src
git commit -m "feat: import governed skill snapshots"
~~~

### 任务 3：Registry 校验与确定性 Catalog

**文件：**

- 新建：src/hwskill/registry.py
- 新建：tests/test_registry.py
- 新建：registry/catalog.json

**接口：**

- validate_registry(repo_root: Path) -> list[SkillRecord]
- build_catalog(repo_root: Path) -> dict
- write_catalog(repo_root: Path, check=False) -> bool

- [ ] **步骤 1：先写失败的校验和构建测试**

~~~python
def test_digest_drift_is_rejected(self):
    skill = next(self.repo.glob("skills-src/**/SKILL.md"))
    skill.write_text(skill.read_text() + "changed\n")
    with self.assertRaisesRegex(RegistryValidationError, "content_digest"):
        validate_registry(self.repo)

def test_catalog_is_sorted_and_reproducible(self):
    first = json.dumps(build_catalog(self.repo), ensure_ascii=False, sort_keys=True)
    second = json.dumps(build_catalog(self.repo), ensure_ascii=False, sort_keys=True)
    self.assertEqual(first, second)
    ids = [item["id"] for item in json.loads(first)["skills"]]
    self.assertEqual(ids, sorted(ids))

def test_check_detects_stale_catalog(self):
    (self.repo / "registry/catalog.json").write_text("{}\n")
    self.assertFalse(write_catalog(self.repo, check=True))
~~~

- [ ] **步骤 2：运行并确认预期失败**

命令：PYTHONPATH=src python3 -m unittest tests.test_registry -v

预期：失败，原因是 Registry 接口尚不存在。

- [ ] **步骤 3：实现校验器和 Catalog builder**

校验 ID 唯一、目录/name 一致、provenance、digest、资源引用、Profile 引用、路径包含关系和保留字段。输出 UTF-8 JSON，键和 Skill 排序、两空格缩进、末尾换行，不写构建时间。

- [ ] **步骤 4：验证真实 Registry**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_registry -v
PYTHONPATH=src python3 -m hwskill registry validate
PYTHONPATH=src python3 -m hwskill registry build
PYTHONPATH=src python3 -m hwskill registry build --check
~~~

预期：测试通过，18 个 Skill 通过校验，--check 不修改文件。

- [ ] **步骤 5：提交**

~~~bash
git add src/hwskill/registry.py tests/test_registry.py registry/catalog.json
git commit -m "feat: validate and build the skill catalog"
~~~

### 任务 4：Profile 解析、搜索与运行时加载

**文件：**

- 新建：src/hwskill/projects.py
- 新建：src/hwskill/profiles.py
- 新建：src/hwskill/search.py
- 新建：src/hwskill/loader.py
- 新建：tests/test_profiles.py
- 新建：tests/test_search_loader.py
- 新建：profiles/personal-baseline.yaml
- 新建：profiles/superpowers.yaml
- 新建：profiles/codex-demo.yaml

**接口：**

- find_project(start: Path) -> Path
- resolve_profiles(project, registry_root) -> EffectiveCatalog
- search_skills(catalog, query, limit=10) -> list[SearchResult]
- load_skill(catalog, skill_id, expected_digest=None, raw=False) -> LoadedSkill

- [ ] **步骤 1：先写失败的 Profile 测试**

~~~python
def test_find_project_prefers_git_root(self):
    nested = self.repo / "a/b"
    nested.mkdir(parents=True)
    (self.repo / ".git").mkdir()
    self.assertEqual(find_project(nested), self.repo)

def test_resolution_is_profile_scoped(self):
    catalog = resolve_profiles(self.project, self.registry)
    self.assertEqual(set(catalog.skill_ids), {
        "superpowers/systematic-debugging",
        "superpowers/test-driven-development",
        "local/gitcode-pr-review-fetch",
    })
    self.assertNotIn("superpowers/brainstorming", catalog.skill_ids)
~~~

- [ ] **步骤 2：先写失败的 Search/Load 测试**

~~~python
def test_search_stays_inside_effective_catalog(self):
    results = search_skills(self.catalog, "debug failing test")
    self.assertEqual(results[0].skill_id, "superpowers/systematic-debugging")
    self.assertNotIn("unbound/skill", [item.skill_id for item in results])

def test_load_enriches_frontmatter_without_changing_digest(self):
    loaded = load_skill(self.catalog, "superpowers/systematic-debugging")
    metadata, body = parse_skill_markdown(loaded.content)
    runtime = metadata["x-hwskill-runtime"]
    self.assertEqual(runtime["content_digest"], loaded.content_digest)
    self.assertTrue(Path(runtime["skill_file"]).is_file())
    self.assertEqual(content_digest(Path(runtime["skill_dir"])), loaded.content_digest)

def test_raw_load_is_byte_exact(self):
    loaded = load_skill(self.catalog, "superpowers/systematic-debugging", raw=True)
    self.assertEqual(loaded.content, Path(loaded.skill_file).read_text())

def test_unbound_load_is_rejected(self):
    with self.assertRaisesRegex(LoadError, "not in the Effective Skill Catalog"):
        load_skill(self.catalog, "superpowers/brainstorming")
~~~

- [ ] **步骤 3：运行并确认预期失败**

命令：PYTHONPATH=src python3 -m unittest tests.test_profiles tests.test_search_loader -v

预期：失败，原因是运行时模块尚不存在。

- [ ] **步骤 4：实现项目、Profile 与 lock 解析**

项目解析顺序为显式路径、Git root、cwd。绑定写期望 Profile ID；lock 写精确 Skill ID、revision 和 digest。只读操作遇到缺失绑定/lock 时返回空 Effective Catalog。

- [ ] **步骤 5：实现确定性 Search 和增强 Load**

统一大小写与标点，ID/name 精确命中优先于 description 词汇重叠，最终按 ID 打破平局。将 x-hwskill-runtime 合入 frontmatter，包含受 Registry 根约束的 skill_dir、skill_file、registry_root、resources、revision 和原始 digest。raw 模式返回原文。

- [ ] **步骤 6：验证并添加真实 Profile**

命令：PYTHONPATH=src python3 -m unittest tests.test_profiles tests.test_search_loader -v

预期：通过。

personal-baseline 包含 3 个本地 Skill；superpowers 包含 15 个 Skill；codex-demo 包含 systematic-debugging、test-driven-development 和无关候选 local/gitcode-pr-review-fetch。

- [ ] **步骤 7：提交**

~~~bash
git add src/hwskill tests/test_profiles.py tests/test_search_loader.py profiles
git commit -m "feat: resolve profiles and load skills on demand"
~~~

### 任务 5：人类 CLI、Codex 配置所有权与 Doctor

**文件：**

- 修改：src/hwskill/cli.py
- 新建：src/hwskill/table.py
- 新建：src/hwskill/configuration.py
- 新建：src/hwskill/doctor.py
- 新建：tests/test_cli.py
- 新建：tests/test_configuration.py
- 新建：tests/test_doctor.py

**接口：**

- 输出规格中确认的命令树。
- setup_codex(project, registry_root) -> SetupResult
- unsetup_codex(project) -> SetupResult
- run_doctor(project, registry_root) -> list[CheckResult]

- [ ] **步骤 1：先写失败的 CLI 格式测试**

~~~python
def test_search_defaults_to_table_and_json_is_opt_in(self):
    table = self.run_cli("skill", "search", "debug", "--project", str(self.project))
    self.assertIn("ID", table.stdout)
    self.assertIn("SCORE", table.stdout)
    machine = self.run_cli("skill", "search", "debug", "--project", str(self.project), "--json")
    self.assertIsInstance(json.loads(machine.stdout)["results"], list)

def test_noninteractive_bind_requires_project_and_yes(self):
    result = self.run_cli("profile", "bind", "codex-demo", stdin_isatty=False)
    self.assertNotEqual(result.code, 0)
    self.assertIn("--project", result.stderr)
    self.assertIn("--yes", result.stderr)

def test_profile_list_is_unambiguous(self):
    result = self.run_cli("profile", "list", "--project", str(self.project))
    self.assertEqual(result.code, 0)
    self.assertIn("PROFILE", result.stdout)
~~~

- [ ] **步骤 2：先写失败的配置所有权测试**

~~~python
def test_setup_preserves_unmanaged_skills_and_config(self):
    unmanaged = self.project / ".agents/skills/legacy/SKILL.md"
    unmanaged.parent.mkdir(parents=True)
    unmanaged.write_text("---\nname: legacy\ndescription: old\n---\n")
    config = self.project / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "existing-model"\n')
    setup_codex(self.project, self.registry)
    self.assertTrue(unmanaged.exists())
    self.assertIn('model = "existing-model"', config.read_text())
    self.assertFalse((self.project / ".agents/skills/hwskill").exists())
~~~

- [ ] **步骤 3：运行并确认预期失败**

命令：PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_configuration tests.test_doctor -v

预期：失败，原因是分组 CLI、配置和 Doctor 尚不存在。

- [ ] **步骤 4：实现命令分组与输出策略**

实现 setup、unsetup、doctor、profile bind/unbind/list/resolve、skill search/load、registry import/validate/build、adapter codex 和 serve-mcp。表格为默认输出，--json 输出一个 JSON 文档；Load 默认增强 Markdown，支持 --raw/--json。交互写操作确认目标，非交互写操作要求 --project 与 --yes。

- [ ] **步骤 5：实现 Codex 配置所有权**

向项目 .codex/config.toml 合并 required STDIO MCP 和 SessionStart Hook，同时保留无关内容。在 .hwskills/state/setup-codex.json 记录拥有的键和 digest。unsetup 只删除匹配的受管条目，条目被外部修改后拒绝删除。

- [ ] **步骤 6：实现 Doctor**

检查 Registry、Profile/lock、Skill digest、MCP 命令、Hook JSON、审计目录、Codex 版本、受管原生投影和非受管同名冲突。非受管 Skill 只产生 PASS/WARN，绝不改动。

- [ ] **步骤 7：验证并提交**

命令：PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_configuration tests.test_doctor -v

预期：通过。

~~~bash
git add src/hwskill tests/test_cli.py tests/test_configuration.py tests/test_doctor.py
git commit -m "feat: add profile CLI and Codex setup diagnostics"
~~~

### 任务 6：审计、SessionStart Hook 与 STDIO MCP

**文件：**

- 新建：src/hwskill/audit.py
- 新建：src/hwskill/codex_adapter.py
- 新建：src/hwskill/mcp_server.py
- 新建：tests/test_audit.py
- 新建：tests/test_codex_adapter.py
- 新建：tests/test_mcp_server.py

**接口：**

- AuditWriter.write(event: AuditEvent) -> None
- session_start(input_data, registry_root) -> dict
- MCP 工具 hwskill_search(query, limit=10)
- MCP 工具 hwskill_load(skill_id, expected_digest=None)

- [ ] **步骤 1：先写失败的审计和 Hook 测试**

~~~python
def test_audit_has_digest_but_no_prompt_or_body(self):
    writer = AuditWriter(self.path)
    writer.write(AuditEvent(event="load", skill_id="local/example",
                            content_digest="sha256:abc", result="ok"))
    record = json.loads(self.path.read_text())
    self.assertEqual(record["content_digest"], "sha256:abc")
    self.assertNotIn("prompt", record)
    self.assertNotIn("content", record)

def test_hook_injects_metadata_not_skill_body(self):
    output = session_start({"cwd": str(self.project), "session_id": "s1",
                            "source": "startup"}, self.registry)
    context = output["hookSpecificOutput"]["additionalContext"]
    self.assertIn("catalog_digest", context)
    self.assertIn("systematic-debugging", context)
    self.assertNotIn("# Systematic Debugging", context)
~~~

- [ ] **步骤 2：先写失败的 MCP 测试**

~~~python
async def test_load_returns_text_and_structured_metadata(self):
    result = await self.server.load("superpowers/systematic-debugging", self.digest)
    self.assertIn("x-hwskill-runtime", result.text)
    self.assertEqual(result.structured["content_digest"], self.digest)
    self.assertTrue(result.structured["skill_dir"].endswith("systematic-debugging"))
~~~

- [ ] **步骤 3：运行并确认预期失败**

命令：PYTHONPATH=src python3 -m unittest tests.test_audit tests.test_codex_adapter tests.test_mcp_server -v

预期：失败，原因是运行时集成模块尚不存在。

- [ ] **步骤 4：实现 JSONL 审计和 Hook**

审计只接受显式 dataclass 字段：UTC 时间、session、cwd、Profile ID、Catalog digest、事件、Skill ID、revision、digest、结果、错误码、耗时。Hook 从 stdin 读取一个 JSON 对象，只向 stdout 输出一个 Codex Hook JSON；诊断写 stderr。

- [ ] **步骤 5：使用官方 SDK 实现 MCP 工具**

使用 FastMCP，从服务配置取得项目 cwd，复用 Core Search/Load，返回增强 Markdown 和结构化运行时元数据，并审计成功/失败。协议 stdout 不输出日志。

- [ ] **步骤 6：安装依赖并验证**

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
~~~

预期：安装成功，hwskill --version 为 0.1.0，测试通过。若下载被沙箱阻止，仅对 pip 安装申请网络权限。

- [ ] **步骤 7：提交**

~~~bash
git add src/hwskill tests/test_audit.py tests/test_codex_adapter.py tests/test_mcp_server.py
git commit -m "feat: expose observable Codex catalog loading"
~~~

### 任务 7：Demo、Docker、Live Eval 与最终证据

**文件：**

- 新建：examples/codex-demo/order_pricing.py
- 新建：examples/codex-demo/tests/test_order_pricing.py
- 新建：examples/codex-demo/.hwskills/profile.yaml
- 新建：examples/codex-demo/.hwskills/lock.yaml
- 新建：examples/codex-demo/README.md
- 新建：tests/test_demo_integration.py
- 新建：docker/Dockerfile
- 新建：docker/entrypoint.sh
- 新建：scripts/run_docker_smoke.sh
- 新建：scripts/run_codex_live_eval.sh
- 新建：README.md
- 修改：本计划，勾选已完成步骤

**接口：**

- 输入：已安装的 hwskill、Registry、Codex CLI，以及可选的进程级 CODEX_API_KEY。
- 输出：离线 smoke 证据，以及可选 artifacts/codex.jsonl、hwskill-audit.jsonl、demo.diff、test-output.txt。

- [ ] **步骤 1：先写失败的 Demo 测试**

~~~python
def test_demo_starts_with_one_boundary_failure(self):
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=DEMO, text=True, capture_output=True)
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("test_discount_threshold_is_inclusive", result.stderr)

def test_demo_has_no_managed_native_skills(self):
    self.assertFalse((DEMO / ".agents/skills").exists())
~~~

- [ ] **步骤 2：运行并确认预期失败**

命令：.venv/bin/python -m unittest tests.test_demo_integration -v

预期：失败，原因是 Demo 尚不存在。

- [ ] **步骤 3：创建故意失败的边界 Demo 和绑定**

实现 calculate_total(subtotal: Decimal, discount_threshold: Decimal, discount_rate: Decimal) -> Decimal，故意使用 subtotal > discount_threshold，而测试要求阈值包含等号。提供低于、等于、高于阈值测试，初始只有等于用例失败。通过 CLI 绑定 codex-demo 并生成 lock。

- [ ] **步骤 4：实现 Docker 离线 smoke**

使用固定 Python 3.10 slim 镜像，安装本包与 Codex CLI 0.147.x，复制 Registry 到 /opt/hwskills、Demo 到 /workspace/demo，创建非 root HOME。断言没有 .agents/skills；无需凭据完成 Registry 校验、resolve、Hook JSON、MCP Search/Load、审计断言和已知 Demo 初始失败。

- [ ] **步骤 5：实现可选 Live Eval**

只在 codex exec 进程中传入 CODEX_API_KEY，使用 --json --ephemeral --sandbox workspace-write。断言 JSONL 出现 hwskill_search 与 hwskill_load，审计包含 lock digest，最终测试通过。另建无绑定项目，断言 Search 为空且 Load 被拒绝。

- [ ] **步骤 6：运行宿主验证**

~~~bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/hwskill registry validate
.venv/bin/hwskill registry build --check
.venv/bin/hwskill doctor codex --project examples/codex-demo --json
.venv/bin/hwskill profile resolve --project examples/codex-demo --json
.venv/bin/hwskill skill search "debug failing Python boundary test" --project examples/codex-demo --json
.venv/bin/hwskill skill load superpowers/systematic-debugging --project examples/codex-demo
git diff --check
~~~

预期：测试和校验通过，Demo Effective Catalog 恰含 3 个候选，Load frontmatter 含 x-hwskill-runtime。

- [ ] **步骤 7：运行可选环境验证**

Docker 可用时运行 bash scripts/run_docker_smoke.sh。只有用户显式提供 CODEX_API_KEY 时才运行 bash scripts/run_codex_live_eval.sh。未运行项以 NOT RUN 和不含秘密的确切原因记录。

- [ ] **步骤 8：记录用法和证据**

README 覆盖维护者流程（import、validate、build）、项目流程（setup、profile bind、doctor）、Agent 流程（Hook、Search、Load）、输出规则、动态 frontmatter、Docker、凭据边界以及实际执行命令。

- [ ] **步骤 9：规格覆盖复核并提交**

确认归档、18 个快照、2 个 Source、3 个 Profile、全量 Catalog、CLI、动态 Load、Hook/MCP、审计、Demo、Docker、负例边界、安全和环境限制均有对应文件与测试。

~~~bash
git add examples docker scripts tests/test_demo_integration.py README.md \
  docs/superpowers/plans/2026-08-27-hwskill-minimal-registry.md
git commit -m "test: add isolated Codex skill-loading demo"
~~~

