# hwskill Minimal Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a Git-backed skill snapshot registry with a human CLI, Profile-scoped virtual Catalog, observable Codex Hook/MCP loading, and isolated Demo verification.

**Architecture:** One Python package owns immutable import, validation, deterministic Catalog generation, Profile resolution, Search, Load, audit, and Codex adaptation. Codex receives only the Effective Skill Catalog through SessionStart and obtains full Markdown through a required STDIO MCP server; native Skill projection is excluded.

**Tech Stack:** Python 3.10+, PyYAML 6.x, official Python MCP SDK 1.x, unittest, Codex CLI 0.147.x, Docker when available.

**Spec:** docs/superpowers/specs/2026-08-27-hwskill-minimal-registry-design.md

## Global Constraints

- Imported portable payloads remain byte-equivalent to their selected source directories; governance is a sibling skill.yaml.
- Managed setup never creates, replaces, or deletes .agents/skills.
- Existing Codex configuration and unmanaged Skills are preserved.
- Human CLI output defaults to tables or Markdown; --json is the machine interface.
- Registry/catalog.json is full metadata and never enters Agent context.
- content_digest excludes skill.yaml and runtime frontmatter.
- Load fails closed for candidate, lock, containment, and digest violations.
- Audit excludes prompts, Skill bodies, credentials, and tokens.
- Python 3.10 is the minimum.
- Docker and Live Eval are reported as passing only after successful execution.

---

### Task 1: Package Skeleton and Research Archive

**Files:**

- Create: pyproject.toml
- Create: src/hwskill/__init__.py
- Create: src/hwskill/__main__.py
- Create: src/hwskill/cli.py
- Create: tests/test_cli_smoke.py
- Create: docs/research/SKILL能力库调查与落地建议.md
- Create: .gitignore

**Interfaces:**

- Consumes the report at /mnt/c/Users/linkeo/Documents/Codex/2026-08-17/d/outputs/SKILL能力库调查与落地建议.md.
- Produces main(argv: Sequence[str] | None = None) -> int and console entry point hwskill.

- [ ] **Step 1: Write the failing CLI smoke test**

~~~python
class CliSmokeTest(unittest.TestCase):
    def test_version_is_printed(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertRegex(output.getvalue(), r"^hwskill 0\.1\.0\n$")
~~~

- [ ] **Step 2: Verify the test fails**

Run: PYTHONPATH=src python3 -m unittest tests.test_cli_smoke -v

Expected: FAIL because hwskill.cli does not exist.

- [ ] **Step 3: Implement packaging and minimal CLI**

Declare Python >=3.10, PyYAML >=6,<7, mcp >=1,<2, version 0.1.0, and the console entry point. Implement --version with argparse.

- [ ] **Step 4: Archive the report byte-for-byte**

Create the destination with apply_patch, then run:

~~~bash
cmp '/mnt/c/Users/linkeo/Documents/Codex/2026-08-17/d/outputs/SKILL能力库调查与落地建议.md' \
  'docs/research/SKILL能力库调查与落地建议.md'
~~~

Expected: exit 0, no output.

- [ ] **Step 5: Verify and commit**

Run: PYTHONPATH=src python3 -m unittest tests.test_cli_smoke -v

Expected: PASS.

~~~bash
git add pyproject.toml .gitignore src tests docs/research
git commit -m "chore: scaffold hwskill and archive research"
~~~

### Task 2: Safe Snapshot Import and Provenance

**Files:**

- Create: src/hwskill/models.py
- Create: src/hwskill/frontmatter.py
- Create: src/hwskill/digest.py
- Create: src/hwskill/importer.py
- Create: tests/test_importer.py
- Create: sources/local-agents-skills.yaml
- Create: sources/superpowers.yaml
- Create: skills-src/l1 and skills-src/l2 snapshot trees

**Interfaces:**

- Produces SourceSpec.from_mapping(data) -> SourceSpec.
- Produces parse_skill_markdown(text) -> tuple[dict, str].
- Produces content_digest(skill_dir: Path) -> str.
- Produces import_source(spec, repo_root, update=False, imported_at=None) -> list[SkillRecord].

- [ ] **Step 1: Write failing import tests**

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

- [ ] **Step 2: Verify failure**

Run: PYTHONPATH=src python3 -m unittest tests.test_importer -v

Expected: FAIL because importer interfaces do not exist.

- [ ] **Step 3: Implement frontmatter, digest, and atomic import**

Hash sorted POSIX relative paths and bytes; exclude only root skill.yaml. Reject absolute/escaping links, sockets, devices, and FIFOs. Copy to a sibling temporary directory, validate, write provenance, and atomically rename. Reject source frontmatter containing x-hwskill-runtime.

- [ ] **Step 4: Run importer tests**

Run: PYTHONPATH=src python3 -m unittest tests.test_importer -v

Expected: PASS.

- [ ] **Step 5: Add manifests and import real Skills**

local-agents-skills selects exactly chinese-thinking, gitcode-discussion-fetch, and gitcode-pr-review-fetch. superpowers selects every direct child with SKILL.md from version 6.3.0 and records https://github.com/obra/superpowers plus MIT.

~~~bash
PYTHONPATH=src python3 -m hwskill registry import --source sources/local-agents-skills.yaml
PYTHONPATH=src python3 -m hwskill registry import --source sources/superpowers.yaml
~~~

Expected: 18 imported Skill records.

- [ ] **Step 6: Verify payload equivalence and commit**

Add a recursive comparison test that ignores only imported skill.yaml. It must pass for all 18 Skills.

~~~bash
git add src/hwskill tests/test_importer.py sources skills-src
git commit -m "feat: import governed skill snapshots"
~~~

### Task 3: Registry Validation and Deterministic Catalog

**Files:**

- Create: src/hwskill/registry.py
- Create: tests/test_registry.py
- Create: registry/catalog.json

**Interfaces:**

- Produces validate_registry(repo_root: Path) -> list[SkillRecord].
- Produces build_catalog(repo_root: Path) -> dict.
- Produces write_catalog(repo_root: Path, check=False) -> bool.

- [ ] **Step 1: Write failing validation/build tests**

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

- [ ] **Step 2: Verify failure**

Run: PYTHONPATH=src python3 -m unittest tests.test_registry -v

Expected: FAIL because registry functions do not exist.

- [ ] **Step 3: Implement validator and Catalog builder**

Validate unique IDs, directory/name agreement, provenance, digest, references, Profile references, containment, and reserved runtime keys. Emit UTF-8 JSON with sorted keys, sorted Skills, two-space indentation, trailing newline, and no timestamp.

- [ ] **Step 4: Verify real Registry**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_registry -v
PYTHONPATH=src python3 -m hwskill registry validate
PYTHONPATH=src python3 -m hwskill registry build
PYTHONPATH=src python3 -m hwskill registry build --check
~~~

Expected: tests pass, 18 Skills validate, and --check exits 0 without modifying Catalog.

- [ ] **Step 5: Commit**

~~~bash
git add src/hwskill/registry.py tests/test_registry.py registry/catalog.json
git commit -m "feat: validate and build the skill catalog"
~~~

### Task 4: Profile Resolution, Search, and Runtime Load

**Files:**

- Create: src/hwskill/projects.py
- Create: src/hwskill/profiles.py
- Create: src/hwskill/search.py
- Create: src/hwskill/loader.py
- Create: tests/test_profiles.py
- Create: tests/test_search_loader.py
- Create: profiles/personal-baseline.yaml
- Create: profiles/superpowers.yaml
- Create: profiles/codex-demo.yaml

**Interfaces:**

- Produces find_project(start: Path) -> Path.
- Produces resolve_profiles(project, registry_root) -> EffectiveCatalog.
- Produces search_skills(catalog, query, limit=10) -> list[SearchResult].
- Produces load_skill(catalog, skill_id, expected_digest=None, raw=False) -> LoadedSkill.

- [ ] **Step 1: Write failing Profile tests**

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

- [ ] **Step 2: Write failing Search/Load tests**

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

- [ ] **Step 3: Verify failure**

Run: PYTHONPATH=src python3 -m unittest tests.test_profiles tests.test_search_loader -v

Expected: FAIL because runtime modules do not exist.

- [ ] **Step 4: Implement project, Profile, and lock resolution**

Use explicit project, Git root, then cwd. Keep expansion deterministic. Binding writes desired Profile IDs; lock records exact Skill IDs, revisions, and digests. Missing binding/lock returns an empty catalog for read operations.

- [ ] **Step 5: Implement deterministic Search and enriched Load**

Normalize case/punctuation, rank exact ID/name before description overlap, and tie-break by ID. Merge x-hwskill-runtime into parsed frontmatter with contained absolute skill_dir, skill_file, registry_root, resources, revision, and original digest. --raw returns stored bytes.

- [ ] **Step 6: Verify and add real Profiles**

Run: PYTHONPATH=src python3 -m unittest tests.test_profiles tests.test_search_loader -v

Expected: PASS.

personal-baseline contains three local Skills; superpowers contains 15 Skills; codex-demo contains systematic-debugging, test-driven-development, and local/gitcode-pr-review-fetch.

- [ ] **Step 7: Commit**

~~~bash
git add src/hwskill tests/test_profiles.py tests/test_search_loader.py profiles
git commit -m "feat: resolve profiles and load skills on demand"
~~~

### Task 5: Human CLI, Owned Codex Setup, and Doctor

**Files:**

- Modify: src/hwskill/cli.py
- Create: src/hwskill/table.py
- Create: src/hwskill/configuration.py
- Create: src/hwskill/doctor.py
- Create: tests/test_cli.py
- Create: tests/test_configuration.py
- Create: tests/test_doctor.py

**Interfaces:**

- Produces the approved command tree.
- Produces setup_codex(project, registry_root) -> SetupResult.
- Produces unsetup_codex(project) -> SetupResult.
- Produces run_doctor(project, registry_root) -> list[CheckResult].

- [ ] **Step 1: Write failing CLI-format tests**

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

- [ ] **Step 2: Write failing ownership test**

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

- [ ] **Step 3: Verify failure**

Run: PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_configuration tests.test_doctor -v

Expected: FAIL because grouped CLI/configuration/Doctor are missing.

- [ ] **Step 4: Implement command groups and output policy**

Implement setup, unsetup, doctor, profile bind/unbind/list/resolve, skill search/load, registry import/validate/build, adapter codex, and serve-mcp. Tables are default; --json emits one document; Load defaults to enriched Markdown and accepts --raw/--json. Interactive writes confirm the resolved target; noninteractive writes require --project and --yes.

- [ ] **Step 5: Implement owned Codex configuration**

Merge required STDIO MCP and SessionStart Hook entries into project .codex/config.toml while preserving unrelated text. Record owned keys and digests in .hwskills/state/setup-codex.json. Unsetup removes only matching owned entries and refuses changed owned entries.

- [ ] **Step 6: Implement Doctor**

Check Registry, Profile/lock, digests, MCP command, Hook JSON, audit directory, Codex version, managed native projection, and unmanaged name overlap. Unmanaged Skills produce PASS/WARN and are never changed.

- [ ] **Step 7: Verify and commit**

Run: PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_configuration tests.test_doctor -v

Expected: PASS.

~~~bash
git add src/hwskill tests/test_cli.py tests/test_configuration.py tests/test_doctor.py
git commit -m "feat: add profile CLI and Codex setup diagnostics"
~~~

### Task 6: Audit, SessionStart Hook, and STDIO MCP

**Files:**

- Create: src/hwskill/audit.py
- Create: src/hwskill/codex_adapter.py
- Create: src/hwskill/mcp_server.py
- Create: tests/test_audit.py
- Create: tests/test_codex_adapter.py
- Create: tests/test_mcp_server.py

**Interfaces:**

- Produces AuditWriter.write(event: AuditEvent) -> None.
- Produces session_start(input_data, registry_root) -> dict.
- Exposes MCP tools hwskill_search(query, limit=10) and hwskill_load(skill_id, expected_digest=None).

- [ ] **Step 1: Write failing audit and Hook tests**

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

- [ ] **Step 2: Write failing MCP test**

~~~python
async def test_load_returns_text_and_structured_metadata(self):
    result = await self.server.load("superpowers/systematic-debugging", self.digest)
    self.assertIn("x-hwskill-runtime", result.text)
    self.assertEqual(result.structured["content_digest"], self.digest)
    self.assertTrue(result.structured["skill_dir"].endswith("systematic-debugging"))
~~~

- [ ] **Step 3: Verify failure**

Run: PYTHONPATH=src python3 -m unittest tests.test_audit tests.test_codex_adapter tests.test_mcp_server -v

Expected: FAIL because runtime integration modules do not exist.

- [ ] **Step 4: Implement JSONL audit and Hook**

Audit explicit dataclass fields only: UTC time, session, cwd, Profile IDs, Catalog digest, event, Skill ID, revision, digest, result, error code, duration. Hook reads one JSON object from stdin and emits exactly one Codex Hook JSON object to stdout; diagnostics go to stderr.

- [ ] **Step 5: Implement official-SDK MCP tools**

Use FastMCP, derive project cwd from server configuration, call Core Search/Load, return enriched Markdown text and structured runtime metadata, and audit success/failure. Keep protocol stdout free of logs.

- [ ] **Step 6: Install and verify dependencies**

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
~~~

Expected: install succeeds, hwskill --version is 0.1.0, and tests pass. If dependency download is sandbox-blocked, request approval for the pip installation.

- [ ] **Step 7: Commit**

~~~bash
git add src/hwskill tests/test_audit.py tests/test_codex_adapter.py tests/test_mcp_server.py
git commit -m "feat: expose observable Codex catalog loading"
~~~

### Task 7: Demo, Docker, Live Eval, and Final Evidence

**Files:**

- Create: examples/codex-demo/order_pricing.py
- Create: examples/codex-demo/tests/test_order_pricing.py
- Create: examples/codex-demo/.hwskills/profile.yaml
- Create: examples/codex-demo/.hwskills/lock.yaml
- Create: examples/codex-demo/README.md
- Create: tests/test_demo_integration.py
- Create: docker/Dockerfile
- Create: docker/entrypoint.sh
- Create: scripts/run_docker_smoke.sh
- Create: scripts/run_codex_live_eval.sh
- Create: README.md
- Modify: this plan to check completed steps

**Interfaces:**

- Consumes installed hwskill, Registry, Codex CLI, and optional process-scoped CODEX_API_KEY.
- Produces offline smoke evidence and optional artifacts/codex.jsonl, hwskill-audit.jsonl, demo.diff, and test-output.txt.

- [ ] **Step 1: Write failing Demo test**

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

- [ ] **Step 2: Verify failure**

Run: .venv/bin/python -m unittest tests.test_demo_integration -v

Expected: FAIL because Demo does not exist.

- [ ] **Step 3: Create intentional boundary bug and binding**

Implement calculate_total(subtotal: Decimal, discount_threshold: Decimal, discount_rate: Decimal) -> Decimal with intentional subtotal > discount_threshold while the test expects inclusive behavior. Include passing below/above tests and exactly one failing equality test. Bind codex-demo and generate its lock through the CLI.

- [ ] **Step 4: Build offline Docker smoke**

Use a pinned Python 3.10 slim image, install this package and Codex CLI 0.147.x, copy Registry to /opt/hwskills and Demo to /workspace/demo, and create a non-root HOME. Assert no .agents/skills. Run Registry validation, resolve, Hook JSON, MCP Search/Load, audit assertions, and the known initial Demo failure without credentials.

- [ ] **Step 5: Build optional Live Eval**

Pass CODEX_API_KEY only to codex exec. Use --json --ephemeral --sandbox workspace-write. Assert JSONL contains hwskill_search and hwskill_load; audit contains locked digest; final tests pass. In a separate unbound project assert Search is empty and Load is denied.

- [ ] **Step 6: Run host verification**

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

Expected: tests/validations pass, resolved Catalog has exactly three candidates, and Load begins with x-hwskill-runtime frontmatter.

- [ ] **Step 7: Run optional environment checks**

Run bash scripts/run_docker_smoke.sh only when Docker is available. Run bash scripts/run_codex_live_eval.sh only when CODEX_API_KEY was explicitly supplied. Record unavailable checks as NOT RUN with the exact non-secret reason.

- [ ] **Step 8: Document usage and evidence**

README covers maintainer flow (import, validate, build), project flow (setup, profile bind, doctor), Agent flow (Hook, Search, Load), output rules, dynamic frontmatter, Docker, credential boundary, and commands actually run.

- [ ] **Step 9: Complete spec coverage review and commit**

Confirm archive, 18 snapshots, two Sources, three Profiles, full Catalog, CLI, dynamic Load, Hook/MCP, audit, Demo, Docker, negative boundary test, safety, and environment limitations each have a file and test.

~~~bash
git add examples docker scripts tests/test_demo_integration.py README.md \
  docs/superpowers/plans/2026-08-27-hwskill-minimal-registry.md
git commit -m "test: add isolated Codex skill-loading demo"
~~~

