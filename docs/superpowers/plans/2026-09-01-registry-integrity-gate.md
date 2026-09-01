# Registry Integrity Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, offline `hwskill integrity-check` that proves source, Skill, Catalog, Profile, lock, and test-reference consistency without replacing existing Registry validation.

**Architecture:** Keep `registry validate` as the focused Skill/Catalog input validator, then compose it inside a new integrity engine that reports all repository-level issues rather than failing at the first one. The engine exposes structured issues to candidate maintenance transactions and a thin CLI formatter; behavior tests remain a separate subsystem.

**Tech Stack:** Python 3.10+, standard library, PyYAML 6.x, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-01-skill-source-maintenance-and-testing-design.md`

## Global Constraints

- `integrity-check` is offline, read-only, deterministic, and never runs behavior tests.
- `registry validate` remains public and is a strict subset reused by `integrity-check`.
- Report every independent issue in one run with stable codes and paths.
- A stale Catalog is an error with guidance to run `registry build`; integrity never repairs it.
- Manual/upstream ownership and source resolved/ignore invariants are mandatory.
- Profile and repository example locks must resolve against the current Catalog.
- Test manifests are validated when present; their execution belongs to `hwskill test`.
- Human output is grouped and readable; JSON is stable and exhaustive.

---

### Task 1: Structured integrity report and repository inventory

**Files:**
- Create: `src/hwskill/integrity.py`
- Create: `tests/test_integrity.py`

**Interfaces:**
- Produces: `IntegrityIssue(code: str, path: str, message: str)`.
- Produces: `IntegrityReport(issues: tuple[IntegrityIssue, ...], skill_count: int, source_count: int, profile_count: int)`.
- Produces: `check_integrity(repo_root: Path) -> IntegrityReport`.
- Produces: `require_integrity(repo_root: Path) -> IntegrityReport`, raising `IntegrityError` with the report on failure.

- [ ] **Step 1: Write failing aggregation tests**

```python
def test_integrity_collects_orphan_skill_and_stale_catalog_together(self):
    self.make_orphan_skill("skills-src/l1/team/orphan")
    (self.repo / "registry/catalog.json").write_text("{}\n")
    report = check_integrity(self.repo)
    self.assertEqual(
        {issue.code for issue in report.issues},
        {"orphan-skill", "catalog-stale"},
    )
```

Also test deterministic issue ordering by `(path, code, message)` and a clean report with counts.

- [ ] **Step 2: Run the test and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_integrity -v`

Expected: FAIL because `hwskill.integrity` does not exist.

- [ ] **Step 3: Implement inventory without early exit**

Inventory all `SKILL.md`, `skill.yaml`, source manifests, Profile definitions, repository `.hwskills/lock.yaml` files, Catalog entries, and `tests/{skills,profiles}/**/test.yaml` files. Convert exceptions from focused parsers into `IntegrityIssue` values and continue scanning unrelated paths.

- [ ] **Step 4: Implement deterministic report types**

```python
@dataclass(frozen=True, order=True)
class IntegrityIssue:
    path: str
    code: str
    message: str

@dataclass(frozen=True)
class IntegrityReport:
    issues: tuple[IntegrityIssue, ...]
    skill_count: int
    source_count: int
    profile_count: int

    @property
    def ok(self) -> bool:
        return not self.issues
```

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_integrity -v`

Expected: PASS.

- [ ] **Step 6: Commit the report engine**

```bash
git add src/hwskill/integrity.py tests/test_integrity.py
git commit -m "feat: inventory repository integrity issues"
```

---

### Task 2: Source, Skill, and Catalog three-way consistency

**Files:**
- Modify: `src/hwskill/integrity.py`
- Modify: `src/hwskill/registry.py`
- Modify: `tests/test_integrity.py`
- Modify: `tests/test_registry.py`

**Interfaces:**
- `validate_registry(repo_root)` continues to return `list[SkillRecord]` and validates each Skill's own governance.
- `check_integrity` adds source ownership and exact generated-Catalog comparisons.

- [ ] **Step 1: Write failing three-way mismatch tests**

```python
def test_upstream_skill_must_match_source_and_catalog(self):
    self.copy_clean_registry()
    source = self.read_source("superpowers")
    source["resolved"]["skills"][0]["content_digest"] = "sha256:" + "0" * 64
    self.write_source("superpowers", source)
    report = check_integrity(self.repo)
    self.assertIn("source-skill-digest-mismatch", {i.code for i in report.issues})
```

Cover manual Skill appearing in resolved, upstream Skill missing from resolved, one Skill in two sources, resolved/ignore overlap, path/ID/layer/revision mismatch, Catalog extra/missing entry, and Catalog stale bytes.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_integrity tests.test_registry -v`

Expected: FAIL on the new issue-code assertions.

- [ ] **Step 3: Extend focused Registry validation**

Validate schema-2 `source.kind` in every `skill.yaml`, manual `revision="manual"`, upstream source ID/full revision/path presence, payload digest, frontmatter name, and physical `skills-src/<layer>/<namespace>/<name>` path. Do not fetch upstream.

- [ ] **Step 4: Add cross-source indexes and exact comparisons**

Build indexes by Skill ID and `(source_id, upstream_path)`. Compare normalized `build_catalog(repo_root)` JSON bytes to `registry/catalog.json`; report `catalog-stale` once, plus precise entry mismatches when useful.

- [ ] **Step 5: Run Registry and integrity tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_registry tests.test_integrity -v`

Expected: PASS.

- [ ] **Step 6: Commit cross-consistency checks**

```bash
git add src/hwskill/integrity.py src/hwskill/registry.py tests/test_integrity.py tests/test_registry.py
git commit -m "feat: enforce source skill catalog consistency"
```

---

### Task 3: Profile, lock, and test-reference integrity

**Files:**
- Modify: `src/hwskill/integrity.py`
- Modify: `src/hwskill/profiles.py`
- Modify: `tests/test_integrity.py`
- Modify: `tests/test_profiles.py`

**Interfaces:**
- Produces: `validate_test_manifest_references(repo_root, known_skill_ids, known_profile_ids) -> tuple[IntegrityIssue, ...]`.
- Reuses Profile definitions and `_lock_data` semantics without mutating locks.

- [ ] **Step 1: Write failing reference tests**

```python
def test_profile_test_target_must_exist(self):
    path = self.repo / "tests/profiles/missing/test.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: 1\ntarget:\n  kind: profile\n  id: missing\ncases: []\n")
    report = check_integrity(self.repo)
    self.assertIn("unknown-test-profile", {i.code for i in report.issues})
```

Also test unknown Skill targets, duplicate test target collections, invalid repository example lock digest, missing Profile Skill, and rename leftovers in tests.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_integrity tests.test_profiles -v`

Expected: FAIL on the new reference assertions.

- [ ] **Step 3: Validate Profile definitions and repository locks**

For each repository-owned `.hwskills/profile.yaml` and lock pair, resolve the named Profiles against the current Registry and compare exact expected Skill IDs, revisions, digests, and Catalog digest. Do not inspect user-level configuration outside the repository.

- [ ] **Step 4: Validate test target headers without executing cases**

Read only `schema_version`, `target.kind`, and `target.id` so this plan does not depend on the full test runner parser. Require one collection per Skill/Profile target and reject targets absent from the Registry/Profile inventory.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_integrity tests.test_profiles -v`

Expected: PASS.

- [ ] **Step 6: Commit reference checks**

```bash
git add src/hwskill/integrity.py src/hwskill/profiles.py tests/test_integrity.py tests/test_profiles.py
git commit -m "feat: validate profile lock and test references"
```

---

### Task 4: Integrity CLI and maintenance transaction gate

**Files:**
- Create: `src/hwskill/integrity_cli.py`
- Modify: `src/hwskill/cli.py`
- Modify: `src/hwskill/maintenance_transaction.py`
- Create: `tests/test_integrity_cli.py`
- Modify: `tests/test_maintenance_transaction.py`

**Interfaces:**
- Produces top-level `hwskill integrity-check [--repo-root PATH] [--json]`.
- Adds `RepositoryTransaction.validate(callback: Callable[[Path], None]) -> None` before apply.

- [ ] **Step 1: Write failing CLI output tests**

```python
def test_integrity_json_reports_all_issues_and_nonzero_status(self):
    code, output = self.run_cli("integrity-check", "--repo-root", str(self.repo), "--json")
    payload = json.loads(output)
    self.assertEqual(code, 1)
    self.assertEqual(payload["status"], "failed")
    self.assertEqual([item["code"] for item in payload["issues"]], sorted(
        item["code"] for item in payload["issues"]
    ))
```

Also test readable grouped output, zero status on clean repository, and `registry validate` remaining available.

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_integrity_cli -v`

Expected: FAIL because the command is absent.

- [ ] **Step 3: Implement command registration and formatting**

Keep parser and dispatch in `integrity_cli.py`; `cli.py` only delegates. Human output starts with `Integrity check`, prints counts, then groups issues by top-level path. JSON serializes dataclasses with stable ordering.

- [ ] **Step 4: Gate every maintenance candidate**

Source/Skill maintenance services call `require_integrity(transaction.candidate_root)` after writing the candidate Catalog and before displaying/applying the plan. A failed candidate is discarded and leaves the real worktree untouched.

- [ ] **Step 5: Run CLI, transaction, and full unit tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_integrity_cli tests.test_integrity tests.test_maintenance_transaction -v
PYTHONPATH=src python3 -m hwskill integrity-check --repo-root .
```

Expected: focused tests PASS and the real repository reports a clean integrity result.

- [ ] **Step 6: Commit the integrity command**

```bash
git add src/hwskill/integrity_cli.py src/hwskill/cli.py src/hwskill/maintenance_transaction.py tests/test_integrity_cli.py tests/test_maintenance_transaction.py
git commit -m "feat: add repository integrity gate"
```

---

### Task 5: Documentation and final compatibility verification

**Files:**
- Modify: `README.md`
- Modify: `tests/test_cli_help.py`

**Interfaces:**
- Documents `registry validate` as the focused subset and `integrity-check` as the full repository gate.

- [ ] **Step 1: Add a failing README command-boundary assertion**

```python
def test_readme_distinguishes_registry_integrity_and_behavior_checks(self):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    self.assertIn("hwskill registry validate", text)
    self.assertIn("hwskill integrity-check", text)
    self.assertIn("hwskill test affected", text)
```

- [ ] **Step 2: Run help tests and verify the documentation assertion fails before README/help edits**

Run: `PYTHONPATH=src python3 -m unittest tests.test_cli_help -v`

Expected: FAIL because README does not yet document all three command boundaries.

- [ ] **Step 3: Document command boundaries and maintainer workflow**

Show `registry build --check`, `registry validate`, `integrity-check`, and `test affected` as separate commands. State that integrity is offline and does not fetch upstream or run tests.

- [ ] **Step 4: Run final plan verification**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m hwskill registry validate --repo-root .
PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check
PYTHONPATH=src python3 -m hwskill integrity-check --repo-root .
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md tests/test_cli_help.py
git commit -m "docs: explain registry and integrity validation"
```
