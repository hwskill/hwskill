# hwskill User/Project Scope CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the README-defined user/project scope CLI, plural Profile resolution, Registry-wide Skill listing and safe batch export, user-level host integrations, and isolated Docker verification.

**Architecture:** Introduce one `ScopeTarget` path contract and one `HostSpec` metadata table, then make Profile, host configuration, Doctor, Info, adapters, and MCP consume those shared contracts. Effective Profile resolution is project-over-user with no merge; host configurations may coexist across scopes, while user adapters suppress themselves when a valid project integration owns the current project.

**Tech Stack:** Python 3.11+, `argparse`, dataclasses, PyYAML, JSON/TOML text management, `unittest`, POSIX shell, Docker, FastMCP.

**Spec:** `docs/superpowers/specs/2026-08-29-user-project-scope-cli-design.md`

## Global Constraints

- Profile input is a comma-separated plural set; trim whitespace, reject empty members, and de-duplicate in first-seen order.
- `profile set --empty` is mutually exclusive with the Profile CSV and creates an explicit empty set.
- A project Profile file completely overrides the user Profile; only an absent project file falls back to user; an explicit empty project set disables all Skills.
- User config defaults: `${XDG_CONFIG_HOME:-~/.config}/hwskill`; user state defaults: `${XDG_STATE_HOME:-~/.local/state}/hwskill`.
- Host paths honor `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and `XDG_CONFIG_HOME` exactly as specified in the design.
- User host configuration must never pin the setup-time project or cwd.
- `claude-code` is the canonical hwskill host ID; `claude_code` is an input alias; `claude` is the executable.
- Verified host versions are Claude Code `2.1.141`, Codex `0.147.0`, and OpenCode `1.14.48`; other versions produce `WARN`, not an incompatibility error.
- Skill selectors accept canonical IDs and unique exact Names; ID wins over Name; ambiguous Names fail with candidate IDs.
- Skill export copies all runtime snapshot content except root `skill.yaml`, refuses changed destinations, has no `--force`, and completes all conflict preflight before any destination write.
- Preserve current managed-entry ownership checks and never overwrite or delete externally modified configuration.
- Runtime Docker verification runs as a non-root user with a fresh HOME and `--network none`.
- Existing staged README changes belong to the user; preserve them and edit only the lines required by the implemented interface.

---

## File Responsibility Map

- `src/hwskill/scopes.py`: scope enum/data model, XDG and host path resolution, project selection.
- `src/hwskill/hosts.py`: canonical host IDs, aliases, executable names, verified versions.
- `src/hwskill/profiles.py`: Profile definition listing, CSV parsing, scoped storage, lock writing, effective resolution.
- `src/hwskill/skill_export.py`: Registry Skill selector resolution, export preflight, staging, digest check, final placement.
- `src/hwskill/configuration.py`: Codex scoped setup/current/unsetup and shared setup result.
- `src/hwskill/json_configuration.py`: Claude Code and OpenCode scoped configuration and ownership.
- `src/hwskill/catalog.py` and adapters: effective-catalog rendering and user-adapter suppression.
- `src/hwskill/doctor.py`: scoped common and host checks.
- `src/hwskill/info.py`: installation plus user/project/effective status aggregation.
- `src/hwskill/cli.py`: parser and presentation only; business rules remain in focused modules.
- `install.sh`: bootstrap argument parsing and install-path precedence.
- `docker/entrypoint.sh`: offline project and user-scope smoke assertions.

---

### Task 1: Scope and Host Metadata Contracts

**Files:**
- Create: `src/hwskill/scopes.py`
- Create: `src/hwskill/hosts.py`
- Modify: `src/hwskill/models.py`
- Create: `tests/test_scopes.py`
- Modify: `tests/test_paths.py`

**Interfaces:**
- Produces: `ScopeTarget`, `user_scope()`, `project_scope()`, `profile_path()`, `lock_path()`, `setup_state_path()`, `HostSpec`, `canonical_host()`, `HOST_SPECS`, `CommandRunner`, `run_command()`.
- Consumes: existing `find_project(start: Path) -> Path` from `src/hwskill/projects.py`.

- [ ] **Step 1: Write failing scope and host metadata tests**

```python
class ScopeTargetTest(unittest.TestCase):
    def test_user_scope_honors_xdg_roots(self):
        with patch.dict(os.environ, {
            "HOME": "/home/demo",
            "XDG_CONFIG_HOME": "/cfg",
            "XDG_STATE_HOME": "/state",
        }, clear=True):
            target = user_scope()
        self.assertEqual(profile_path(target), Path("/cfg/hwskill/profile.yaml"))
        self.assertEqual(lock_path(target), Path("/cfg/hwskill/lock.yaml"))
        self.assertEqual(
            setup_state_path(target, "claude-code"),
            Path("/state/hwskill/setup-claude-code.json"),
        )

    def test_bare_project_uses_git_root(self):
        project = self.base / "repo"
        (project / ".git").mkdir(parents=True)
        nested = project / "a/b"
        nested.mkdir(parents=True)
        self.assertEqual(project_scope(None, nested).project_root, project.resolve())

    def test_host_alias_normalizes_without_changing_executable(self):
        self.assertEqual(canonical_host("claude_code"), "claude-code")
        self.assertEqual(HOST_SPECS["claude-code"].executable, "claude")
        self.assertEqual(HOST_SPECS["codex"].verified_version, "0.147.0")
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_scopes tests.test_paths -v`

Expected: FAIL because `hwskill.scopes` and `hwskill.hosts` do not exist.

- [ ] **Step 3: Implement the minimal contracts**

```python
# src/hwskill/scopes.py
@dataclass(frozen=True)
class ScopeTarget:
    kind: Literal["user", "project"]
    project_root: Path | None
    config_root: Path
    state_root: Path

def user_scope() -> ScopeTarget:
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "hwskill"
    state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / "hwskill"
    return ScopeTarget("user", None, config, state)

def project_scope(value: str | Path | None, cwd: Path | None = None) -> ScopeTarget:
    project = Path(value).expanduser().resolve() if value else find_project(cwd or Path.cwd())
    root = project / ".hwskills"
    return ScopeTarget("project", project, root, root / "state")

def profile_path(target: ScopeTarget) -> Path:
    return target.config_root / "profile.yaml"

def lock_path(target: ScopeTarget) -> Path:
    return target.config_root / "lock.yaml"

def setup_state_path(target: ScopeTarget, host: str) -> Path:
    return target.state_root / f"setup-{canonical_host(host)}.json"

# src/hwskill/hosts.py
@dataclass(frozen=True)
class HostSpec:
    host_id: str
    executable: str
    verified_version: str

HOST_SPECS = {
    "codex": HostSpec("codex", "codex", "0.147.0"),
    "claude-code": HostSpec("claude-code", "claude", "2.1.141"),
    "opencode": HostSpec("opencode", "opencode", "1.14.48"),
}

def canonical_host(value: str) -> str:
    host = value.replace("_", "-")
    if host not in HOST_SPECS:
        raise ValueError(f"unsupported host: {value}")
    return host

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=10, check=False)
```

Extend `EffectiveCatalog` with `effective_scope: str | None` and `profile_source: Path | None`, providing defaults so existing direct construction remains source-compatible.

- [ ] **Step 4: Run focused and existing path tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_scopes tests.test_paths tests.test_runtime -v`

Expected: PASS.

- [ ] **Step 5: Commit the contracts**

```bash
git add src/hwskill/scopes.py src/hwskill/hosts.py src/hwskill/models.py tests/test_scopes.py tests/test_paths.py
git commit -m "feat: add scope and host metadata contracts"
```

---

### Task 2: Scoped Profile Storage and Effective Resolution

**Files:**
- Modify: `src/hwskill/profiles.py`
- Create: `tests/test_profiles.py`
- Modify: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `ScopeTarget`, `profile_path()`, `lock_path()` from Task 1; `EffectiveCatalog` from `models.py`.
- Produces: `ProfileDefinition`, `parse_csv()`, `list_profiles()`, `read_profile_ids()`, `set_profiles()`, `unset_profiles()`, and extended `resolve_profiles()`.

- [ ] **Step 1: Write failing CSV, set/show/unset, and precedence tests**

```python
def test_parse_csv_trims_deduplicates_and_rejects_empty_members(self):
    self.assertEqual(parse_csv("superpowers, codex-demo,superpowers", "profile"),
                     ("superpowers", "codex-demo"))
    with self.assertRaisesRegex(ProfileError, "empty profile selector"):
        parse_csv("superpowers,,codex-demo", "profile")

def test_project_profiles_override_user_and_absence_falls_back(self):
    set_profiles(self.user, ROOT, ("personal-baseline", "superpowers"))
    fallback = resolve_profiles(self.project, ROOT, user_target=self.user)
    self.assertEqual(fallback.effective_scope, "user")
    set_profiles(self.project_target, ROOT, ("codex-demo",))
    overridden = resolve_profiles(self.project, ROOT, user_target=self.user)
    self.assertEqual(overridden.profile_ids, ("codex-demo",))
    self.assertEqual(overridden.effective_scope, "project")

def test_explicit_empty_project_does_not_fall_back_and_unset_restores_fallback(self):
    set_profiles(self.user, ROOT, ("personal-baseline",))
    set_profiles(self.project_target, ROOT, ())
    self.assertEqual(resolve_profiles(self.project, ROOT, user_target=self.user).profile_ids, ())
    unset_profiles(self.project_target)
    self.assertEqual(resolve_profiles(self.project, ROOT, user_target=self.user).profile_ids,
                     ("personal-baseline",))
```

Add a failure-closed test that writes a mismatched Lock and expects `ProfileError("lock does not match")`.

- [ ] **Step 2: Run Profile tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_profiles tests.test_runtime -v`

Expected: FAIL because the scoped APIs and fallback fields are missing.

- [ ] **Step 3: Implement scoped Profile operations**

```python
@dataclass(frozen=True)
class ProfileDefinition:
    profile_id: str
    description: str
    skill_ids: tuple[str, ...]

def parse_csv(value: str, label: str) -> tuple[str, ...]:
    members = [member.strip() for member in value.split(",")]
    if not members or any(not member for member in members):
        raise ProfileError(f"empty {label} selector")
    return tuple(dict.fromkeys(members))

def list_profiles(registry_root: Path) -> tuple[ProfileDefinition, ...]:
    definitions = []
    for path in sorted((registry_root / "profiles").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        definitions.append(ProfileDefinition(
            profile_id=str(data["id"]),
            description=str(data.get("description", "")),
            skill_ids=tuple(str(item) for item in data.get("skills", ())),
        ))
    return tuple(definitions)
```

Implement the remaining public signatures exactly as `read_profile_ids(target: ScopeTarget) -> tuple[str, ...] | None`, `set_profiles(target: ScopeTarget, registry_root: Path, profile_ids: Sequence[str]) -> EffectiveCatalog`, `unset_profiles(target: ScopeTarget) -> bool`, and `resolve_profiles(project: Path, registry_root: Path, verify_lock: bool = True, *, user_target: ScopeTarget | None = None) -> EffectiveCatalog`. Write Profile and Lock to validated sibling temporary files, then `Path.replace()` them. Treat a missing Profile as “not configured” and an existing `profiles: []` as explicit empty. Keep `bind_profile()` and `unbind_profile()` as wrappers around project `read/set` for one compatibility release.

- [ ] **Step 4: Run Profile, runtime, search, and load regressions**

Run: `PYTHONPATH=src python3 -m unittest tests.test_profiles tests.test_runtime tests.test_demo_integration -v`

Expected: PASS.

- [ ] **Step 5: Commit scoped Profile resolution**

```bash
git add src/hwskill/profiles.py tests/test_profiles.py tests/test_runtime.py
git commit -m "feat: add scoped profile resolution"
```

---

### Task 3: Profile CLI Commands and Scope Parsing

**Files:**
- Modify: `src/hwskill/cli.py`
- Modify: `tests/test_cli_help.py`
- Modify: `tests/test_cli_runtime.py`

**Interfaces:**
- Consumes: Profile and Scope APIs from Tasks 1–2.
- Produces: `_add_scope_arguments()`, `_scope_target()`, and CLI commands `profile list/set/show/unset`.

- [ ] **Step 1: Write failing parser and command-output tests**

```python
def run_cli(self, *argv: str) -> str:
    stdout = StringIO()
    with redirect_stdout(stdout):
        self.assertEqual(main(list(argv)), 0)
    return stdout.getvalue()

def test_profile_set_requires_exactly_one_scope(self):
    with self.assertRaises(SystemExit):
        main(["profile", "set", "personal-baseline"])
    with self.assertRaises(SystemExit):
        main(["profile", "set", "personal-baseline", "--user", "--project"])

def test_profile_set_empty_disables_user_fallback(self):
    self.run_cli("profile", "set", "--empty", "--project", str(self.project),
                 "--repo-root", str(ROOT), "--yes")
    shown = self.run_cli("profile", "show", "--project", str(self.project),
                         "--repo-root", str(ROOT))
    self.assertIn("EFFECTIVE_SCOPE\tproject", shown)
    self.assertIn("PROFILES\tnone", shown)

def test_profile_list_lists_registry_definitions_not_project_bindings(self):
    output = self.run_cli("profile", "list", "--repo-root", str(ROOT))
    self.assertIn("personal-baseline", output)
    self.assertIn("superpowers", output)

def test_profile_show_project_reports_user_fallback(self):
    output = self.run_cli("profile", "show", "--project", str(self.project),
                          "--repo-root", str(ROOT))
    self.assertIn("EFFECTIVE_SCOPE\tuser", output)
```

Also assert help renders `--project [PROJECT]`, `--user`, CSV examples, and the `unset` command.

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_cli_help tests.test_cli_runtime -v`

Expected: FAIL because the new subcommands and optional `--project` value are absent.

- [ ] **Step 3: Implement scope-aware Profile CLI wiring**

```python
def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", action="store_true")
    group.add_argument("--project", nargs="?", const="")

def _scope_target(args: argparse.Namespace, *, write: bool) -> ScopeTarget:
    if args.user:
        return user_scope()
    return project_scope(args.project or None, Path.cwd())
```

Make the Profile CSV positional optional only when `--empty` is present; reject both or neither. Use `()` for `--empty`, otherwise use `parse_csv()` for `set`. Print explicit/effective fields for `show`, require confirmation for `set/unset`, and emit deprecation warnings from `bind/unbind` to stderr.

- [ ] **Step 4: Run CLI and Profile regression tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_cli_help tests.test_cli_runtime tests.test_profiles tests.test_runtime -v`

Expected: PASS.

- [ ] **Step 5: Commit Profile CLI**

```bash
git add src/hwskill/cli.py tests/test_cli_help.py tests/test_cli_runtime.py
git commit -m "feat: add profile set show and unset commands"
```

---

### Task 4: Registry Skill Listing, Selector Resolution, and Safe Export

**Files:**
- Create: `src/hwskill/skill_export.py`
- Modify: `src/hwskill/cli.py`
- Create: `tests/test_skill_export.py`
- Modify: `tests/test_cli_help.py`
- Modify: `tests/test_cli_runtime.py`

**Interfaces:**
- Consumes: `validate_registry()`, `content_digest()`, `parse_csv()`, and `list_profiles()`.
- Produces: `ExportResult`, `resolve_skill_selectors()`, `skills_for_profiles()`, `export_skills()` and CLI commands `skill list/dump/dump-profile`.

- [ ] **Step 1: Write failing selector and preflight tests**

```python
def record(self, skill_id: str) -> SkillRecord:
    return SkillRecord(
        skill_id=skill_id, name=skill_id.rsplit("/", 1)[-1], description="fixture",
        layer="l1", source_id="fixture", revision="1", license="MIT",
        content_digest="sha256:fixture", path=self.base / skill_id.replace("/", "-"),
    )

def select_two(self) -> tuple[SkillRecord, SkillRecord]:
    by_id = {item.skill_id: item for item in validate_registry(ROOT)}
    return by_id["local/chinese-thinking"], by_id["superpowers/systematic-debugging"]

def test_selectors_accept_ids_unique_names_mixed_and_multiple(self):
    records = validate_registry(ROOT)
    selected = resolve_skill_selectors(
        "local/chinese-thinking,systematic-debugging,chinese-thinking", records
    )
    self.assertEqual([item.skill_id for item in selected], [
        "local/chinese-thinking", "superpowers/systematic-debugging"
    ])

def test_ambiguous_name_lists_candidate_ids(self):
    records = (self.record("one/shared"), self.record("two/shared"))
    with self.assertRaisesRegex(ExportError, "one/shared.*two/shared"):
        resolve_skill_selectors("shared", records)

def test_conflict_preflight_writes_nothing(self):
    changed = self.destination / "systematic-debugging"
    changed.mkdir(parents=True)
    (changed / "SKILL.md").write_text("changed", encoding="utf-8")
    with self.assertRaisesRegex(ExportError, "refusing to overwrite"):
        export_skills(self.select_two(), self.destination)
    self.assertFalse((self.destination / "chinese-thinking").exists())
```

Add tests for identical `UNCHANGED`, complete `scripts/` copying, exclusion of root `skill.yaml`, digest verification, target Name collision, and multi-Profile union.

- [ ] **Step 2: Run Skill export tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_skill_export tests.test_cli_runtime -v`

Expected: FAIL because `skill_export.py` and the three CLI commands do not exist.

- [ ] **Step 3: Implement selection and export preflight**

```python
@dataclass(frozen=True)
class ExportResult:
    skill_id: str
    target: Path
    status: Literal["CREATED", "UNCHANGED"]

class ExportError(ValueError):
    pass

def resolve_skill_selectors(
    value: str, records: Sequence[SkillRecord]
) -> tuple[SkillRecord, ...]:
    by_id = {record.skill_id: record for record in records}
    by_name: dict[str, list[SkillRecord]] = {}
    for record in records:
        by_name.setdefault(record.name, []).append(record)
    selected: dict[str, SkillRecord] = {}
    for selector in parse_csv(value, "skill"):
        record = by_id.get(selector)
        if record is None:
            matches = by_name.get(selector, [])
            if not matches:
                raise ExportError(f"unknown skill selector: {selector}")
            if len(matches) > 1:
                candidates = ", ".join(item.skill_id for item in matches)
                raise ExportError(f"ambiguous skill name: {selector}; matches: {candidates}")
            record = matches[0]
        selected.setdefault(record.skill_id, record)
    return tuple(selected.values())
```

Implement `skills_for_profiles(value: str, registry_root: Path, records: Sequence[SkillRecord]) -> tuple[SkillRecord, ...]` by parsing Profile IDs, validating each definition, and de-duplicating its Skill IDs in Profile/input order. Implement `export_skills(records: Sequence[SkillRecord], destination: Path) -> tuple[ExportResult, ...]` with a complete preflight over source digests, final Names, and existing destinations before creating staging directories. Stage each new Skill under the destination parent with `tempfile.mkdtemp(prefix=".hwskill-export-", dir=destination)`. Iterate `record.path.iterdir()`, skip only a root child named `skill.yaml`, use `shutil.copytree()` for directories and `shutil.copy2()` for files, verify `content_digest(staging)`, then rename. Clean only the staging paths created by that invocation on failure.

- [ ] **Step 4: Wire `skill list/dump/dump-profile` and JSON output**

Use `validate_registry()` once per command. `skill list` prints `ID\tNAME\tLAYER\tREVISION`; dump commands print `SKILL\tTARGET\tSTATUS`. JSON serializes all Registry metadata or export results without converting paths implicitly.

- [ ] **Step 5: Run focused and Registry regression tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_skill_export tests.test_cli_help tests.test_cli_runtime tests.test_registry -v`

Expected: PASS.

- [ ] **Step 6: Commit Skill listing and export**

```bash
git add src/hwskill/skill_export.py src/hwskill/cli.py tests/test_skill_export.py tests/test_cli_help.py tests/test_cli_runtime.py
git commit -m "feat: add skill listing and batch export"
```

---

### Task 5: Scoped Codex Configuration

**Files:**
- Modify: `src/hwskill/configuration.py`
- Modify: `src/hwskill/cli.py`
- Modify: `src/hwskill/doctor.py`
- Modify: `tests/test_host_configuration.py`
- Modify: `tests/test_host_doctor.py`
- Modify: `tests/test_codex_runtime.py`

**Interfaces:**
- Consumes: `ScopeTarget`, `setup_state_path()`, `HostSpec`.
- Produces: `setup_codex(target, registry_root, audit_path)`, `codex_setup_is_current(target)`, `unsetup_codex(target)`.

- [ ] **Step 1: Write failing user/project Codex tests**

```python
def test_user_codex_setup_writes_codex_home_without_pinning_project(self):
    target = user_scope()
    result = setup_codex(target, ROOT)
    text = result.config_path.read_text(encoding="utf-8")
    self.assertEqual(result.config_path, Path(os.environ["CODEX_HOME"]) / "config.toml")
    self.assertNotIn("--project", text)
    self.assertIn("--scope user", text)

def test_project_codex_setup_keeps_explicit_project_runtime(self):
    target = project_scope(self.project)
    setup_codex(target, ROOT)
    text = (self.project / ".codex/config.toml").read_text(encoding="utf-8")
    self.assertIn(str(self.project.resolve()), text)
    self.assertIn("--scope project", text)
```

Retain idempotency, unmanaged-block refusal, external modification refusal, and unsetup preservation tests for both scopes.

- [ ] **Step 2: Run Codex configuration tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_configuration tests.test_codex_runtime -v`

Expected: FAIL because configuration APIs still accept a project Path.

- [ ] **Step 3: Refactor Codex managed blocks around `ScopeTarget`**

```python
def _codex_config_path(target: ScopeTarget) -> Path:
    if target.kind == "user":
        return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    assert target.project_root is not None
    return target.project_root / ".codex/config.toml"

def _runtime_scope_args(target: ScopeTarget) -> list[str]:
    if target.kind == "user":
        return ["--scope", "user"]
    assert target.project_root is not None
    return ["--scope", "project", "--project", str(target.project_root)]
```

Expose `setup_codex(target: ScopeTarget, registry_root: Path, audit_path: Path | None = None) -> SetupResult`, `codex_setup_is_current(target: ScopeTarget) -> bool`, and `unsetup_codex(target: ScopeTarget) -> SetupResult`. Resolve user config from `CODEX_HOME` or `~/.codex`; resolve project config from `target.project_root`. Store absolute managed config paths in user state and project-relative paths in project state. Generate user MCP args without `--project`; retain explicit project args for project setup.

Mechanically update existing project-only callers in CLI, Doctor, and tests to pass `project_scope(existing_project_path)` so this commit does not leave the old Path signature in live code. Task 8 will replace the temporary project-only CLI wrapping with the final required Scope parser.

- [ ] **Step 4: Run Codex and ownership regressions**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_configuration tests.test_host_doctor tests.test_codex_runtime -v`

Expected: PASS for Codex cases; unchanged Claude/OpenCode cases remain passing through compatibility wrappers until Task 6.

- [ ] **Step 5: Commit scoped Codex setup**

```bash
git add src/hwskill/configuration.py src/hwskill/cli.py src/hwskill/doctor.py tests/test_host_configuration.py tests/test_host_doctor.py tests/test_codex_runtime.py
git commit -m "feat: support user scoped codex setup"
```

---

### Task 6: Scoped Claude Code and OpenCode Configuration

**Files:**
- Modify: `src/hwskill/json_configuration.py`
- Modify: `tests/test_host_configuration.py`

**Interfaces:**
- Consumes: `ScopeTarget`, state paths, canonical host metadata, `CommandRunner`, `run_command()`, and existing JSON ownership helpers.
- Produces: scoped `setup/unsetup_claude_code()` and `setup/unsetup_opencode()`; `claude_setup_is_current()` and `opencode_setup_is_current()`.

- [ ] **Step 1: Write failing Claude user-scope tests with a fake command runner**

```python
class FakeRunner:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        call = tuple(argv)
        self.calls.append(call)
        if call[:4] == ("claude", "mcp", "get", "hwskill"):
            return subprocess.CompletedProcess(call, 1, "", "not found")
        return subprocess.CompletedProcess(call, 0, "", "")

def test_user_claude_setup_uses_settings_file_and_official_mcp_cli(self):
    runner = FakeRunner()
    result = setup_claude_code(user_scope(), ROOT, command_runner=runner)
    self.assertEqual(result.config_path, Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json")
    add = next(call for call in runner.calls if call[:4] == ("claude", "mcp", "add-json", "hwskill"))
    self.assertIn("--scope", add)
    self.assertIn("user", add)
    self.assertNotIn("--project", json.dumps(add))
```

Test `claude mcp get hwskill` inspection, unmanaged same-name refusal, external Hook modification refusal, and `claude mcp remove hwskill --scope user` during Unsetup.

- [ ] **Step 2: Write failing OpenCode user-scope tests**

```python
def test_user_opencode_setup_uses_xdg_global_config_and_dynamic_plugin(self):
    result = setup_opencode(user_scope(), ROOT)
    root = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode"
    self.assertEqual(result.config_path, root / "opencode.json")
    plugin = (root / "plugins/hwskill.js").read_text(encoding="utf-8")
    self.assertIn("process.cwd()", plugin)
    self.assertIn("--scope", plugin)
    self.assertNotIn(str(self.project), plugin)
```

- [ ] **Step 3: Run focused configuration tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_configuration -v`

Expected: FAIL because Claude/OpenCode APIs are project-only and Claude edits `.mcp.json` directly.

- [ ] **Step 4: Implement scoped Claude and OpenCode configuration**

```python
def _claude_settings_path(target: ScopeTarget) -> Path:
    if target.kind == "user":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"
    assert target.project_root is not None
    return target.project_root / ".claude/settings.json"

def _opencode_root(target: ScopeTarget) -> Path:
    if target.kind == "user":
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return xdg / "opencode"
    assert target.project_root is not None
    return target.project_root
```

Expose scoped signatures `setup_claude_code(target, registry_root, audit_path=None, command_runner=run_command)`, `unsetup_claude_code(target, command_runner=run_command)`, `claude_setup_is_current(target, command_runner=run_command) -> bool`, `setup_opencode(target, registry_root, audit_path=None)`, `unsetup_opencode(target)`, and `opencode_setup_is_current(target) -> bool`. For Claude user scope, write only the owned Hook entry directly and use the CLI for MCP. Persist the exact expected MCP JSON and Hook in hwskill state. For project scope, retain `.mcp.json`. For OpenCode user scope, use the XDG global config and plugin directory; project scope retains existing files.

- [ ] **Step 5: Run all host configuration tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_configuration -v`

Expected: PASS.

- [ ] **Step 6: Commit scoped JSON host setup**

```bash
git add src/hwskill/json_configuration.py tests/test_host_configuration.py
git commit -m "feat: support user scoped claude and opencode setup"
```

---

### Task 7: Dynamic Runtime Resolution and Duplicate Injection Suppression

**Files:**
- Modify: `src/hwskill/catalog.py`
- Modify: `src/hwskill/codex_adapter.py`
- Modify: `src/hwskill/claude_code_adapter.py`
- Modify: `src/hwskill/opencode_adapter.py`
- Modify: `src/hwskill/mcp_server.py`
- Modify: `src/hwskill/configuration.py`
- Modify: `src/hwskill/json_configuration.py`
- Modify: `tests/test_host_adapters.py`
- Modify: `tests/test_codex_runtime.py`
- Modify: `tests/test_runtime.py`

**Interfaces:**
- Consumes: effective Profile resolver and `*_setup_is_current(target)` ownership checks.
- Produces: `should_suppress_user_adapter(host, project)`, scoped adapter entry points, cwd-based MCP runtime.

- [ ] **Step 1: Write failing dynamic fallback and suppression tests**

```python
def test_user_adapter_uses_event_cwd_and_user_profile_fallback(self):
    set_profiles(self.user, ROOT, ("personal-baseline",))
    output = run_codex_session_start(ROOT, self.audit,
        json.dumps({"cwd": str(self.project), "session_id": "s"}),
        scope="user", user_target=self.user)
    self.assertIn("local/chinese-thinking", output)

def test_user_adapter_is_empty_when_valid_project_setup_exists(self):
    setup_codex(project_scope(self.project), ROOT)
    output = run_codex_session_start(ROOT, self.audit,
        json.dumps({"cwd": str(self.project), "session_id": "s"}),
        scope="user", user_target=self.user)
    self.assertEqual(output, "")
```

Add equivalent Claude and OpenCode cases, plus a broken project state case that does not suppress and is later diagnosed as incomplete.

- [ ] **Step 2: Run adapter/runtime tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_adapters tests.test_codex_runtime tests.test_runtime -v`

Expected: FAIL because adapters do not receive scope and never suppress.

- [ ] **Step 3: Implement scoped rendering and suppression**

```python
def should_suppress_user_adapter(host: str, project: Path) -> bool:
    target = project_scope(project)
    checks = {
        "codex": codex_setup_is_current,
        "claude-code": claude_setup_is_current,
        "opencode": opencode_setup_is_current,
    }
    return checks[canonical_host(host)](target)

def render_effective_catalog(
    project: Path, registry_root: Path, audit: AuditWriter,
    session_id: str | None = None, *, user_target: ScopeTarget | None = None,
) -> str:
    catalog = resolve_profiles(project.resolve(), registry_root.resolve(),
                               user_target=user_target)
    lines = [
        "hwskill Effective Skill Catalog",
        f"catalog_digest: {catalog.catalog_digest}",
        "Skills:",
        *(f"- {item.skill_id}: {item.description}" for item in catalog.skills),
    ]
    audit.write(AuditEvent(
        event="catalog", result="ok", session_id=session_id,
        cwd=str(project.resolve()), profile_ids=catalog.profile_ids,
        catalog_digest=catalog.catalog_digest,
    ))
    return "\n".join(lines)
```

Add `scope` to adapter runner signatures. Return an empty string before rendering or auditing a Catalog when a valid project integration owns the host. Make OpenCode plugin append only `context.trim()` when non-empty. Keep MCP project selection at process start from explicit `--project` or cwd default.

- [ ] **Step 4: Run adapter, MCP, audit, search, and load regressions**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_adapters tests.test_codex_runtime tests.test_runtime tests.test_host_eval_events -v`

Expected: PASS and exactly one Catalog audit event in overlap fixtures.

- [ ] **Step 5: Commit dynamic runtime behavior**

```bash
git add src/hwskill/catalog.py src/hwskill/codex_adapter.py src/hwskill/claude_code_adapter.py src/hwskill/opencode_adapter.py src/hwskill/mcp_server.py src/hwskill/configuration.py src/hwskill/json_configuration.py tests/test_host_adapters.py tests/test_codex_runtime.py tests/test_runtime.py
git commit -m "feat: resolve user profiles at host runtime"
```

---

### Task 8: Scoped Doctor, Info, Version Checks, and Host CLI Wiring

**Files:**
- Modify: `src/hwskill/doctor.py`
- Modify: `src/hwskill/info.py`
- Modify: `src/hwskill/cli.py`
- Modify: `tests/test_host_doctor.py`
- Modify: `tests/test_info.py`
- Modify: `tests/test_cli_help.py`
- Modify: `tests/test_cli_runtime.py`

**Interfaces:**
- Consumes: scoped configuration current checks, `HOST_SPECS`, Profile effective resolution, Scope parser.
- Produces: `run_doctor(host, target, registry_root)`, scoped integration summaries, Setup/Doctor/Unsetup CLI.

- [ ] **Step 1: Write failing scoped Doctor tests**

```python
class DoctorRunner:
    def __init__(self, versions: dict[str, str]):
        self.versions = versions
        self.claude_mcp_configured = False

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        call = tuple(argv)
        if call[:4] == ("claude", "mcp", "get", "hwskill"):
            code = 0 if self.claude_mcp_configured else 1
            return subprocess.CompletedProcess(call, code, "hwskill" if code == 0 else "", "")
        if call[:4] == ("claude", "mcp", "add-json", "hwskill"):
            self.claude_mcp_configured = True
            return subprocess.CompletedProcess(call, 0, "", "")
        executable = argv[0]
        return subprocess.CompletedProcess(tuple(argv), 0, self.versions[executable] + "\n", "")

def test_user_doctor_reports_user_config_and_verified_claude_version(self):
    runner = DoctorRunner({"claude": "2.1.141 (Claude Code)"})
    setup_claude_code(self.user, ROOT, command_runner=runner)
    checks = {item.name: item for item in run_doctor(
        "claude-code", self.user, ROOT, command_runner=runner)}
    self.assertEqual(checks["claude-hook-config"].status, "PASS")
    self.assertEqual(checks["claude-mcp-config"].status, "PASS")
    self.assertEqual(checks["claude-version"].status, "PASS")

def test_unverified_codex_version_is_warn_not_error(self):
    runner = DoctorRunner({"codex": "codex-cli 0.148.0"})
    checks = {item.name: item for item in run_doctor(
        "codex", self.user, ROOT, command_runner=runner)}
    self.assertEqual(checks["codex-version"].status, "WARN")
```

Add project fallback source and overlapping integration checks.

- [ ] **Step 2: Write failing Info shape and CLI scope tests**

```python
def test_info_reports_user_project_and_effective_profiles(self):
    info = self.collect()
    profiles = info["integration"]["profiles"]
    self.assertEqual(set(profiles), {"user", "project", "effective", "effective_scope"})

def test_setup_doctor_unsetup_require_scope_and_accept_bare_project(self):
    for command in ("setup", "doctor", "unsetup"):
        with self.assertRaises(SystemExit):
            main([command, "codex"])
```

- [ ] **Step 3: Run Doctor, Info, and CLI tests to verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_doctor tests.test_info tests.test_cli_help tests.test_cli_runtime -v`

Expected: FAIL because these components remain project-shaped.

- [ ] **Step 4: Implement scoped checks and summaries**

```python
def _version_check(host: str, command_runner: CommandRunner) -> CheckResult:
    spec = HOST_SPECS[canonical_host(host)]
    completed = command_runner((spec.executable, "--version"))
    installed = completed.stdout.strip().splitlines()[0] if completed.returncode == 0 else "unknown"
    verified = spec.verified_version in installed
    return CheckResult(
        f"{spec.host_id}-version", "PASS" if verified else "WARN",
        f"installed: {installed}; verified: {spec.verified_version}",
    )

def run_doctor(
    host: str, target: ScopeTarget, registry_root: Path,
    *, command_runner: CommandRunner = run_command,
) -> list[CheckResult]:
    return [
        *run_common_checks(target, registry_root),
        *run_host_checks(host, target, command_runner=command_runner),
        _version_check(host, command_runner),
    ]
```

Use `HostSpec.executable` and exact `--version` output normalization for all hosts. Have Claude user Doctor query `claude mcp get hwskill`. Refactor Info to report separate user/project/effective sections and effective integration source.

- [ ] **Step 5: Wire scoped Setup/Doctor/Unsetup CLI**

Use the same required mutually-exclusive Scope arguments from Task 3. Normalize `claude_code` to `claude-code` before dispatch. Keep `--repo-root`, `--audit-path`, `--json`, and `--yes` behavior.

- [ ] **Step 6: Run all host, Info, and CLI tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_host_configuration tests.test_host_doctor tests.test_info tests.test_cli_help tests.test_cli_runtime -v`

Expected: PASS.

- [ ] **Step 7: Commit diagnostics and CLI wiring**

```bash
git add src/hwskill/doctor.py src/hwskill/info.py src/hwskill/cli.py tests/test_host_doctor.py tests/test_info.py tests/test_cli_help.py tests/test_cli_runtime.py
git commit -m "feat: add scoped host diagnostics"
```

---

### Task 9: Installer `--install-path`

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_user_install.py`

**Interfaces:**
- Consumes: existing bootstrap/local-checkout split and environment variables.
- Produces: strict `--install-path=<path>`, `--install-path <path>`, and `--help` parsing.

- [ ] **Step 1: Write failing installer argument tests**

```python
def run_local(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    checkout = self.local_checkout()
    environment = os.environ | {
        "HOME": str(self.base / "home"),
        "HWSKILL_BIN_DIR": str(self.base / "bin"),
        "HWSKILL_SHELL_CONFIG": str(self.base / "bashrc"),
        "PYTHON": str(self.fake_python()),
    }
    return subprocess.run([str(checkout / "install.sh"), *args], env=environment,
                          text=True, capture_output=True, check=False)

def run_bootstrap(self, args: Sequence[str], **overrides: str) -> subprocess.CompletedProcess[str]:
    source = self.make_git_remote_fixture()
    environment = os.environ | {
        "HOME": str(self.base / "home"),
        "HWSKILL_REPOSITORY_URL": str(source),
        "HWSKILL_BIN_DIR": str(self.base / "bin"),
        "HWSKILL_SHELL_CONFIG": str(self.base / "bashrc"),
        "PYTHON": str(self.fake_python()),
        **overrides,
    }
    return subprocess.run(["sh", "-s", "--", *args], input=(ROOT / "install.sh").read_text(),
                          env=environment, text=True, capture_output=True, check=False)

def make_git_remote_fixture(self) -> Path:
    source = self.base / "remote"
    shutil.copytree(self.local_checkout(), source)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Test",
        "-c", "user.email=test@example.com", "commit", "-qm", "fixture",
    ], check=True)
    return source

def test_bootstrap_install_path_argument_overrides_environment(self):
    completed = self.run_bootstrap(
        ["--install-path", str(self.base / "argument-target")],
        HWSKILL_HOME=str(self.base / "environment-target"),
    )
    self.assertEqual(completed.returncode, 0, completed.stderr)
    self.assertTrue((self.base / "argument-target/.git").is_dir())

def test_unknown_argument_and_local_checkout_mismatch_fail_without_installing(self):
    unknown = self.run_local(["--unknown"])
    self.assertNotEqual(unknown.returncode, 0)
    mismatch = self.run_local(["--install-path", str(self.base / "elsewhere")])
    self.assertIn("does not move an existing checkout", mismatch.stderr)
```

Also cover equals syntax, missing value, help, environment fallback, and default fallback.

- [ ] **Step 2: Run installer tests and verify RED**

Run: `PYTHONPATH=src python3 -m unittest tests.test_user_install -v`

Expected: FAIL because installer arguments are ignored.

- [ ] **Step 3: Implement POSIX shell argument parsing**

```sh
install_path=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-path=*) install_path=${1#*=} ;;
    --install-path) shift; [ "$#" -gt 0 ] || usage_error "missing --install-path value"; install_path=$1 ;;
    --help) usage; exit 0 ;;
    *) usage_error "unknown argument: $1" ;;
  esac
  shift
done
```

Resolve `install_home=${install_path:-${HWSKILL_HOME:-"$HOME/.local/share/hwskill"}}`. Preserve the chosen path through bootstrap `exec`. In local mode, compare canonical paths and reject a different target.

- [ ] **Step 4: Run installer tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_user_install -v`

Expected: PASS.

- [ ] **Step 5: Commit installer arguments**

```bash
git add install.sh tests/test_user_install.py
git commit -m "feat: add install path argument"
```

---

### Task 10: Docker User-Scope Isolation and README Alignment

**Files:**
- Modify: `docker/entrypoint.sh`
- Modify: `scripts/run_docker_smoke.sh`
- Modify: `README.md`
- Modify: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: all preceding commands and verified host binaries already pinned by `docker/Dockerfile`.
- Produces: one offline smoke covering fresh user configuration, fallback, override, duplicate suppression, MCP, list, and export.

- [ ] **Step 1: Add a failing CLI documentation smoke test**

```python
def assert_help_succeeds(self, argv: Sequence[str]) -> None:
    with redirect_stdout(StringIO()), self.assertRaises(SystemExit) as caught:
        main(list(argv))
    self.assertEqual(caught.exception.code, 0)

def test_readme_target_commands_exist_in_help(self):
    for argv in (
        ["profile", "list", "--help"], ["profile", "set", "--help"],
        ["profile", "show", "--help"], ["profile", "unset", "--help"],
        ["skill", "list", "--help"], ["skill", "dump", "--help"],
        ["skill", "dump-profile", "--help"], ["setup", "--help"],
    ):
        self.assert_help_succeeds(argv)
```

- [ ] **Step 2: Extend the Docker entrypoint with fresh-user fixtures**

Add shell assertions that:

```sh
test ! -e "$HOME/.codex/config.toml"
test ! -e "$HOME/.claude/settings.json"
test ! -e "$XDG_CONFIG_HOME/opencode/opencode.json"

hwskill profile set personal-baseline,superpowers --user --repo-root "$HWSKILL_REGISTRY_ROOT" --yes
hwskill setup codex --user --repo-root "$HWSKILL_REGISTRY_ROOT" --yes
hwskill setup claude-code --user --repo-root "$HWSKILL_REGISTRY_ROOT" --yes
hwskill setup opencode --user --repo-root "$HWSKILL_REGISTRY_ROOT" --yes

hwskill doctor codex --user --repo-root "$HWSKILL_REGISTRY_ROOT"
hwskill doctor claude-code --user --repo-root "$HWSKILL_REGISTRY_ROOT"
hwskill doctor opencode --user --repo-root "$HWSKILL_REGISTRY_ROOT"
claude mcp get hwskill
codex mcp get hwskill
```

Create `/tmp/user-fallback` and `/tmp/project-override`; bind only the latter to `codex-demo`. Assert `profile show/resolve` sources, invoke adapters from both directories, count exactly one Catalog event under overlapping setup, execute an actual STDIO MCP Search/Load, run `skill list`, batch `skill dump`, and `dump-profile`, and verify a copied helper script.

- [ ] **Step 3: Update README to the implemented interface**

Preserve the user's current staged prose while correcting executable and completed details:

```markdown
hwskill profile set personal-baseline,superpowers --user
hwskill skill dump local/chinese-thinking,systematic-debugging ~/.agents/skills
hwskill skill list
cd <your-project>
claude
```

Rename “建议版本” to “已验证版本” and fill `2.1.141`, `0.147.0`, and `1.14.48`. Document unique Name resolution and project-over-user fallback in one concise paragraph.

- [ ] **Step 4: Run unit and Registry verification**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests -v`

Expected: PASS with zero failures and zero errors.

Run: `PYTHONPATH=src python3 -m hwskill registry validate --repo-root .`

Expected: exit 0 and all registered Skill IDs listed.

Run: `PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check`

Expected: exit 0 with no catalog drift.

- [ ] **Step 5: Run Docker offline smoke**

Run: `bash scripts/run_docker_smoke.sh`

Expected: image build succeeds; runtime uses `--network none`; final output is `Docker offline smoke PASS`.

- [ ] **Step 6: Inspect the final diff and commit documentation/verification**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only intended implementation, test, Docker, and README files remain.

```bash
git add README.md docker/entrypoint.sh scripts/run_docker_smoke.sh tests/test_cli_smoke.py
git commit -m "test: verify user scoped integrations offline"
```

---

### Task 11: Full Regression and Design Conformance Gate

**Files:**
- Modify only files required to correct failures found by the commands below.

**Interfaces:**
- Consumes: the complete implementation.
- Produces: verified repository state matching the approved spec.

- [ ] **Step 1: Run the complete Python suite from a clean temporary user environment**

Run:

```bash
test_home=$(mktemp -d /tmp/hwskill-plan-home-XXXXXX)
HOME="$test_home" XDG_CONFIG_HOME="$test_home/.config" XDG_STATE_HOME="$test_home/.local/state" \
  PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected: all tests PASS; no file is created under the real HOME.

- [ ] **Step 2: Run Registry invariants**

```bash
PYTHONPATH=src python3 -m hwskill registry validate --repo-root .
PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check
```

Expected: both commands exit 0.

- [ ] **Step 3: Run the offline Docker integration gate**

Run: `bash scripts/run_docker_smoke.sh`

Expected: `Docker offline smoke PASS` under `--network none`.

- [ ] **Step 4: Check every approved CLI form**

```bash
PYTHONPATH=src python3 -m hwskill profile list --repo-root .
PYTHONPATH=src python3 -m hwskill skill list --repo-root .
PYTHONPATH=src python3 -m hwskill setup --help
PYTHONPATH=src python3 -m hwskill doctor --help
PYTHONPATH=src python3 -m hwskill skill dump --help
```

Expected: commands exit 0 and help shows required Scope/selector forms.

- [ ] **Step 5: Review requirements and repository cleanliness**

Run:

```bash
git diff --check
git status --short
git log --oneline -12
```

Expected: no whitespace errors; user-authored changes are preserved; task commits are small and ordered as above.

- [ ] **Step 6: Commit any conformance-only correction**

If Steps 1–5 required a correction, first run `git diff --name-only`, compare every reported path with the correction just made, and stage only those verified correction paths one at a time with `git add -- path`. Confirm the staged set with `git diff --cached --name-only`, then run `git commit -m "fix: align scoped CLI with approved design"`. If no correction was required, do not create an empty commit.
