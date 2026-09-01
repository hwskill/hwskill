# Skill Source Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace local-path Skill importing with reproducible manual/upstream source management and complete Skill lifecycle commands.

**Architecture:** Parse schema-2 source manifests into immutable domain models, resolve Git refs through an injected Git client, and construct candidate repository trees before applying any multi-file change. Source-level services own upstream discovery and atomic updates; Skill-level services own manual content and identity changes; the existing runtime Catalog continues consuming normalized `SkillRecord` values.

**Tech Stack:** Python 3.10+, standard library `dataclasses`, `pathlib`, `subprocess`, `tempfile`, `shutil`, PyYAML 6.x, `unittest`, Git CLI.

**Spec:** `docs/superpowers/specs/2026-09-01-skill-source-maintenance-and-testing-design.md`

## Global Constraints

- No command or manifest may synchronize Skill content from a local filesystem path.
- Manual Skill payloads are edited only under `skills-src/`; their runtime revision string is `manual` and their exact version is the content digest.
- Upstream sources update as one unit and always store a full 40-character resolved commit SHA.
- `refs/heads/*`, `refs/tags/*`, and full commit SHA tracks are accepted; bare ambiguous refs are rejected.
- A fixed tag moving to another commit is an error, not an update.
- `source update --all` is a single repository transaction.
- Preserve unrelated dirty work and abort on overlapping target changes.
- Do not run upstream hooks, setup scripts, or payload code while fetching.
- Existing `info`, Profile, host integration, adapter, MCP, Skill read/export, `registry validate`, and `registry build` commands remain compatible.
- Remove `registry import` and all `kind: local` parsing.
- Use TDD for every task and commit only the files named by that task.

---

### Task 1: Schema-2 source and Skill provenance models

**Files:**
- Create: `src/hwskill/source_manifest.py`
- Modify: `src/hwskill/models.py`
- Create: `tests/test_source_manifest.py`
- Modify: `tests/test_registry.py`

**Interfaces:**
- Produces: `load_source_manifest(path: Path) -> UpstreamSource`
- Produces: `write_source_manifest(path: Path, source: UpstreamSource) -> None`
- Produces: `load_all_sources(repo_root: Path) -> tuple[UpstreamSource, ...]`
- Produces immutable `IgnoredSkill`, `ResolvedSourceSkill`, `SourceDefaults`, `UpstreamConfig`, and `UpstreamSource` dataclasses.
- Changes `SkillRecord.source_id` to `str | None` and adds `source_kind: Literal["manual", "upstream"]`.

- [ ] **Step 1: Write failing source manifest parsing tests**

```python
def test_source_manifest_separates_track_from_resolved_state(self):
    source = load_source_manifest(self.write_source({
        "schema_version": 2,
        "source_id": "superpowers",
        "kind": "upstream",
        "upstream": {
            "repository": "https://github.com/obra/superpowers.git",
            "track": "refs/heads/main",
            "skills_path": "skills",
            "ignore": [{"path": "unused", "reason": "not selected"}],
        },
        "defaults": {"namespace": "superpowers", "layer": "l1", "license": "MIT"},
        "resolved": {
            "revision": "a" * 40,
            "skills": [{
                "path": "brainstorming",
                "id": "superpowers/brainstorming",
                "layer": "l1",
                "content_digest": "sha256:" + "b" * 64,
            }],
        },
    }))
    self.assertEqual(source.upstream.track, "refs/heads/main")
    self.assertEqual(source.resolved_revision, "a" * 40)
    self.assertEqual(source.skills[0].skill_id, "superpowers/brainstorming")
```

Also test duplicate source IDs, local repository paths, bare refs, unsafe `skills_path`, unsafe ignore paths, resolved/ignore overlap, duplicate Skill IDs, non-full revisions, and round-trip deterministic YAML.

- [ ] **Step 2: Run the parser test and verify the expected failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_source_manifest -v`.

Expected: FAIL because `hwskill.source_manifest` does not exist.

- [ ] **Step 3: Implement the dataclasses and strict parser**

```python
@dataclass(frozen=True)
class ResolvedSourceSkill:
    path: str
    skill_id: str
    layer: str
    content_digest: str

@dataclass(frozen=True)
class UpstreamSource:
    source_id: str
    upstream: UpstreamConfig
    defaults: SourceDefaults
    resolved_revision: str
    skills: tuple[ResolvedSourceSkill, ...]
```

Use explicit field validation; do not silently coerce missing collections or unknown source kinds. Accept remote HTTPS, SSH, `ssh://`, and `git://` Git URLs, but reject absolute paths, `file://`, `.` and `..`-relative repository values.

- [ ] **Step 4: Update `SkillRecord` without breaking runtime consumers**

```python
@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    description: str
    layer: str
    source_kind: Literal["manual", "upstream"]
    source_id: str | None
    revision: str
    license: str
    content_digest: str
    path: Path
```

Update existing test fixtures to pass `source_kind="upstream"`; manual fixtures use `source_id=None` and `revision="manual"`.

- [ ] **Step 5: Run focused and existing Registry tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_source_manifest tests.test_registry tests.test_profiles tests.test_runtime -v`

Expected: PASS; if the repository has already moved tests, use the corresponding `tests.core.*` module names.

- [ ] **Step 6: Commit the schema models**

```bash
git add src/hwskill/models.py src/hwskill/source_manifest.py tests/test_source_manifest.py tests/test_registry.py tests/test_profiles.py tests/test_runtime.py
git commit -m "feat: model manual and upstream skill sources"
```

---

### Task 2: Safe Git reference resolution and Skill discovery

**Files:**
- Create: `src/hwskill/git_source.py`
- Create: `tests/test_git_source.py`

**Interfaces:**
- Defines: `CommandRunner = Callable[..., subprocess.CompletedProcess[str]]`.
- Produces: `GitSourceClient.list_remote(repository: str) -> RemoteRefs`
- Produces: `GitSourceClient.resolve(repository: str, track: str) -> ResolvedTrack`
- Produces: `GitSourceClient.materialize(repository: str, track: str, destination: Path) -> ResolvedTrack`
- Produces: `discover_skills(checkout: Path, skills_path: str) -> tuple[DiscoveredSkill, ...]`

- [ ] **Step 1: Write failing Git client tests with local bare remote fixtures**

```python
def test_resolve_branch_returns_full_commit_without_checkout(self):
    resolved = self.client.resolve(str(self.remote), "refs/heads/main")
    self.assertEqual(resolved.kind, "branch")
    self.assertRegex(resolved.commit, r"^[0-9a-f]{40}$")

def test_fixed_tag_move_is_reported(self):
    with self.assertRaisesRegex(GitSourceError, "tag moved"):
        verify_existing_tag("refs/tags/v1", "a" * 40, "b" * 40)
```

Create the remote using test subprocess calls to `git init --bare`, `git init`, `git commit`, and `git push`; never depend on network in unit tests.

- [ ] **Step 2: Run the Git source tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_git_source -v`

Expected: FAIL because `hwskill.git_source` does not exist.

- [ ] **Step 3: Implement remote listing and exact resolution**

```python
@dataclass(frozen=True)
class RemoteRefs:
    default_branch: str
    refs: Mapping[str, str]

@dataclass(frozen=True)
class ResolvedTrack:
    track: str
    kind: Literal["branch", "tag", "commit"]
    commit: str

class GitSourceClient:
    def __init__(self, runner: CommandRunner = subprocess.run):
        self._runner = runner

    def resolve(self, repository: str, track: str) -> ResolvedTrack:
        # refs/heads: read exact ref
        # refs/tags: prefer peeled <ref>^{} object
        # 40-hex SHA: materialize and verify commit^{commit}
```

Pass a minimal environment that disables prompts and hooks: `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, and command-local `core.hooksPath` pointing to an empty directory. Preserve proxy and credential-helper behavior required for authenticated remotes.

- [ ] **Step 4: Implement materialization and discovery**

Materialize into a caller-owned empty temporary directory with `git init`, an explicit `git fetch --depth=1 <repository> <track>`, and `git checkout --detach FETCH_HEAD`. Verify `HEAD^{commit}` equals the resolved SHA before reading files.

```python
@dataclass(frozen=True)
class DiscoveredSkill:
    path: str
    name: str
    description: str
```

Discovery scans direct children of `skills_path` that contain `SKILL.md`, validates frontmatter, and returns sorted relative POSIX paths.

- [ ] **Step 5: Test annotated tags, full SHA, missing refs, unsafe trees, and discovery ordering**

Run: `PYTHONPATH=src python3 -m unittest tests.test_git_source -v`

Expected: PASS with no network access.

- [ ] **Step 6: Commit Git source support**

```bash
git add src/hwskill/git_source.py tests/test_git_source.py
git commit -m "feat: resolve and inspect upstream git sources"
```

---

### Task 3: Candidate repository transactions

**Files:**
- Create: `src/hwskill/maintenance_transaction.py`
- Create: `tests/test_maintenance_transaction.py`

**Interfaces:**
- Produces: `RepositoryTransaction(repo_root: Path)` context manager.
- Produces: `candidate_root: Path`, `changed_paths() -> tuple[Path, ...]`, `apply() -> None`, and `discard() -> None`.
- Produces: `MaintenanceSummary` and `MaintenancePlan` shared by all maintenance services.
- Consumes a validation callback `Callable[[Path], None]` supplied by source/Skill services.

- [ ] **Step 1: Write rollback and overlap failure tests**

```python
def test_apply_rolls_back_every_target_when_second_replace_fails(self):
    tx = RepositoryTransaction(self.repo, replace=self.fail_second_replace)
    tx.write_text(Path("sources/a.yaml"), "new source\n")
    tx.write_text(Path("registry/catalog.json"), "new catalog\n")
    with self.assertRaises(OSError):
        tx.apply()
    self.assertEqual((self.repo / "sources/a.yaml").read_text(), "old source\n")
    self.assertEqual((self.repo / "registry/catalog.json").read_text(), "old catalog\n")
```

Also test deletion rollback, unrelated dirty files, target preimage drift between planning and apply, candidate cleanup, and `--all`-style multi-source writes.

- [ ] **Step 2: Run the transaction tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_maintenance_transaction -v`

Expected: FAIL because the transaction module does not exist.

- [ ] **Step 3: Implement explicit target tracking and preimage digests**

Copy only managed roots (`sources`, `skills-src`, `profiles`, `registry`, and `tests`) into a temporary candidate root. Record each target's original kind and SHA-256 bytes digest. Before apply, re-read every target and abort if any preimage changed.

Define the shared plan contract in this module:

```python
@dataclass(frozen=True)
class MaintenanceSummary:
    operation: str
    source_ids: tuple[str, ...] = ()
    added_skill_ids: tuple[str, ...] = ()
    updated_skill_ids: tuple[str, ...] = ()
    removed_skill_ids: tuple[str, ...] = ()
    manualized_skill_ids: tuple[str, ...] = ()
    affected_profile_ids: tuple[str, ...] = ()

@dataclass
class MaintenancePlan:
    transaction: RepositoryTransaction
    summary: MaintenanceSummary

    def apply(self) -> None:
        self.transaction.apply()
```

- [ ] **Step 4: Implement backup-based apply and rollback**

Use a temporary backup directory outside target paths. Apply sorted target changes; on any exception, restore every previously changed target in reverse order. Never stage Git or mutate `.git` except the later pending-verification feature.

- [ ] **Step 5: Run transaction and full core tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_maintenance_transaction -v`

Expected: PASS.

- [ ] **Step 6: Commit transaction support**

```bash
git add src/hwskill/maintenance_transaction.py tests/test_maintenance_transaction.py
git commit -m "feat: apply repository maintenance transactions safely"
```

---

### Task 4: Source inspection, ignore, add, and update services

**Files:**
- Create: `src/hwskill/source_maintenance.py`
- Create: `tests/test_source_maintenance.py`

**Interfaces:**
- Produces: `inspect_source(repo_root, source, git_client) -> SourceInspection`
- Produces: `plan_add_source(repo_root, request: SourceAddRequest, selection: SourceSelection, git_client: GitSourceClient) -> MaintenancePlan`
- Produces: `plan_update_sources(repo_root, source_ids, policies, git_client) -> MaintenancePlan`
- Produces: `plan_ignore_change(repo_root, source_id, path, operation) -> MaintenancePlan`
- Produces: `plan_delete_source(repo_root, source_id, skill_policy, remove_from_profiles) -> MaintenancePlan`
- `MaintenancePlan.apply()` delegates to `RepositoryTransaction` after validation.

- [ ] **Step 1: Write failing inventory classification tests**

```python
def test_inspection_partitions_discovered_paths(self):
    inspection = inspect_source(self.repo, self.source, self.git)
    self.assertEqual([item.path for item in inspection.added], ["new-skill"])
    self.assertEqual([item.path for item in inspection.updated], ["changed-skill"])
    self.assertEqual(inspection.removed, ("deleted-skill",))
    self.assertEqual(inspection.ignored, ("ignored-skill",))
```

Also test future ignore, fixed tag movement, identical content under a new revision, duplicate inferred IDs, and source default layer materialization.

- [ ] **Step 2: Run source maintenance tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_source_maintenance -v`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement inspection and deterministic mapping**

Infer IDs as `<defaults.namespace>/<frontmatter.name>`. Preserve existing resolved IDs and layers. New selected entries receive `defaults.layer`; ignored entries never receive a Skill directory.

```python
@dataclass(frozen=True)
class SourceAddRequest:
    source_id: str
    repository: str
    track: str
    skills_path: str
    defaults: SourceDefaults

@dataclass(frozen=True)
class SourceSelection:
    included_paths: tuple[str, ...]
    ignored_paths: tuple[str, ...]

@dataclass(frozen=True)
class SourceInspection:
    old_revision: str | None
    new_revision: str
    added: tuple[DiscoveredSkill, ...]
    updated: tuple[DiscoveredSkill, ...]
    removed: tuple[str, ...]
    ignored: tuple[str, ...]
```

- [ ] **Step 4: Implement add/update policies**

```python
@dataclass(frozen=True)
class UpdatePolicies:
    on_added: Literal["include", "ignore", "fail"]
    on_removed: Literal["remove", "manualize", "fail"]
```

`plan_update_sources` resolves and stages every source before applying any. `manualize` removal retains the current payload as manual and adds its path to ignore. Removing an upstream Skill adds the path to ignore.

- [ ] **Step 5: Implement ignore add/remove**

Allow future paths. Reject resolved paths. On remove, inspect the current upstream tree; if the inferred ID collides with a manual Skill, raise an error containing the exact `hwskill source adopt` command.

- [ ] **Step 6: Implement whole-source deletion policies**

`skill_policy="delete"` removes all resolved payloads and rejects Profile references unless `remove_from_profiles=True`. `skill_policy="manualize"` rewrites every payload to manual provenance, preserves IDs/content/layers, then removes the source manifest; no ignore survives a deleted source.

- [ ] **Step 7: Run focused tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_source_maintenance tests.test_maintenance_transaction tests.test_git_source -v`

Expected: PASS.

- [ ] **Step 8: Commit source maintenance services**

```bash
git add src/hwskill/source_maintenance.py tests/test_source_maintenance.py
git commit -m "feat: plan atomic upstream source updates"
```

---

### Task 5: Manual Skill lifecycle, identity changes, manualize, and adopt

**Files:**
- Create: `src/hwskill/skill_maintenance.py`
- Create: `tests/test_skill_maintenance.py`
- Modify: `src/hwskill/profiles.py`

**Interfaces:**
- Produces: `plan_create_manual(repo_root: Path, skill_id: str, layer: str, description: str, license_name: str) -> MaintenancePlan`.
- Produces: `plan_update_manual(repo_root: Path, skill_id: str) -> MaintenancePlan`.
- Produces: `plan_move_skill(repo_root: Path, skill_id: str, layer: str) -> MaintenancePlan`.
- Produces: `plan_rename_skill(repo_root: Path, old_id: str, new_id: str) -> MaintenancePlan`.
- Produces: `plan_delete_skill(repo_root: Path, skill_id: str, remove_from_profiles: bool) -> MaintenancePlan`.
- Produces: `plan_manualize_skill(repo_root: Path, skill_id: str) -> MaintenancePlan`.
- Produces: `plan_adopt_skill(repo_root: Path, source_id: str, skill_id: str, upstream_path: str, replace: bool, git_client: GitSourceClient) -> MaintenancePlan`.
- Produces: `find_skill_references(repo_root: Path, skill_id: str) -> SkillReferences`.

`SkillReferences` contains sorted `profile_ids: tuple[str, ...]` and `test_paths: tuple[Path, ...]`.

- [ ] **Step 1: Write failing manual create/update tests**

```python
def test_update_manual_recomputes_governance_without_local_source(self):
    skill = self.make_manual_skill("team/review", layer="l2")
    (skill / "SKILL.md").write_text(SKILL_TEXT + "\nChanged.\n")
    plan = plan_update_manual(self.repo, "team/review")
    plan.apply()
    governance = yaml.safe_load((skill / "skill.yaml").read_text())
    self.assertEqual(governance["source"], {"kind": "manual"})
    self.assertEqual(governance["content_digest"], content_digest(skill))
```

Test create refusal on duplicate ID, upstream update refusal, layer move with stable ID, rename migration, and path containment.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_skill_maintenance -v`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement manual create/update/move/rename**

Create valid `SKILL.md` frontmatter directly in the target directory. Rename updates repository Profile definitions, source resolved entries when applicable, and test target IDs if test manifests already exist. Never rewrite user/project locks outside this repository.

- [ ] **Step 4: Implement reference-aware delete**

Default deletion raises `SkillReferencedError` listing Profile and test references. `remove_from_profiles=True` edits repository Profile definitions; test references must be removed explicitly rather than silently deleted.

- [ ] **Step 5: Implement manualize and adopt**

Manualize preserves payload bytes, writes manual provenance, removes the resolved entry, and adds ignore in one transaction. Adopt removes ignore; identical content changes only provenance, while differing content requires `replace=True` and stages the upstream payload.

- [ ] **Step 6: Run lifecycle and Profile tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_skill_maintenance tests.test_profiles -v`

Expected: PASS.

- [ ] **Step 7: Commit Skill lifecycle services**

```bash
git add src/hwskill/skill_maintenance.py src/hwskill/profiles.py tests/test_skill_maintenance.py tests/test_profiles.py
git commit -m "feat: manage manual and upstream skill lifecycles"
```

---

### Task 6: Maintenance CLI and readable output

**Files:**
- Create: `src/hwskill/maintenance_cli.py`
- Create: `src/hwskill/console_output.py`
- Modify: `src/hwskill/cli.py`
- Create: `tests/test_maintenance_cli.py`
- Modify: `tests/test_cli_help.py`

**Interfaces:**
- Produces: `register_maintenance_commands(commands) -> None`
- Produces: `run_maintenance_command(args, repo_root, stdin, stdout, stderr) -> int | None`
- Produces: `format_maintenance_plan(plan: MaintenancePlan, color: bool) -> str`

- [ ] **Step 1: Write failing CLI help and wizard tests**

```python
def test_source_add_wizard_defaults_to_repo_name_default_branch_and_all_skills(self):
    answers = iter([REPOSITORY, "", "", "", "", "y"])
    with patch("builtins.input", side_effect=lambda _: next(answers)):
        code = main(["source", "add", "--repo-root", str(self.repo)])
    self.assertEqual(code, 0)
    source = load_source_manifest(self.repo / "sources/superpowers.yaml")
    self.assertEqual(source.source_id, "superpowers")
    self.assertEqual(source.upstream.track, "refs/heads/main")
    self.assertEqual(len(source.skills), 2)
```

Also test duplicate source-name retry, `source update --all`, noninteractive missing policies, future ignore, adopt collision guidance, no ANSI when stdout is not a TTY, and stable JSON.

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_maintenance_cli tests.test_cli_help -v`

Expected: FAIL because the commands are absent.

- [ ] **Step 3: Register source and Skill maintenance commands**

Keep parser construction out of `main()` by moving only the new command registrations and dispatch into `maintenance_cli.py`. Remove `registry import` parser/dispatch/imports. Preserve every existing command listed in the spec compatibility table.

Map domain outcomes to the specified exit codes: 0 success/current, 1 update available or validation failure, 2 usage/manifest error, 3 blocked external environment, and 4 overlapping local conflict or apply failure.

- [ ] **Step 4: Implement interactive selection and confirmations**

Use numeric lists with blank meaning the displayed default; source add defaults to all discovered Skills. `--yes` skips only the final confirmation and never supplies missing destructive policies.

- [ ] **Step 5: Implement structured and human output**

Render `Source`, `Skills`, `Profiles`, and `Tests` sections with aligned columns and counts. JSON contains explicit `status`, revisions, deltas, affected Profiles, and affected tests.

- [ ] **Step 6: Run maintenance CLI and existing CLI tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_maintenance_cli tests.test_cli_help tests.test_cli_runtime -v`

Expected: PASS and existing help still lists `info`, `registry`, `profile`, `skill`, `setup`, `unsetup`, `doctor`, `adapter`, and `serve-mcp`.

- [ ] **Step 7: Commit the maintenance CLI**

```bash
git add src/hwskill/maintenance_cli.py src/hwskill/console_output.py src/hwskill/cli.py tests/test_maintenance_cli.py tests/test_cli_help.py tests/test_cli_runtime.py
git commit -m "feat: expose source and skill maintenance commands"
```

---

### Task 7: Migrate the repository and remove local import

**Files:**
- Delete: `src/hwskill/importer.py`
- Delete: `tests/test_importer.py`
- Delete: `sources/local-agents-skills.yaml`
- Modify: `sources/superpowers.yaml`
- Modify: `skills-src/**/skill.yaml`
- Modify: `registry/catalog.json`
- Modify: `src/hwskill/registry.py`
- Modify: `src/hwskill/loader.py`
- Modify: `src/hwskill/profiles.py`
- Modify: `README.md`

**Interfaces:**
- `validate_registry` accepts manual and upstream provenance and emits schema-2 `SkillRecord` values.
- `build_catalog` emits Catalog schema 2 while preserving runtime `id`, `revision`, `content_digest`, and `path` fields.

- [ ] **Step 1: Write migration acceptance tests**

```python
def test_repository_has_no_local_source_manifests_or_absolute_roots(self):
    for path in (ROOT / "sources").glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("kind: local", text)
        self.assertNotRegex(text, r"(?m)^root:")

def test_manual_skills_have_manual_runtime_revision(self):
    records = {item.skill_id: item for item in validate_registry(ROOT)}
    self.assertEqual(records["local/chinese-thinking"].source_kind, "manual")
    self.assertEqual(records["local/chinese-thinking"].revision, "manual")
```

- [ ] **Step 2: Run migration tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_registry -v`

Expected: FAIL because local manifests and provenance remain.

- [ ] **Step 3: Convert the three local Skills to manual governance**

Rewrite only `skill.yaml`; preserve every payload byte. Recompute digests and verify the three directories still match their pre-migration payload digests.

- [ ] **Step 4: Convert Superpowers through the new source service**

In one candidate transaction, remove the legacy Superpowers manifest and existing Superpowers snapshot directories, then call `plan_add_source` for `refs/tags/v6.3.0` with the existing 14 paths selected. Let the Git client resolve and write the full SHA; add every other discovered direct Skill path to ignore. Before apply, compare the old and candidate payload digests for all 14 IDs and fail migration if any payload differs.

- [ ] **Step 5: Remove importer code and update Catalog/runtime compatibility**

Manual Catalog entries use `source_kind: manual`, `source_id: null`, and `revision: manual`. Upstream entries use the source ID and full SHA. Ensure loader/Profile locks still rely on content digest and accept the manual revision string.

- [ ] **Step 6: Update README maintainer commands**

Replace `registry import` examples with `source check`, `source update`, `skill update`, `integrity-check`, and separate test commands. State that no local-path sync is supported.

- [ ] **Step 7: Run full source-lifecycle verification**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m hwskill registry validate --repo-root .
PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check
rg -n 'kind: local|^root: /|registry import' sources README.md src tests
```

Expected: tests and Registry checks PASS; `rg` returns no matches.

- [ ] **Step 8: Commit the repository migration**

```bash
git add README.md src/hwskill tests sources skills-src registry/catalog.json
git commit -m "feat: migrate skills to manual and upstream maintenance"
```
