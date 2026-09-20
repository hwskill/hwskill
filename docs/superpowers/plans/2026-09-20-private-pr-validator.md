# Private PR Skill Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a low-cost private workflow that validates installation and one declared use task for a public HWSkill PR, then writes an ephemeral `hwskill-validation` GitHub Check for the exact PR head SHA.

**Architecture:** A private repository owns trusted workflow code and secrets. A maintainer manually dispatches validation after public deterministic CI and review. The runner authenticates as a least-privilege GitHub App, reads only the target PR files, treats contributed and upstream content as untrusted data, runs in a disposable workspace, and writes a Check without storing validation state in the catalog.

**Tech Stack:** Python 3.12, PyYAML 6, jsonschema 4, direct GitHub REST API, OpenAI Responses API, GitHub Actions, unittest, disposable process/container sandbox.

**Spec:** `docs/superpowers/specs/2026-09-20-translation-first-skill-catalog-design.md`

## Global Constraints

- This plan is executed in a new private repository named `hwskill/hwskill-validator`; it is not implemented in the public `hwskill/hwskill` tree.
- The validator repository does not accept external pull requests. `pull_request_target` and execution of public-PR workflow code are forbidden.
- Validate an explicit repository, PR number, expected head SHA, and skill ID. Abort before billing if the current head differs.
- Never execute upstream installers, hooks, setup scripts, workflow files, or dependency commands. Copy declared skill material as data into a disposable target directory.
- Secrets exist only in the protected `skill-validation` Environment. Logs redact credentials and do not print full model inputs or third-party content.
- One installation task, one use task, at most one infrastructure retry, and hard token/time/cost limits per skill.
- Results are `pass`, `fail`, `not-applicable`, or `error` and live only in the Check and Actions logs.
- The first release is manual `workflow_dispatch`; do not deploy a webhook service.

## Review Focus

- A stale head SHA, unauthorized repository, malformed skill ID, changed source locator, or changed expected result must stop before installation or model use.
- Public PR content cannot alter workflow code, prompts, sandbox policy, requested model, budgets, GitHub token permissions, or Check conclusion mapping.
- GitHub App permissions must be limited to Metadata read, Contents read, Pull requests read, and Checks write on `hwskill/hwskill`.
- Cache identity is the tuple `(repository, head_sha, skill_id, validator_version)`; a failed or changed head never reuses a pass.
- `not-applicable` needs a reason and protected-environment reviewer approval. It must not silently map to pass.
- Cost and token totals appear in the private run summary without exposing secrets or full prompts.

---

## Human Readiness Gate

These actions require organization ownership, billing authority, secrets, or policy judgment and cannot be completed autonomously by an Agent.

| ID | Human-provided item | Owner | Needed before | Acceptance evidence |
|---|---|---|---|---|
| PV1 | Create private repository `hwskill/hwskill-validator`, disable public forking, and restrict write access to maintainers | Organization administrator | Task 1 | Repository settings screenshot or reviewed settings record |
| PV2 | Create GitHub App `HWSkill Validator`, install it only on `hwskill/hwskill`, and grant Metadata read, Contents read, Pull requests read, Checks write | Organization administrator | Task 5 | Installation ID is discoverable and a test token can read a PR and create a draft Check |
| PV3 | Add Actions variable `HWSKILL_VALIDATOR_APP_ID` and secret `HWSKILL_VALIDATOR_APP_PRIVATE_KEY` | Secret administrator | Task 5 | Workflow secret/variable names exist; values are never committed or logged |
| PV4 | Create protected Environment `skill-validation`, add required reviewers, and prevent self-review where the organization plan supports it | Organization administrator | Task 6 | Environment rule is visible and blocks an unapproved dispatch job |
| PV5 | Choose a currently supported low-cost model and provide its API credential; approve maximum 150,000 input tokens and 20,000 output tokens per skill with two model calls total | Billing owner | Task 4 | `VALIDATION_MODEL`, reviewed budget, and `OPENAI_API_KEY` secret are configured in the Environment |
| PV6 | Approve the dispatch allowlist and designate maintainers who may request runs | Repository owner | Task 6 | `VALID_TARGETS` and team/reviewer policy are reviewed |
| PV7 | Decide case by case whether skills needing private accounts, special networks, paid tools, or hardware are `not-applicable`, or provide a dedicated test resource | Maintainer and resource owner | Task 4 and each run | Approved reason in the protected workflow review or dedicated credential/resource |
| PV8 | After a successful dry run, require the exact `hwskill-validation` Check on public `main` | Organization administrator | Public cutover | Branch protection rule contains the exact Check name |

Agent work that can be prepared before PV1-PV5: complete repository skeleton as a patch or archive, write tests, define permissions, prepare App setup instructions, create sample payloads, and estimate maximum run cost. The Agent cannot create organization resources, accept billing, obtain secret values, approve protected environments, or decide whether a semantic behavior result is acceptable.

## Cost Envelope

- Default dispatch validates one skill. A PR with multiple skills uses an explicit comma-separated list and runs sequentially under one aggregate cap.
- Reject source packages larger than 2 MiB or 200 files before model use.
- Truncate model context by a fixed trusted policy: Entry, translated headings, original `SKILL.md`, referenced local text files, and declared example only.
- Use at most two model calls: plan/evidence extraction and result judgment. Do not run open-ended autonomous loops.
- Default wall-clock limit is 15 minutes per skill. Installation discovery commands receive 30 seconds each.
- Store a private run-summary artifact for 14 days; do not store the installed workspace.
- Before enabling the model, the billing owner records current provider prices and a calculated hard currency cap in `config/budget.yaml`. The workflow fails closed if the price table or cap is missing.

---

### Task 1: Create the Private Repository Skeleton and Trusted Configuration

**Files in `hwskill/hwskill-validator`:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `config/policy.yaml`
- Create: `config/budget.yaml.example`
- Create: `src/hwskill_validator/__init__.py`
- Create: `src/hwskill_validator/models.py`
- Create: `tests/test_models.py`
- Create: `.github/CODEOWNERS`
- Create: `.github/dependabot.yml`

**Interfaces:**
- `ValidationRequest(repository, pr_number, expected_head_sha, skill_ids)` validates immutable dispatch inputs.
- `ValidationResult(conclusion, summary, details, fetched_commit, cost)` accepts only the four public result classes.
- Trusted policy allows only `hwskill/hwskill` and model/budget identifiers defined on the default branch.

- [ ] **Step 1: Obtain PV1 or prepare these files as a reviewable patch if the repository does not yet exist.**
- [ ] **Step 2: Write failing model tests** for malformed repository names, nonpositive PR numbers, non-40-character SHA values, duplicate/unsafe skill IDs, unknown conclusions, and negative costs.
- [ ] **Step 3: Run tests and confirm failure.**

```bash
python -m unittest tests.test_models -v
```

- [ ] **Step 4: Implement frozen dataclasses and strict parsing.** Skill IDs match `^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$`; target repositories must be in trusted `config/policy.yaml`.
- [ ] **Step 5: Add CODEOWNERS for workflow, policy, budget, prompts, and source code.** Require maintainer review on the default branch.
- [ ] **Step 6: Run tests and commit.**

```bash
python -m unittest tests.test_models -v
git add .
git commit -m "chore: initialize private skill validator"
```

---

### Task 2: Read and Pin a Public PR Safely

**Files:**
- Create: `src/hwskill_validator/github_client.py`
- Create: `src/hwskill_validator/pr_reader.py`
- Create: `tests/fixtures/pr-files.json`
- Create: `tests/test_pr_reader.py`

**Interfaces:**
- `GitHubClient.get_pr(repository, number) -> PullRequestMetadata`
- `GitHubClient.get_file(repository, path, ref) -> bytes`
- `read_skill_change(client, request) -> SkillChange` checks the live head before and after reading files.
- `SkillChange` contains only the selected Entry, translation, PR report, and trusted comparison metadata.

- [ ] **Step 1: Add fake-client tests** for exact head match, stale head, missing Entry, path traversal, duplicate Entry, unrelated skill request, oversized content, and a head changing during reads.
- [ ] **Step 2: Run tests and confirm failure.**
- [ ] **Step 3: Implement GitHub REST reads at the explicit head SHA.** Do not check out or execute the public repository. Decode file API responses in memory and cap response sizes.
- [ ] **Step 4: Parse Entry and translation with a vendored v2 schema snapshot reviewed in this private repository.** Do not import executable code from the public PR.
- [ ] **Step 5: Re-read the PR head after all files are loaded and abort if it changed.**
- [ ] **Step 6: Run tests and commit.**

```bash
python -m unittest tests.test_pr_reader -v
git add src/hwskill_validator tests
git commit -m "feat: read pinned skill changes safely"
```

---

### Task 3: Fetch Upstream Skill Material as Untrusted Data

**Files:**
- Create: `src/hwskill_validator/source.py`
- Create: `src/hwskill_validator/archive.py`
- Create: `tests/test_source.py`
- Create: `tests/fixtures/source-tree.json`

**Interfaces:**
- `fetch_source(locator, limits) -> FetchedSource(files, commit_sha, source_url)` resolves the declared ref or current default branch and records the observed commit only in the run result.
- `sanitize_relative_path(value: str) -> PurePosixPath` rejects absolute paths, traversal, NUL, control characters, and `.git` internals.

- [ ] **Step 1: Add failing tests** for omitted ref, branch, tag, commit, missing `SKILL.md`, symlink, submodule, archive bomb, too many files, oversized files, traversal, and default branch moving during fetch.
- [ ] **Step 2: Implement provider adapters using archive or Contents APIs.** Fetch only the locator path, reject symlinks/submodules, cap 200 files and 2 MiB total, and hash bytes for the private report.
- [ ] **Step 3: Treat all retrieved files as inert bytes.** Do not invoke shell, Git hooks, package managers, or executable bits.
- [ ] **Step 4: Run tests and commit.**

```bash
python -m unittest tests.test_source -v
git add src/hwskill_validator tests
git commit -m "feat: fetch bounded upstream skill data"
```

---

### Task 4: Validate Installation and One Declared Use Task

**Files:**
- Create: `src/hwskill_validator/install.py`
- Create: `src/hwskill_validator/behavior.py`
- Create: `src/hwskill_validator/prompts.py`
- Create: `src/hwskill_validator/budget.py`
- Create: `tests/test_install.py`
- Create: `tests/test_behavior.py`
- Create: `tests/test_budget.py`

**Interfaces:**
- `install_skill(source, target_agent, workspace) -> InstallationEvidence` copies files to a disposable project scope and checks discoverability using trusted commands.
- `evaluate_example(entry, source, evidence, client, budget) -> BehaviorEvidence` runs exactly one declared example under trusted prompts.
- `Budget.reserve(estimated_input_tokens)`, `record(usage)`, and `remaining_currency` fail closed before exceeding caps.

- [ ] **Step 1: Add installation tests** for complete file copy, destination confinement, ignored executable bits, discovery success/failure, unsupported Agent, and cleanup.
- [ ] **Step 2: Add behavior tests** using a fake model client for expected result, wrong result, refusal, tool request, prompt injection in skill text, timeout, provider error, and token cap.
- [ ] **Step 3: Implement a trusted installation adapter per supported Agent.** The adapter may copy files and invoke only a checked, fixed discovery command. It must never use commands supplied by the Entry or source.
- [ ] **Step 4: Implement fixed prompts.** Delimit untrusted skill content, prohibit tool/network/credential access, ask for structured JSON evidence, and validate response schema before judging.
- [ ] **Step 5: Obtain PV5 and PV7.** Add the chosen model and reviewed hard budget to protected configuration. Specialized resources are absent by default and yield `not-applicable` with a concrete reason.
- [ ] **Step 6: Run tests and commit.**

```bash
python -m unittest tests.test_install tests.test_behavior tests.test_budget -v
git add src/hwskill_validator tests config
git commit -m "feat: validate skill install and behavior"
```

---

### Task 5: Write the Exact-SHA GitHub Check

**Files:**
- Create: `src/hwskill_validator/checks.py`
- Create: `src/hwskill_validator/report.py`
- Create: `tests/test_checks.py`
- Create: `tests/test_report.py`

**Interfaces:**
- Check name is exactly `hwskill-validation`.
- `create_in_progress(repository, head_sha, external_id) -> check_run_id`
- `complete_check(check_run_id, result) -> None`
- Mapping: `pass -> success`, `fail -> failure`, `not-applicable -> neutral`, `error -> action_required`.

- [ ] **Step 1: Add fake-API tests** for exact head association, stable external ID, conclusion mapping, annotation limits, redaction, API retry, and refusing a changed head.
- [ ] **Step 2: Implement a concise Check summary.** Include skill ID, fetched upstream commit, installation evidence, example and result summary, limitations, duration, token usage, and estimated cost. Exclude full prompts, source bodies, and credentials.
- [ ] **Step 3: Obtain PV2 and PV3, then perform a permissions smoke test on a disposable public test PR.** Verify the App cannot write repository contents or manage pull requests.
- [ ] **Step 4: Run tests and commit.**

```bash
python -m unittest tests.test_checks tests.test_report -v
git add src/hwskill_validator tests
git commit -m "feat: publish skill validation checks"
```

---

### Task 6: Add the Protected Manual Workflow

**Files:**
- Create: `src/hwskill_validator/cli.py`
- Create: `.github/workflows/validate-pr.yml`
- Create: `tests/test_cli.py`
- Create: `docs/runbook.md`

**Interfaces:**
- Workflow inputs: `repository`, `pr_number`, `expected_head_sha`, `skill_ids`.
- Job environment: `skill-validation`.
- Workflow permissions are `contents: read`; GitHub App authentication is created inside the job for target-repository reads and Checks writes.

- [ ] **Step 1: Add CLI tests** for parsing, target allowlist, stale SHA exit before model initialization, sequential multi-skill limits, and final aggregate conclusion.
- [ ] **Step 2: Implement CLI orchestration.** Create the in-progress Check only after request validation; always complete it in a `finally` path when a Check ID exists.
- [ ] **Step 3: Add `workflow_dispatch` only.** Pin every third-party Action by full commit SHA, set a 30-minute job timeout, disable credential persistence, and upload only the redacted JSON summary for 14 days.
- [ ] **Step 4: Obtain PV4 and PV6.** Confirm an unapproved run blocks at the protected Environment and an unauthorized target fails before secret use.
- [ ] **Step 5: Document dispatch, retry, stale-head, not-applicable approval, secret rotation, budget updates, and incident shutdown.**
- [ ] **Step 6: Run tests and commit.**

```bash
python -m unittest tests.test_cli -v
git add src/hwskill_validator tests .github/workflows/validate-pr.yml docs/runbook.md
git commit -m "ci: add protected manual validation workflow"
```

---

### Task 7: Add Idempotency, Concurrency, and Cost Evidence

**Files:**
- Create: `src/hwskill_validator/cache.py`
- Create: `tests/test_cache.py`
- Modify: `.github/workflows/validate-pr.yml`
- Modify: `src/hwskill_validator/cli.py`
- Create: `docs/cost-model.md`

**Interfaces:**
- Cache key is SHA-256 of `repository\0head_sha\0skill_id\0validator_version`.
- Only a previous `pass` for the exact key can skip billed work; a caller may request `force` after protected approval.
- Workflow concurrency group is `hwskill-validation-${repository}-${pr_number}-${skill_ids}` with `cancel-in-progress: false`.

- [ ] **Step 1: Add tests** for exact-key hits, changed head/version/skill misses, fail/error nonreuse, concurrent lock, and corrupted cache.
- [ ] **Step 2: Implement private cache metadata without workspaces or source bodies.** Cache only result summary, usage, timestamps, and Check URL.
- [ ] **Step 3: Record provider pricing assumptions, worst-case tokens, worst-case currency, GitHub runner minutes, and monthly examples for 10, 50, and 200 skill validations.** The billing owner must approve changes to prices or caps.
- [ ] **Step 4: Run tests and commit.**

```bash
python -m unittest tests.test_cache tests.test_budget -v
git add src/hwskill_validator tests .github/workflows/validate-pr.yml docs/cost-model.md
git commit -m "feat: bound validator retries and cost"
```

---

### Task 8: End-to-End Dry Run and Public Cutover

**Files:**
- Create: `tests/fixtures/e2e-pass/`
- Create: `tests/fixtures/e2e-fail/`
- Create: `docs/acceptance.md`
- Modify: `README.md`

- [ ] **Step 1: Run the complete private test suite.**

```bash
python -m unittest discover -s tests -p 'test_*.py'
python -m build
```

- [ ] **Step 2: Create a public test PR with one bounded sample skill and a complete validation report.** Record its exact head SHA.
- [ ] **Step 3: Manually dispatch the private workflow.** Verify environment approval, head recheck, source commit capture, install evidence, one use task, cost summary, cleanup, and `hwskill-validation` attached to the exact SHA.
- [ ] **Step 4: Push a new commit to the test PR.** Confirm the old Check does not satisfy the new head and the stale dispatch aborts before billing.
- [ ] **Step 5: Exercise fail, error, and not-applicable paths.** The maintainer reviews summaries and confirms none maps silently to success.
- [ ] **Step 6: Obtain PV8.** Add the exact Check to public branch protection only after the successful dry run.
- [ ] **Step 7: Record acceptance evidence without secrets or full prompts, then commit docs.**

```bash
git add README.md docs/acceptance.md tests/fixtures
git commit -m "docs: record validator acceptance"
```

## Completion Criteria

- A maintainer can dispatch validation for an explicit PR head and skill list without copying secrets or code into the public repository.
- Stale heads and disallowed targets stop before any model call.
- Upstream and PR files remain inert data and cannot change validator execution policy.
- Installation and one declared behavior task produce a redacted, bounded-cost Check for the exact head SHA.
- GitHub App, Environment, dispatch allowlist, billing cap, not-applicable policy, and branch protection are human-approved and evidenced.
- The public catalog never receives validation state, reports, fetched commits, prompts, credentials, or validator logs.
