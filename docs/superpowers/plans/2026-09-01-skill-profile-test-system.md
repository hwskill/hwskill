# Skill and Profile Test System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide standardized core, Skill, and Profile test collections with command/Agent actions, structured post-check evidence, affected-test selection, model setup, and Docker execution.

**Architecture:** Parse each `test.yaml` into immutable collection/case/action models, execute cases in isolated workspaces, and persist a normalized context plus per-action artifacts for post-check. A host adapter layer launches configured Agent CLIs and normalizes their event streams; a Docker runner provides the standard environment while the local runner remains available for debugging.

**Tech Stack:** Python 3.10+, PyYAML 6.x, `unittest`, standard library `subprocess`/`tempfile`/`json`, Docker CLI, Codex CLI 0.147.x, Claude Code 2.1.x, OpenCode 1.14.x.

**Spec:** `docs/superpowers/specs/2026-09-01-skill-source-maintenance-and-testing-design.md`

## Global Constraints

- Test roots are exactly `tests/core`, `tests/skills`, and `tests/profiles`.
- `test.yaml` defines a target and cases; each case has optional prepare, non-empty steps, and required post-check.
- Action types are exactly `command` and `agent` in schema 1.
- `action.workdir > case.workdir > case workspace root`; every resolved path remains inside the isolated workspace.
- Command strings run under `/bin/bash -lc` in the standard Linux environment.
- Post-check is the final business verdict and receives standardized prior-action evidence.
- Do not record hidden reasoning, secrets, tokens, or unredacted credential files.
- `hwskill test` never invokes `integrity-check` implicitly.
- Required unavailable infrastructure is BLOCKED, never PASS or SKIPPED.
- Standard runs use Docker; local runs are debugging-only.
- Model and reasoning configuration is user-level; credentials remain in host stores or injected environment.

---

### Task 1: Move framework tests under `tests/core`

**Files:**
- Create: `tests/test_test_layout.py`
- Create: `tests/core/__init__.py`
- Move: every current `tests/test_*.py` that tests `src/hwskill` into `tests/core/`
- Modify: moved tests whose `ROOT` constant depends on parent depth
- Modify: `README.md`
- Modify: `scripts/run_docker_smoke.sh`
- Modify: `docker/entrypoint.sh`

**Interfaces:**
- Core suite command becomes `PYTHONPATH=src python3 -m unittest discover -s tests/core -t . -v`.
- `tests/skills` and `tests/profiles` are reserved for declarative test collections, not framework unit modules.

- [ ] **Step 1: Add a test-layout assertion before moving files**

```python
class TestLayoutTest(unittest.TestCase):
    def test_framework_tests_live_only_under_core(self):
        root = Path(__file__).parents[2]
        self.assertFalse(list((root / "tests").glob("test_*.py")))
        self.assertTrue(list((root / "tests/core").glob("test_*.py")))
```

- [ ] **Step 2: Run the assertion and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_test_layout -v`

Expected: FAIL because framework tests still live at `tests/test_*.py`.

- [ ] **Step 3: Move framework tests and fix repository roots**

Use `git mv tests/test_*.py tests/core/`. In moved modules, replace `Path(__file__).parents[1]` with `Path(__file__).parents[2]` when it means repository root. Keep `tests/__init__.py` and add `tests/core/__init__.py`.

- [ ] **Step 4: Update all core suite invocations**

Update README, Docker entrypoints, scripts, and plan-produced commands to use `discover -s tests/core -t .`. Do not rewrite commands that intentionally execute fixture project tests.

- [ ] **Step 5: Run the moved full suite**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests/core -t . -v`

Expected: the same test count and pass/fail result as immediately before the move.

- [ ] **Step 6: Commit the layout migration**

```bash
git add tests README.md scripts/run_docker_smoke.sh docker/entrypoint.sh
git commit -m "test: organize framework tests under core"
```

---

### Task 2: Test collection, case, and action schema

**Files:**
- Create: `src/hwskill/test_manifest.py`
- Create: `tests/core/test_test_manifest.py`

**Interfaces:**
- Produces: `load_test_collection(path: Path, repo_root: Path) -> TestCollection`.
- Produces: `discover_test_collections(repo_root: Path) -> tuple[TestCollection, ...]`.
- Produces immutable `TestTarget`, `CommandAction`, `AgentAction`, `TestCase`, and `TestCollection`.

- [ ] **Step 1: Write failing schema and workdir tests**

```python
def test_action_workdir_overrides_case_workdir(self):
    collection = load_test_collection(self.write_manifest({
        "schema_version": 1,
        "target": {"kind": "skill", "id": "team/review"},
        "cases": [{
            "id": "review",
            "workdir": "workspace",
            "steps": [{
                "id": "test",
                "type": "command",
                "workdir": "workspace/project",
                "command": "python3 -m unittest -v",
            }],
            "post_check": {"type": "command", "command": "python3 check.py"},
        }],
    }), self.repo)
    self.assertEqual(collection.cases[0].steps[0].workdir, "workspace/project")
```

Also reject missing post-check, empty steps, duplicate case/action IDs, unknown action type, absolute/escaping workdir, empty command/prompt, wrong target root, and duplicate target collections.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_manifest -v`

Expected: FAIL because the parser does not exist.

- [ ] **Step 3: Implement immutable schema models**

```python
@dataclass(frozen=True)
class CommandAction:
    action_id: str
    command: str
    workdir: str | None = None

@dataclass(frozen=True)
class AgentAction:
    action_id: str
    prompt: str
    workdir: str | None = None
```

Generate deterministic IDs `prepare`, `step-1`, ..., `post-check` when omitted; reject explicit duplicates.

- [ ] **Step 4: Implement target-root discovery**

Skill collections must live under `tests/skills/<namespace>/<name>/test.yaml` and match `target.id`. Profile collections must live under `tests/profiles/<profile-id>/test.yaml` and match the Profile ID.

- [ ] **Step 5: Run schema tests**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_manifest -v`

Expected: PASS.

- [ ] **Step 6: Commit test schema support**

```bash
git add src/hwskill/test_manifest.py tests/core/test_test_manifest.py
git commit -m "feat: define skill and profile test collections"
```

---

### Task 3: Isolated local case runner and artifact context

**Files:**
- Create: `src/hwskill/test_runner.py`
- Create: `src/hwskill/test_artifacts.py`
- Create: `tests/core/test_test_runner.py`

**Interfaces:**
- Produces: `run_collection(collection, environment, artifact_root) -> CollectionResult`.
- Produces: `run_case(case, environment, artifact_root) -> CaseResult`.
- Produces: `CaseContext.write(path: Path) -> None` with action artifact indexes.
- Defines `ActionExecutor` protocol used by command and Agent implementations.

- [ ] **Step 1: Write failing command lifecycle test**

```python
def test_prepare_steps_and_post_check_share_workspace_and_context(self):
    result = run_case(self.case_with_commands(), self.environment, self.artifacts)
    self.assertEqual(result.status, "PASS")
    context = json.loads((result.artifact_dir / "context.json").read_text())
    self.assertEqual(list(context["actions"]), ["prepare", "write", "post-check"])
    self.assertTrue((result.artifact_dir / "workspace.diff").is_file())
```

Test prepare nonzero => BLOCKED, post-check exit 1 => FAIL, post-check other nonzero => BLOCKED, step nonzero still reaches post-check, timeout handling, environment variables, and workdir symlink escape.

- [ ] **Step 2: Run runner tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_runner -v`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement isolated workspace and command execution**

Copy the collection's `fixtures/` contents into a fresh workspace. Execute commands as `("/bin/bash", "-lc", action.command)` with `cwd` set by resolved workdir. Capture UTF-8 stdout/stderr without embedding them in `context.json`.

Define the shared runner contracts before implementing executors:

```python
@dataclass(frozen=True)
class TestEnvironment:
    repo_root: Path
    runner: str
    host: str
    model: str
    reasoning: str
    secret_values: tuple[str, ...] = ()

@dataclass(frozen=True)
class ActionResult:
    action_id: str
    status: Literal["completed", "failed", "blocked"]
    exit_code: int | None
    artifact_dir: Path

@dataclass(frozen=True)
class ActionContext:
    workspace: Path
    artifact_dir: Path
    environment: TestEnvironment

class ActionExecutor(Protocol):
    def run(self, action: CommandAction | AgentAction, context: ActionContext) -> ActionResult:
        raise NotImplementedError
```

`CaseResult` contains `case_id`, `status`, `artifact_dir`, and ordered action results. `CollectionResult` contains the target, collection status, and ordered case results.

- [ ] **Step 4: Implement normalized artifacts**

Write `actions/<id>/result.json`, `stdout.log`, and `stderr.log`. Agent executors may additionally write `events.jsonl` and `final-response.md`. Redact configured secret values before persisting text.

- [ ] **Step 5: Inject post-check context**

Set `HWSKILL_TEST_CONTEXT`, `HWSKILL_TEST_ARTIFACTS`, and `HWSKILL_TEST_WORKSPACE` to absolute paths. Post-check command exit 0/1/other maps to PASS/FAIL/BLOCKED exactly.

- [ ] **Step 6: Run runner tests**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_runner -v`

Expected: PASS.

- [ ] **Step 7: Commit the local runner**

```bash
git add src/hwskill/test_runner.py src/hwskill/test_artifacts.py tests/core/test_test_runner.py
git commit -m "feat: run isolated test cases with structured evidence"
```

---

### Task 4: Agent action host adapters and Agent post-check verdicts

**Files:**
- Create: `src/hwskill/test_agent.py`
- Modify: `src/hwskill/eval_events.py`
- Create: `tests/core/test_test_agent.py`
- Modify: `tests/core/test_host_eval_events.py`

**Interfaces:**
- Produces: `AgentExecutor.run(action, context) -> ActionResult`.
- Produces host adapters `CodexTestHost`, `ClaudeCodeTestHost`, `OpenCodeTestHost` implementing `build_command()` and `normalize_events()`.
- Produces: `parse_agent_post_check(response: str) -> Literal["PASS", "FAIL", "BLOCKED"]`.

- [ ] **Step 1: Write failing host command tests**

```python
def test_codex_action_uses_configured_model_and_json_events(self):
    command = CodexTestHost().build_command(self.context, model="gpt-5.6-terra", reasoning="high")
    self.assertIn(("--model", "gpt-5.6-terra"), adjacent_pairs(command))
    self.assertIn("--json", command)
    self.assertEqual(command[-1], self.context.action.prompt)
```

Mock subprocess execution; do not call live models in unit tests. Cover Claude/OpenCode command construction, normalized event artifacts, credential absence => BLOCKED, timeout, and invalid Agent post-check JSON.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_agent -v`

Expected: FAIL because host adapters do not exist.

- [ ] **Step 3: Implement host adapters using existing event normalization**

Set up hwskill integration inside the isolated workspace before launching the host. Capture the host's raw JSONL and normalize only observable tool/command events. The final response is stored separately.

- [ ] **Step 4: Implement Agent post-check JSON contract**

Require a top-level object with `result` equal to `pass` or `fail` and a non-empty string list `evidence`. Invalid/missing JSON is BLOCKED. Append read-only context/artifact locations to the post-check prompt as harness metadata, not as user-authored test content.

- [ ] **Step 5: Run host adapter and existing normalization tests**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_agent tests.core.test_host_eval_events tests.core.test_eval_observer -v`

Expected: PASS.

- [ ] **Step 6: Commit Agent actions**

```bash
git add src/hwskill/test_agent.py src/hwskill/eval_events.py tests/core/test_test_agent.py tests/core/test_host_eval_events.py
git commit -m "feat: execute agent test actions with observable evidence"
```

---

### Task 5: Affected-test selection and pending verification state

**Files:**
- Create: `src/hwskill/test_impact.py`
- Create: `src/hwskill/pending_verification.py`
- Modify: `src/hwskill/maintenance_transaction.py`
- Create: `tests/core/test_test_impact.py`
- Create: `tests/core/test_pending_verification.py`

**Interfaces:**
- Produces: `select_affected_tests(repo_root, base: str | None) -> TestSelection`.
- Produces: `write_pending_verification(repo_root, selection, digests) -> Path` under the Git dir.
- Produces: `clear_pending_verification(repo_root, passed_digests) -> bool`.

- [ ] **Step 1: Write failing impact matrix tests**

```python
def test_changed_skill_selects_its_collection_and_every_containing_profile(self):
    selection = select_from_changes(
        self.registry,
        ["skills-src/l1/team/review/SKILL.md"],
    )
    self.assertEqual(selection.skill_ids, ("team/review",))
    self.assertEqual(selection.profile_ids, ("review-workflow",))
```

Cover new unprofiled Skill, Profile membership changes, rename, layer-only move, manualize/adopt with identical digest, source revision-only change, ignore-only change, test fixture changes, and runtime loader/search/adapter changes selecting all.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_impact tests.core.test_pending_verification -v`

Expected: FAIL because selection/state modules do not exist.

- [ ] **Step 3: Implement Git and candidate-plan change inputs**

For CLI `--base`, parse `git diff --name-status -z <base> --` plus worktree changes. Maintenance services pass their structured pre/post Skill/Profile inventory directly, avoiding textual Git diff parsing before apply.

```python
@dataclass(frozen=True)
class TestSelection:
    core: bool
    skill_ids: tuple[str, ...]
    profile_ids: tuple[str, ...]
    collection_paths: tuple[Path, ...]
```

- [ ] **Step 4: Implement exact impact rules from the spec**

Profile ID-set or member-digest change selects every collection for that Profile. Layer/source/ignore-only changes with identical ID/content do not select behavior tests.

- [ ] **Step 5: Implement gitdir-local pending state**

Resolve the Git directory with `git rev-parse --git-dir`; write `hwskill/pending-verification.json` atomically. Store changed paths, expected content digests, and selected test IDs. Clear only when the current digests equal the recorded digests and all selected required cases PASS.

- [ ] **Step 6: Run impact and pending-state tests**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_impact tests.core.test_pending_verification -v`

Expected: PASS.

- [ ] **Step 7: Commit impact selection**

```bash
git add src/hwskill/test_impact.py src/hwskill/pending_verification.py src/hwskill/maintenance_transaction.py tests/core/test_test_impact.py tests/core/test_pending_verification.py
git commit -m "feat: select and track affected behavior tests"
```

---

### Task 6: Test setup and user-level model configuration

**Files:**
- Create: `src/hwskill/test_configuration.py`
- Create: `src/hwskill/test_setup.py`
- Create: `tests/core/test_test_configuration.py`
- Create: `tests/core/test_test_setup.py`

**Interfaces:**
- Produces: `test_config_path() -> Path` at `$XDG_CONFIG_HOME/hwskill/test.yaml` or `~/.config/hwskill/test.yaml`.
- Produces: `load_test_configuration() -> TestConfiguration` and `write_test_configuration(config) -> None`.
- Produces: `inspect_test_setup(config, runner) -> SetupReport`.

`SetupReport` contains `status: Literal["READY", "BLOCKED"]` and ordered checks with `name`, `status`, and redacted `detail`.

- [ ] **Step 1: Write failing configuration and secret-safety tests**

```python
def test_configuration_records_model_but_not_credentials(self):
    write_test_configuration(TestConfiguration(
        runner="docker",
        default_host="codex",
        hosts={"codex": HostModel("gpt-5.6-terra", "high")},
    ))
    text = test_config_path().read_text()
    self.assertIn("gpt-5.6-terra", text)
    self.assertNotIn("api_key", text.lower())
    self.assertNotIn("token", text.lower())
```

Cover XDG paths, malformed config, Docker missing, host missing, current model display, availability probe success/failure, and `--check` no-write behavior.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_configuration tests.core.test_test_setup -v`

Expected: FAIL because configuration/setup modules do not exist.

- [ ] **Step 3: Implement user-level configuration**

Store runner, `default_host`, and per-host model/reasoning effort only. Credential inspection reports source kind and availability without returning secret values. Agent actions use `default_host` unless the CLI supplies an explicit `--host` override.

```python
@dataclass(frozen=True)
class HostModel:
    model: str
    reasoning: str

@dataclass(frozen=True)
class TestConfiguration:
    runner: Literal["docker", "local"]
    default_host: str
    hosts: Mapping[str, HostModel]
```

- [ ] **Step 4: Implement setup inspection and minimal probes**

Check Docker daemon, standard image digest, host CLI version, authentication status, configured model, and a minimal model availability call. All subprocesses are injected in unit tests.

- [ ] **Step 5: Implement interactive replace/keep flow in service functions**

If current model is usable, default to keep; otherwise require a replacement. `--check` reports READY/BLOCKED and never writes configuration or builds images.

- [ ] **Step 6: Run setup tests**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_configuration tests.core.test_test_setup -v`

Expected: PASS.

- [ ] **Step 7: Commit test setup services**

```bash
git add src/hwskill/test_configuration.py src/hwskill/test_setup.py tests/core/test_test_configuration.py tests/core/test_test_setup.py
git commit -m "feat: configure test runners and agent models"
```

---

### Task 7: Test CLI

**Files:**
- Create: `src/hwskill/test_cli.py`
- Modify: `src/hwskill/cli.py`
- Create: `tests/core/test_test_cli.py`
- Modify: `tests/core/test_cli_help.py`

**Interfaces:**
- Produces `hwskill test setup|all|skills|profiles|affected|<test-path>`.
- Supports `--runner docker|local`, `--host`, `--base`, and `--json` where applicable.

- [ ] **Step 1: Write failing command routing tests**

```python
def test_direct_test_path_is_restricted_to_test_roots(self):
    code, _, error = self.run_cli("test", "../outside.yaml", "--runner", "local")
    self.assertEqual(code, 2)
    self.assertIn("tests/core, tests/skills, or tests/profiles", error)
```

Cover setup check, all, one Skill, one Profile, affected with base, JSON summaries, PASS/FAIL/BLOCKED exit status, and no implicit integrity call.

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_cli tests.core.test_cli_help -v`

Expected: FAIL because the test command group is absent.

- [ ] **Step 3: Implement one positional target dispatcher**

Parse the first target as one of `setup`, `all`, `skills`, `profiles`, `affected`, or an allowed repository-relative test path. Do not execute manifests outside the repository roots.

- [ ] **Step 4: Implement result output and pending-state clearing**

Human output groups collections/cases/actions and prints artifact directories. JSON includes actual host/model/version, runner, case status, post-check status, and artifact paths. Successful affected runs clear matching pending state.

- [ ] **Step 5: Run CLI and core tests**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_cli tests.core.test_cli_help -v`

Expected: PASS; `hwskill test` does not call or print integrity results.

- [ ] **Step 6: Commit the test CLI**

```bash
git add src/hwskill/test_cli.py src/hwskill/cli.py tests/core/test_test_cli.py tests/core/test_cli_help.py
git commit -m "feat: expose skill and profile test commands"
```

---

### Task 8: Standard Docker test runner

**Files:**
- Create: `docker/test/Dockerfile`
- Create: `docker/test/entrypoint.sh`
- Create: `docker/test/requirements.lock`
- Create: `docker/test/README.md`
- Create: `src/hwskill/docker_test_runner.py`
- Create: `src/hwskill/test_worker.py`
- Create: `tests/core/test_docker_test_runner.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `DockerTestRunner.build() -> ImageInfo` and `run(selection, config) -> TestRunResult`.
- Internal worker entry: `python -m hwskill.test_worker --request /run/request.json`.

- [ ] **Step 1: Write failing Docker command construction tests**

```python
def test_docker_run_mounts_registry_read_only_and_artifacts_writable(self):
    argv = DockerTestRunner(self.repo, self.config).build_run_command(self.request)
    self.assertIn(f"{self.repo}:/registry:ro", argv)
    self.assertIn(f"{self.artifacts}:/artifacts", argv)
    self.assertNotIn("API_KEY=", " ".join(argv))
```

Mock Docker in unit tests. Cover image label/version checks, UID/GID mapping, no credentials in argv, script network-none mode, Agent network mode, and worker request validation.

- [ ] **Step 2: Run tests and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_docker_test_runner -v`

Expected: FAIL because Docker runner modules do not exist.

- [ ] **Step 3: Build the pinned standard image definition**

Install Python, Git, CA certificates, Codex 0.147.x, Claude Code 2.1.x, and OpenCode 1.14.x. Install the repository wheel into a virtual environment. Never COPY credentials or user home data.

Define `ImageInfo(image: str, digest: str)` and `TestRunResult(status: Literal["PASS", "FAIL", "BLOCKED"], artifact_root: Path, collections: tuple[CollectionResult, ...])` in `docker_test_runner.py`.

- [ ] **Step 4: Implement host-side Docker orchestration**

Write a request JSON in a private temporary directory; mount repository/tests read-only, artifacts and workspace writable, and approved credential files read-only at fixed container paths. Pass secret values through inherited environment names, not literal Docker argv values.

- [ ] **Step 5: Implement the in-container worker**

Validate request schema and allowed mount roots, then call the same `test_runner` and `test_agent` services used locally. Emit one result JSON to the artifact root and return 0/1/3 for PASS/FAIL/BLOCKED.

- [ ] **Step 6: Run unit tests and an offline Docker smoke**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.core.test_docker_test_runner -v
docker build -f docker/test/Dockerfile -t hwskill-test:dev .
docker run --rm --network none hwskill-test:dev python -m unittest discover -s /opt/hwskills/tests/core -t /opt/hwskills -v
```

Expected: unit tests PASS, image builds, and core suite passes without network.

- [ ] **Step 7: Commit Docker test infrastructure**

```bash
git add docker/test src/hwskill/docker_test_runner.py src/hwskill/test_worker.py tests/core/test_docker_test_runner.py .gitignore
git commit -m "feat: run standardized tests in Docker"
```

---

### Task 9: Migrate Skill/Profile evals into test collections

**Files:**
- Create: `tests/skills/local/gitcode-pr-review-fetch/test.yaml`
- Create: `tests/skills/local/gitcode-pr-review-fetch/scripts/post_check.py`
- Create: `tests/profiles/codex-demo/test.yaml`
- Create: `tests/profiles/codex-demo/scripts/post_check.py`
- Move/copy: required fixtures from `examples/codex-demo` into collection fixtures only when isolation requires it
- Modify: `scripts/run_codex_live_eval.sh`
- Modify: `scripts/run_gitcode_pr_agent_eval.sh`
- Modify: `scripts/run_claude_code_gitcode_pr_agent_eval.sh`
- Modify: `scripts/run_opencode_gitcode_pr_agent_eval.sh`
- Modify: `README.md`

**Interfaces:**
- Legacy scripts become thin compatibility wrappers around `hwskill test <path>` and retain their current filenames.
- Post-check scripts consume `HWSKILL_TEST_CONTEXT` and produce exit 0/1/other.

- [ ] **Step 1: Add failing manifest integration tests**

```python
def test_repository_collections_are_discoverable(self):
    collections = discover_test_collections(ROOT)
    targets = {(item.target.kind, item.target.target_id) for item in collections}
    self.assertIn(("skill", "local/gitcode-pr-review-fetch"), targets)
    self.assertIn(("profile", "codex-demo"), targets)
```

- [ ] **Step 2: Run discovery test and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.core.test_test_manifest -v`

Expected: FAIL because repository collections are absent.

- [ ] **Step 3: Convert GitCode Skill eval**

Use prepare to create the workspace, an Agent step to fetch the known PR patch, and a command post-check that calls the existing `observe_script_resolution`, verifies output path and patch diagnostics, and exits with the standard contract. Keep host/model selection outside the manifest.

- [ ] **Step 4: Convert codex-demo Profile eval**

Use the `codex-demo` Profile, run the Agent against an isolated fixture, then run business unit tests as a command step. Post-check verifies tests pass and the boundary fix exists in the workspace diff.

- [ ] **Step 5: Replace legacy script bodies with stable wrappers**

Each retained script calls the exact new test path with `--runner docker`, preserves its documented credential prerequisites, and prints the new artifact directory. Remove duplicated Docker build/run and observer logic.

- [ ] **Step 6: Run local deterministic post-check tests and Docker core/command cases**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/core -t . -v
PYTHONPATH=src python3 -m hwskill test skills local/gitcode-pr-review-fetch --runner local
PYTHONPATH=src python3 -m hwskill test profiles codex-demo --runner local
```

Expected: core and deterministic post-check tests PASS. Collection runs are PASS when the configured Agent environment is ready or BLOCKED with explicit missing setup; they are never silently skipped.

- [ ] **Step 7: Run configured live Agent evals**

Run:

```bash
PYTHONPATH=src python3 -m hwskill test setup --check
PYTHONPATH=src python3 -m hwskill test skills local/gitcode-pr-review-fetch --runner docker
PYTHONPATH=src python3 -m hwskill test profiles codex-demo --runner docker
```

Expected: setup is READY and required Agent cases PASS. If credentials/model are unavailable, record BLOCKED evidence and do not claim live validation.

- [ ] **Step 8: Commit migrated collections**

```bash
git add tests/skills tests/profiles scripts README.md
git commit -m "test: migrate skill and profile eval collections"
```

---

### Task 10: Final end-to-end verification

**Files:**
- Modify only files required to correct failures proven by this task; commit each correction separately before the final verification rerun.

**Interfaces:**
- Validates all three implementation plans together.

- [ ] **Step 1: Run static repository gates**

```bash
PYTHONPATH=src python3 -m hwskill registry validate --repo-root .
PYTHONPATH=src python3 -m hwskill registry build --repo-root . --check
PYTHONPATH=src python3 -m hwskill integrity-check --repo-root .
git diff --check
```

Expected: all exit 0.

- [ ] **Step 2: Run complete core tests**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests/core -t . -v`

Expected: all core tests PASS.

- [ ] **Step 3: Run affected and complete behavior collections in Docker**

```bash
PYTHONPATH=src python3 -m hwskill test affected --base HEAD^ --runner docker
PYTHONPATH=src python3 -m hwskill test all --runner docker
```

Expected: required configured cases PASS; any missing external setup produces BLOCKED and prevents a success claim.

- [ ] **Step 4: Verify no local-path import remains**

```bash
rg -n 'kind: local|^root: /|registry import|import_source|SourceSpec' README.md sources src tests
```

Expected: no matches.

- [ ] **Step 5: Verify clean history scope**

```bash
git status --short
git log --oneline --decorate -20
```

Expected: no unintended uncommitted files; commits are scoped to the tasks above.
