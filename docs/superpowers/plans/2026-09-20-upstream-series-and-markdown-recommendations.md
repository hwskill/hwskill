# Upstream Series and Markdown Recommendations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish all 40 stable Superpowers and Matt Pocock skills, two Markdown-maintained series recommendations, and a first-class recommendation browsing and contribution page.

**Architecture:** Keep one reviewed external Entry YAML per skill at immutable upstream commits. Replace recommendation source YAML with one Markdown file containing validated YAML Frontmatter; normalize it to the backward-compatible recommendation JSON contract, then render it safely at Astro build time. Reuse one contribution-prompt module across the contribution and recommendation pages.

**Tech Stack:** Python 3.10+, PyYAML 6, jsonschema 4, Astro 7.0.0, Node 22.19, markdown-it 15.0.2, Pagefind 1.5.2, unittest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-20-upstream-series-and-markdown-recommendations-design.md`

## Global Constraints

- Superpowers source is `https://github.com/obra/superpowers.git` at `5bf4e78011075bcfc0dc295f0724994cd123ee71`; exactly 15 `skills/*/SKILL.md` directories are in scope.
- Matt Pocock source is `https://github.com/mattpocock/skills.git` at `c55ee46073ed923f86ce59a5eb3b6d895095d1b7`; exactly the 18 Engineering and 7 Productivity skills in the README Reference are in scope.
- Exclude Matt Pocock `skills/in-progress/`, `skills/misc/`, and `skills/deprecated/`.
- Both upstream repositories use MIT licenses; link license and install documentation at the immutable commit.
- External skill bodies stay upstream. Do not copy or execute their files or scripts.
- Unverified entries remain installable through an informed Agent prompt. State `installation` and `behavior` as not run; do not block the prompt or claim success.
- Recommendation authors edit one `recommendations/<id>.md` file. Repository source does not retain parallel recommendation YAML files.
- Markdown raw HTML stays disabled and dangerous URL protocols never reach generated HTML.
- Keep public recommendation machine documents at `schema_version: 1`; optional `summary` and `body_format` preserve old-document compatibility.
- Existing release/feed lifecycle behavior remains unchanged.
- Do not stage or modify the existing untracked `.superpowers/` directory.

## Review Focus

- Frontmatter with CRLF, a missing closing delimiter, duplicate YAML keys, or an empty Markdown body must return a stable directory issue instead of crashing or publishing partial content; Task 1 tests each shape.
- An old machine recommendation without `summary` or `body_format` must still validate and flow through sharing code as plain text; Task 1 adds the compatibility test.
- Raw HTML and `javascript:`, `data:`, or credential-bearing links must not become active page markup; Task 5 tests renderer output directly.
- A misplaced entry, duplicate source path, mutable ref, or excluded Matt directory must make the series completeness test fail; Tasks 2 and 3 pin exact maps.
- A 25-skill recommendation must remain navigable and readable on narrow screens, with every associated skill linked exactly once; Tasks 5 and 6 test compact grouping and generated HTML.

---

## File Structure

### Recommendation contract and build

- Create `src/hwskill/directory/recommendations.py`: strict Frontmatter parser and normalized recommendation loader.
- Create `schemas/recommendation-source.schema.json` and `src/hwskill/directory/schemas/recommendation-source.schema.json`: source Frontmatter contract.
- Modify both copies of `recommendation.schema.json`: add optional normalized `summary` and `body_format` fields.
- Modify `src/hwskill/directory/yaml_io.py`: expose strict parsing from an in-memory string without weakening duplicate-key checks.
- Modify `src/hwskill/directory/entries.py`: discover `.md`, validate source metadata, report Markdown errors, enforce ID/path matching, and include Markdown in the input digest.
- Modify `src/hwskill/directory/catalog.py`: load normalized Markdown recommendations into `recommendations.json`.
- Replace `templates/recommendations/recommendation.yaml` with `templates/recommendations/recommendation.md`.
- Migrate all repository and directory-test recommendation fixtures from YAML to Markdown.

### Series inventory and recommendation content

- Add or update 15 `entries/l1|l2/superpowers/*.yaml` files.
- Add or update 25 `entries/l1|l2/mattpocock/*.yaml` files.
- Create `tests/directory/test_upstream_series.py`: exact stable inventory, layer, source path, ref, license, and exclusion tests.
- Create `recommendations/superpowers-engineering-workflow.md` and `recommendations/matt-pocock-composable-engineering.md`.
- Convert `recommendations/engineering-evidence.yaml` and `recommendations/data-and-performance.yaml` to `.md`.

### Site and contribution flow

- Create `site/src/lib/markdown.mjs`: one configured safe Markdown renderer.
- Create `site/tests/markdown.test.mjs`: renderer security and supported-syntax tests.
- Create `site/src/lib/contribution-prompts.ts`: shared public URLs and prompt builders.
- Create `site/src/components/CopyPrompt.astro`: accessible read-only prompt and copy behavior.
- Create `site/src/pages/recommendations/index.astro`: recommendation list and contribution guidance.
- Modify `site/src/pages/recommendations/[id].astro`: safe Markdown article and compact skill grouping.
- Modify `site/src/pages/contribute/index.astro`, `site/src/pages/index.astro`, `site/src/layouts/BaseLayout.astro`, `site/src/lib/data.ts`, `site/src/styles/global.css`, and `site/scripts/check-build.mjs`.
- Modify `site/package.json` and `site/package-lock.json` to pin `markdown-it` 15.0.2.
- Modify `CONTRIBUTING.md` and `docs/guides/agent-contribution.md` for the Markdown source contract.

---

### Task 1: Introduce the Markdown Recommendation Source Contract

**Files:**
- Create: `src/hwskill/directory/recommendations.py`
- Create: `schemas/recommendation-source.schema.json`
- Create: `src/hwskill/directory/schemas/recommendation-source.schema.json`
- Modify: `schemas/recommendation.schema.json`
- Modify: `src/hwskill/directory/schemas/recommendation.schema.json`
- Modify: `src/hwskill/directory/yaml_io.py`
- Modify: `src/hwskill/directory/entries.py`
- Modify: `src/hwskill/directory/catalog.py`
- Replace: `templates/recommendations/recommendation.yaml` with `templates/recommendations/recommendation.md`
- Rename and edit: every `tests/directory/fixtures/*/recommendations/*.yaml` to `.md`
- Rename and edit: `recommendations/engineering-evidence.yaml`, `recommendations/data-and-performance.yaml`
- Modify: `tests/directory/test_entries.py`
- Modify: `tests/directory/test_build.py`
- Modify: `tests/directory/test_packaging.py`
- Modify: `site/scripts/check-build.mjs`
- Modify: `site/src/pages/contribute/index.astro`

**Interfaces:**
- Consumes: strict JSON-compatible YAML rules from `yaml_io.py` and schemas returned by `validator_for(name)`.
- Produces: `load_recommendation(path: Path) -> dict[str, Any]`, returning Frontmatter plus `body`, `body_format: "markdown"`; `RecommendationContractError` with `code`, `field`, and message.

- [ ] **Step 1: Add failing parser and validation tests**

Add focused tests to `tests/directory/test_entries.py` using temporary repositories. Cover a valid CRLF document, missing opening delimiter, missing closing delimiter, duplicate Frontmatter key, Frontmatter `body`, filename/ID mismatch, whitespace-only body, unsafe evidence URL, and a ready reference to a missing skill.

```python
def test_markdown_recommendation_is_normalized_and_path_checked(self) -> None:
    from hwskill.directory.recommendations import load_recommendation

    with TemporaryDirectory() as directory:
        path = Path(directory) / "guide.md"
        path.write_text(
            "---\r\nschema_version: 1\r\nid: guide\r\nskills:\r\n  - id: local/hosted\r\n"
            "title: Guide\r\nsummary: Short guide.\r\nauthor: test\r\nstatus: ready\r\n---\r\n\r\n# Body\r\n",
            encoding="utf-8",
        )
        self.assertEqual(
            load_recommendation(path)["body_format"],
            "markdown",
        )
        self.assertEqual(load_recommendation(path)["body"], "# Body\n")
```

For failure cases, assert exact issue codes: `recommendation-frontmatter-missing`, `recommendation-frontmatter-unclosed`, `yaml-duplicate-key`, `schema-additionalProperties`, `recommendation-path-mismatch`, and `recommendation-body-empty`.

- [ ] **Step 2: Run the focused tests and confirm the missing parser fails**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_entries.DirectoryValidationTests.test_markdown_recommendation_is_normalized_and_path_checked -v
```

Expected: FAIL because `hwskill.directory.recommendations` does not exist.

- [ ] **Step 3: Add strict in-memory YAML parsing**

Refactor `yaml_io.py` so `load_yaml()` delegates to a new function without changing the loader or JSON-value checks:

```python
def load_yaml_text(text: str) -> dict[str, Any]:
    try:
        value = yaml.load(text, Loader=_StrictSafeLoader)
    except YamlContractError:
        raise
    except yaml.YAMLError as exc:
        raise YamlContractError(str(exc)) from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise YamlContractError("YAML document must be an object with string keys")
    _require_json_value(value)
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    return load_yaml_text(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Implement the Frontmatter parser**

Create `recommendations.py` with an error type carrying directory issue data and a loader that normalizes newlines and preserves a single trailing newline:

```python
class RecommendationContractError(ValueError):
    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def load_recommendation(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise RecommendationContractError("recommendation-frontmatter-missing", "$", "Recommendation must start with YAML Frontmatter.")
    closing = next((index for index, line in enumerate(lines[1:], 1) if line.rstrip("\n") == "---"), None)
    if closing is None:
        raise RecommendationContractError("recommendation-frontmatter-unclosed", "$", "Recommendation Frontmatter requires a closing delimiter.")
    metadata = load_yaml_text("".join(lines[1:closing]))
    body = "".join(lines[closing + 1:]).strip()
    if not body:
        raise RecommendationContractError("recommendation-body-empty", "body", "Recommendation Markdown body must not be empty.")
    return {**metadata, "body": body + "\n", "body_format": "markdown"}
```

Keep schema validation outside this loader so directory validation reports every schema error consistently.

- [ ] **Step 5: Add source and normalized schemas**

Define `recommendation-source.schema.json` with `additionalProperties: false`; require `schema_version`, `id`, `skills`, `title`, `summary`, `author`, and `status`; reuse the current field constraints for skills, topics, evidence, status, and withdrawal reason. It must not define `body` or `body_format`.

Add these optional properties to both normalized recommendation schemas:

```json
"summary": {"type": "string", "minLength": 1},
"body_format": {"enum": ["markdown"]}
```

Keep existing `body` required, and keep `schema_version` at 1.

- [ ] **Step 6: Switch repository validation and build discovery to Markdown**

In `entries.py`, discover `recommendations/**/*.md` and `templates/recommendations/**/*.md`; call `load_recommendation`; validate source metadata before the injected `body` fields, then validate the normalized result with `validator_for("recommendation")`. Convert `RecommendationContractError` and `YamlContractError` into stable `DirectoryIssue` values. Enforce:

```python
expected = root / "recommendations" / f"{recommendation['id']}.md"
if path != expected:
    issues.append(_issue(
        path,
        root,
        "id",
        "recommendation-path-mismatch",
        "Recommendation ID must match recommendations/<id>.md.",
        "Rename the file or correct its Frontmatter id.",
    ))
```

In `catalog.py`, replace recommendation `load_yaml(path)` calls with `load_recommendation(path)`. The output object remains `{"schema_version": 1, "recommendations": recommendations}`.

- [ ] **Step 7: Migrate repository recommendations, template, and fixtures**

Move the two repository recommendations and all test recommendations to `.md`. Put their previous metadata in Frontmatter, add concise `summary`, remove `body` from Frontmatter, and place its former value after the delimiter. Replace the public template with `recommendation.md` using the same source fields and a Markdown section structure: “适用场景”, “推荐理由”, “验证边界”, and “已知限制”.

Update dynamic fixture strings in `tests/directory/test_build.py` to write `.md` Frontmatter. Update `site/scripts/check-build.mjs` and the contribution page template URL to `templates/recommendations/recommendation.md` and `schemas/recommendation-source.schema.json`.

- [ ] **Step 8: Add backward-compatibility and digest tests**

Add assertions that:

```python
legacy = {
    "schema_version": 1,
    "id": "legacy",
    "skills": [{"id": "local/hosted"}],
    "title": "Legacy",
    "body": "Plain text.",
    "author": "test",
    "status": "ready",
}
self.assertEqual(list(validator_for("recommendation").iter_errors(legacy)), [])
```

Also build twice, edit only the Markdown body, and assert `input_digest` changes. Extend the wheel test to load `validator_for("recommendation-source")` from installed package data.

- [ ] **Step 9: Run directory tests and validate the real repository**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_entries tests.directory.test_build tests.directory.test_packaging -v
PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python3 -m hwskill.directory build --repo-root . --out /tmp/hwskill-markdown-directory --json
```

Expected: all tests pass; validate and build report `result: pass`; public recommendation objects contain `summary`, Markdown `body`, and `body_format: markdown`.

- [ ] **Step 10: Commit the source-contract migration**

```bash
git add schemas src/hwskill/directory templates/recommendations recommendations tests/directory site/scripts/check-build.mjs site/src/pages/contribute/index.astro
git commit -m "feat: maintain recommendations as markdown"
```

### Task 2: Add the Complete Superpowers Inventory

**Files:**
- Create or modify: `entries/l1/superpowers/*.yaml`
- Create or modify: `entries/l2/superpowers/*.yaml`
- Create: `tests/directory/test_upstream_series.py`

**Interfaces:**
- Consumes: existing Entry Schema and immutable external-source conventions.
- Produces: exactly 15 active `superpowers/*` entries at one source commit.

- [ ] **Step 1: Write the exact failing inventory test**

In `test_upstream_series.py`, load all Entry YAML and compare an exact mapping:

```python
SUPERPOWERS = {
    "brainstorming": "l1",
    "diagnosing-superpowers": "l1",
    "dispatching-parallel-agents": "l1",
    "executing-plans": "l1",
    "subagent-driven-development": "l2",
    "using-superpowers": "l1",
    "writing-plans": "l1",
    "writing-skills": "l1",
    "finishing-a-development-branch": "l2",
    "receiving-code-review": "l2",
    "requesting-code-review": "l2",
    "systematic-debugging": "l2",
    "test-driven-development": "l2",
    "using-git-worktrees": "l2",
    "verification-before-completion": "l2",
}
```

For every entry assert repository, `skills/<name>` path, full SHA, `install.method == "upstream"`, immutable README instructions URL, MIT identifier and immutable license URL, owner `Jesse Vincent`, active lifecycle, nonempty purposes/examples/limitations, and a limitation stating installation and behavior were not run.

- [ ] **Step 2: Run the inventory test and confirm the current two-entry set fails**

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_upstream_series.UpstreamSeriesTests.test_superpowers_inventory -v
```

Expected: FAIL showing 13 missing IDs and the two existing entries at the old ref and layer.

- [ ] **Step 3: Create and review all 15 entry files**

Use the exact map above. Every file uses this immutable source and license shape:

```yaml
source:
  kind: external
  publicity: public
  locator:
    type: git
    repository: https://github.com/obra/superpowers.git
    path: skills/brainstorming
    requested_ref: 5bf4e78011075bcfc0dc295f0724994cd123ee71
install:
  method: upstream
  default_scope: project
  instructions_url: https://github.com/obra/superpowers/blob/5bf4e78011075bcfc0dc295f0724994cd123ee71/README.md
license:
  status: known
  identifier: MIT
  url: https://github.com/obra/superpowers/blob/5bf4e78011075bcfc0dc295f0724994cd123ee71/LICENSE
owner: Jesse Vincent
lifecycle: active
```

Write distinct Chinese summaries and observable prompt/outcome pairs from each pinned SKILL frontmatter. Record explicit companions only where the pinned skill invokes them: `brainstorming` → `writing-plans`; `executing-plans` → `writing-plans`, `requesting-code-review`, `finishing-a-development-branch`; `subagent-driven-development` → `using-git-worktrees`, `requesting-code-review`, `finishing-a-development-branch`; `systematic-debugging` → `test-driven-development`, `verification-before-completion`; `writing-plans` → `brainstorming`, `executing-plans` or `subagent-driven-development`; `writing-skills` → `test-driven-development`, `verification-before-completion`. State required multi-Agent or Git capabilities in `requirements`, not as verified behavior.

- [ ] **Step 4: Run the series and repository validation tests**

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_upstream_series.UpstreamSeriesTests.test_superpowers_inventory -v
PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json
```

Expected: PASS, with 15 unique Superpowers source identities and no path mismatch after moving the existing debugging and TDD entries to L2.

- [ ] **Step 5: Commit Superpowers entries**

```bash
git add entries/l1/superpowers entries/l2/superpowers tests/directory/test_upstream_series.py
git commit -m "feat: catalog the complete superpowers series"
```

### Task 3: Add the Complete Matt Pocock Inventory

**Files:**
- Create or modify: `entries/l1/mattpocock/*.yaml`
- Create: `entries/l2/mattpocock/*.yaml`
- Modify: `tests/directory/test_upstream_series.py`

**Interfaces:**
- Consumes: the inventory-test helpers created in Task 2.
- Produces: exactly 25 active `mattpocock/*` entries at one source commit.

- [ ] **Step 1: Add the exact failing Matt inventory and exclusion test**

Use these exact groups:

```python
MATT_ENGINEERING = {
    "ask-matt", "code-review", "codebase-design", "diagnosing-bugs",
    "domain-modeling", "grill-with-docs", "implement",
    "improve-codebase-architecture", "prototype", "research",
    "resolving-merge-conflicts", "setup-matt-pocock-skills", "tdd",
    "to-spec", "to-tickets", "triage", "wayfinder", "wizard",
}
MATT_PRODUCTIVITY = {
    "grill-me", "grilling", "handoff", "teach", "to-questionnaire",
    "wait-what", "writing-for-agents",
}
```

Assert Engineering entries are L2 and use `skills/engineering/<name>`; Productivity entries are L1 and use `skills/productivity/<name>`. Assert the exact union has 25 members and no entry path starts with `skills/in-progress/`, `skills/misc/`, or `skills/deprecated/`.

- [ ] **Step 2: Run the Matt test and confirm 24 entries are missing**

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_upstream_series.UpstreamSeriesTests.test_mattpocock_inventory -v
```

Expected: FAIL because only `mattpocock/grill-me` currently exists.

- [ ] **Step 3: Create and review all 25 entry files**

Use this shared immutable metadata, substituting the exact category and name from the sets:

```yaml
source:
  kind: external
  publicity: public
  locator:
    type: git
    repository: https://github.com/mattpocock/skills.git
    path: skills/engineering/ask-matt
    requested_ref: c55ee46073ed923f86ce59a5eb3b6d895095d1b7
install:
  method: upstream
  default_scope: project
  instructions_url: https://github.com/mattpocock/skills/blob/c55ee46073ed923f86ce59a5eb3b6d895095d1b7/README.md
license:
  status: known
  identifier: MIT
  url: https://github.com/mattpocock/skills/blob/c55ee46073ed923f86ce59a5eb3b6d895095d1b7/LICENSE
owner: Matt Pocock
lifecycle: active
```

Write distinct summaries, purposes, keywords, and observable examples from the pinned README Reference and each SKILL frontmatter. Encode direct orchestration companions: `grill-me` → `grilling`; `grill-with-docs` → `grilling`, `domain-modeling`; `implement` → `tdd`, `code-review`; `improve-codebase-architecture` → `codebase-design`, `domain-modeling`, `grilling`; `triage` → `setup-matt-pocock-skills`, `grilling`, `domain-modeling`; `wayfinder` → `setup-matt-pocock-skills`, `grilling`, `domain-modeling`, `prototype`, `research`. State project setup, tracker, sub-Agent, browser, or Bash requirements only on skills that use them.

- [ ] **Step 4: Run exact inventory and repository validation**

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_upstream_series -v
PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json
```

Expected: PASS with 15 Superpowers and 25 Matt entries, no excluded source paths, and no duplicate source identities.

- [ ] **Step 5: Commit Matt Pocock entries**

```bash
git add entries/l1/mattpocock entries/l2/mattpocock tests/directory/test_upstream_series.py
git commit -m "feat: catalog the complete matt pocock series"
```

### Task 4: Write the Two Series Recommendations

**Files:**
- Create: `recommendations/superpowers-engineering-workflow.md`
- Create: `recommendations/matt-pocock-composable-engineering.md`
- Modify: `tests/directory/test_upstream_series.py`
- Modify: `curation/topics.yaml`
- Modify: `curation/synonyms.yaml`

**Interfaces:**
- Consumes: all 40 stable entry IDs and the Markdown recommendation loader.
- Produces: two ready normalized recommendations whose skill arrays exactly cover their respective series.

- [ ] **Step 1: Add failing article coverage tests**

Load both Markdown files with `load_recommendation` and assert title, summary, `body_format`, ready status, evidence pinned to the upstream repository and observation date, and exact associated skill IDs:

```python
self.assertEqual(
    {item["id"] for item in superpowers["skills"]},
    {f"superpowers/{name}" for name in SUPERPOWERS},
)
self.assertEqual(
    {item["id"] for item in matt["skills"]},
    {f"mattpocock/{name}" for name in MATT_ENGINEERING | MATT_PRODUCTIVITY},
)
```

Assert both bodies contain headings for positioning, workflow, applicable scenarios, costs and limits, comparison, combination boundaries, and verification status.

- [ ] **Step 2: Run the article test and confirm both files are absent**

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_upstream_series.UpstreamSeriesTests.test_series_recommendations_cover_exact_inventory -v
```

Expected: ERROR or FAIL because the two recommendation files do not exist.

- [ ] **Step 3: Write the Superpowers recommendation**

Use ID `superpowers-engineering-workflow`, author `hwskill-maintainers`, topics `agent-workflow`, `software-engineering`, `testing`, and all 15 Superpowers IDs. The Markdown body must explain the design → plan → worktree → execution → TDD/debugging → verification → review → branch completion flow; distinguish user approval gates from Agent autonomy; list long-running engineering, quality-sensitive changes, and team-standard workflows as fitting scenarios; describe process overhead and harness requirements; compare with Matt Pocock; and state source/metadata verification separately from installation/behavior verification.

- [ ] **Step 4: Write the Matt Pocock recommendation**

Use ID `matt-pocock-composable-engineering`, author `hwskill-maintainers`, topics `agent-workflow`, `software-engineering`, `domain-modeling`, and all 25 Matt IDs. The body must explain user-invoked orchestration versus model-invoked discipline, the setup → grill/domain language → spec/tickets → implement/TDD → review/handoff flow, and optional triage/research/prototype/wayfinder tools. Describe flexible composition, tracker and document setup, Bash-specific wizard constraints, comparison with Superpowers, overlap handling, and verification boundaries.

- [ ] **Step 5: Add discoverable topics and synonyms**

Add these topic records:

```yaml
  - slug: agent-workflow
    title: Agent 工作流
  - slug: software-engineering
    title: 软件工程
  - slug: domain-modeling
    title: 领域建模
  - slug: testing
    title: 测试
```

Add synonyms connecting planning, TDD, requirements, domain language, Agent orchestration, and engineering workflow terms without changing existing groups.

- [ ] **Step 6: Validate and build recommendation machine data**

```bash
PYTHONPATH=src python3 -m unittest tests.directory.test_upstream_series -v
PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python3 -m hwskill.directory build --repo-root . --out /tmp/hwskill-series-directory --json
python3 -m json.tool /tmp/hwskill-series-directory/recommendations.json >/dev/null
```

Expected: PASS; the built document contains four ready recommendations and the two new items reference 15 and 25 skills respectively.

- [ ] **Step 7: Commit the articles and curation**

```bash
git add recommendations curation tests/directory/test_upstream_series.py
git commit -m "content: recommend superpowers and matt pocock skills"
```

### Task 5: Render Recommendation Markdown Safely

**Files:**
- Modify: `site/package.json`
- Modify: `site/package-lock.json`
- Create: `site/src/lib/markdown.mjs`
- Create: `site/tests/markdown.test.mjs`
- Modify: `site/src/lib/data.ts`
- Modify: `site/src/pages/recommendations/[id].astro`
- Modify: `site/src/styles/global.css`

**Interfaces:**
- Consumes: `Recommendation.body`, `Recommendation.body_format`, linked catalog entries.
- Produces: `renderMarkdown(source: string) -> string` and grouped compact skill links.

- [ ] **Step 1: Add failing renderer security and syntax tests**

Create `site/tests/markdown.test.mjs`:

```javascript
import assert from "node:assert/strict";
import test from "node:test";
import { renderMarkdown } from "../src/lib/markdown.mjs";

test("renders supported recommendation markdown", () => {
  const html = renderMarkdown("# 标题\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n```sh\necho ok\n```\n");
  assert.match(html, /<h1>标题<\/h1>/);
  assert.match(html, /<table>/);
  assert.match(html, /<code class="language-sh">/);
});

test("does not activate html or dangerous protocols", () => {
  const html = renderMarkdown('<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n[y](data:text/html,bad)\n\n[z](https://user:secret@example.com/private)');
  assert.doesNotMatch(html, /<script|javascript:|data:text\/html|user:secret/i);
});

test("marks external links safely", () => {
  const html = renderMarkdown("[upstream](https://github.com/obra/superpowers)");
  assert.match(html, /rel="noopener noreferrer"/);
});
```

- [ ] **Step 2: Run the Node test and confirm the module is absent**

```bash
cd site && node --test tests/markdown.test.mjs
```

Expected: FAIL with module-not-found for `src/lib/markdown.mjs`.

- [ ] **Step 3: Install the pinned renderer and implement one safe configuration**

```bash
cd site && npm install --save-exact markdown-it@15.0.2
```

Create a single `MarkdownIt` instance with `html: false`, `linkify: false`, and `typographer: false`. Override `validateLink` to allow only credential-free `http:`, credential-free `https:`, `mailto:`, same-site absolute paths, relative paths, and fragments; reject parsed URLs with a username or password. Override `link_open` to add `rel="noopener noreferrer"` to HTTP(S) links. Export only `renderMarkdown`.

- [ ] **Step 4: Update the recommendation TypeScript contract**

In `site/src/lib/data.ts`, add:

```typescript
summary?: string;
body_format?: "markdown";
```

Correct the existing skill reference property to `experience_version?: string` so it matches the schema. Add a helper that returns `recommendation.summary ?? recommendation.body` only for old plain-text documents.

- [ ] **Step 5: Render the article and compact linked-skill groups**

In `[id].astro`, fail if a non-withdrawn recommendation has an unknown nonempty `body_format`; render `body_format === "markdown"` through `set:html={renderMarkdown(recommendation.body)}`, and render a missing format as an ordinary escaped paragraph for old machine documents. Use `summary` for `BaseLayout` description. Group linked entries by namespace and layer, sort each group by `entry.name`, and render one compact link per referenced ID. Assert missing linked entries during static path creation so a site build cannot silently omit a skill.

- [ ] **Step 6: Add article and compact-list styles**

Replace the one-paragraph `.prose` treatment with readable heading, paragraph, list, table, pre/code, blockquote, and link styles. Add compact grouped skill lists and a mobile rule that allows long IDs and tables to scroll without widening the page.

- [ ] **Step 7: Run renderer tests and an Astro build**

```bash
cd site
node --test tests/*.test.mjs
npm run build
```

Expected: all Node tests pass and Astro builds all recommendation detail pages without unsafe markup.

- [ ] **Step 8: Commit safe Markdown rendering**

```bash
git add site/package.json site/package-lock.json site/src/lib site/src/pages/recommendations/'[id].astro' site/src/styles/global.css site/tests
git commit -m "feat: render recommendation markdown safely"
```

### Task 6: Add Recommendation Navigation, Listing, and Shared Prompt

**Files:**
- Create: `site/src/lib/contribution-prompts.ts`
- Create: `site/src/components/CopyPrompt.astro`
- Create: `site/src/pages/recommendations/index.astro`
- Modify: `site/src/pages/contribute/index.astro`
- Modify: `site/src/pages/index.astro`
- Modify: `site/src/layouts/BaseLayout.astro`
- Modify: `site/src/styles/global.css`
- Modify: `site/scripts/check-build.mjs`

**Interfaces:**
- Consumes: ready `recommendations`, `sitePath`, `Astro.site`, and the public source schema/template URLs.
- Produces: `buildContributionUrls(site: URL)` and `buildRecommendationPrompt(urls)`, plus a reusable prompt-copy component.

- [ ] **Step 1: Add failing built-site checks**

Extend `site/scripts/check-build.mjs` to require `recommendations/index.html`. After reading it, assert it contains both new recommendation titles, the recommendation source schema URL, the Markdown template URL, and the exact phrase requiring missing skills in the same Pull Request. For each ready recommendation page, collect links matching `href="[^"]*/skills/[^"]+"` and compare unique targets with the recommendation skill IDs; the 25-skill article must have 25 unique targets.

- [ ] **Step 2: Run the site build and confirm the listing check fails**

```bash
cd site && npm run build
```

Expected: FAIL because `recommendations/index.html` is missing.

- [ ] **Step 3: Extract shared contribution prompt builders**

Move repository URL, public resource URL construction, skill prompt text, and recommendation prompt text out of `contribute/index.astro`. The recommendation builder must point to `recommendation-source.schema.json` and `recommendation.md`, require missing entries in the same PR, authorize branch/commit/push/PR, deny merge authorization, and require the PR URL and verification result.

- [ ] **Step 4: Create the reusable copy component**

`CopyPrompt.astro` accepts `id`, `label`, `value`, and `rows`. It renders a readonly textarea and button. Its client script copies through `navigator.clipboard`, then falls back to focus/select and changes the button text to “请手动复制所选文本”. Preserve a selectable textarea when JavaScript or clipboard permission is unavailable.

- [ ] **Step 5: Build the recommendation listing page**

Create `/recommendations/` with a page heading, recommendation count, cards for ready items, topics, author, associated skill count, and links to details. Add a “贡献推荐” section explaining source facts, validation boundaries, Markdown maintenance, and missing-skill handling. Render the shared recommendation prompt and direct links to the Agent guide, source schema, Markdown template, contribution rules, and repository.

- [ ] **Step 6: Update navigation, homepage, and contribution page**

Add “技能推荐” after “找技能” in `BaseLayout.astro`. Limit the homepage recommendation block to a stable small slice and add “查看全部推荐”. Replace duplicated prompt code and copy scripts in the contribution page with the shared builder and component. Keep skill and recommendation prompts as separate primary entries.

- [ ] **Step 7: Add responsive list and prompt styles**

Add recommendation card metadata, contribution callout, article grid, and narrow-screen navigation styles. At 760px, cards and metadata become one column, prompts remain full width, and the four navigation links wrap without overlapping the brand.

- [ ] **Step 8: Run complete site checks**

```bash
cd site
node --test tests/*.test.mjs
npm run build
npm run index
```

Expected: Node tests pass; Astro, generated-file checks, Pagefind indexing, and search evaluation pass; recommendation list and every ready detail page are present.

- [ ] **Step 9: Commit the recommendation experience**

```bash
git add site/src site/scripts/check-build.mjs
git commit -m "feat: add skill recommendation hub"
```

### Task 7: Update Contributor Documentation and Run the Full Gate

**Files:**
- Modify: `CONTRIBUTING.md`
- Modify: `docs/guides/agent-contribution.md`
- Modify: `docs/guides/user-guide.md`
- Modify: `docs/validation/implementation-status.md`

**Interfaces:**
- Consumes: final file paths, schemas, routes, prompts, and validation commands from Tasks 1–6.
- Produces: contributor and operator documentation matching the shipped behavior.

- [ ] **Step 1: Update contribution and user documentation**

Document `recommendations/<id>.md`, required Frontmatter, supported Markdown, source versus normalized schemas, exact validation commands, the ready-reference rule, and the requirement to add missing skill entries in the same PR. Document `/recommendations/`, warning-versus-blocking behavior for unverified skills, and the stable-series exclusions. Update implementation status with 40 cataloged series entries, Markdown recommendations, and explicit “installation/behavior not run” scope.

- [ ] **Step 2: Run the complete Python test suite**

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected: all tests pass with no failures or errors; existing publishing, sharing, verification, and migration tests remain green.

- [ ] **Step 3: Run deterministic directory validation and build**

```bash
rm -rf /tmp/hwskill-final-directory
PYTHONPATH=src python3 -m hwskill.directory validate --repo-root . --json
PYTHONPATH=src python3 -m hwskill.directory build --repo-root . --out /tmp/hwskill-final-directory --json
```

Expected: both commands report pass; status reports the new entry and recommendation counts; a second build to a different output has byte-identical `catalog.json`, `recommendations.json`, and `curation.json`.

- [ ] **Step 4: Run the complete static-site gate**

```bash
cd site
node --test tests/*.test.mjs
npm run build
npm run index
```

Expected: all tests and commands exit 0; Pagefind evaluates all configured search cases; generated checks include the recommendation hub and exact linked-skill coverage.

- [ ] **Step 5: Inspect generated security and content evidence**

```bash
rg -n "<script>alert|javascript:|data:text/html" site/dist/recommendations
rg -n "Superpowers：|Matt Pocock：|贡献推荐" site/dist/recommendations/index.html
git status --short
git diff --check HEAD
```

Expected: the unsafe-pattern search returns no matches; the content search finds both titles and contribution guidance; Git status shows only intended feature files plus the pre-existing untracked `.superpowers/`; diff check emits no errors.

- [ ] **Step 6: Commit documentation and final adjustments**

```bash
git add CONTRIBUTING.md docs/guides docs/validation/implementation-status.md
git commit -m "docs: explain markdown skill recommendations"
```

- [ ] **Step 7: Perform a final branch review before any push**

```bash
git log --oneline --decorate cb9cc26..HEAD
git diff --stat cb9cc26..HEAD
git diff --name-status cb9cc26..HEAD
git status --short
```

Expected: the history is split by source contract, each upstream series, recommendation content, site rendering, recommendation hub, and documentation; `.superpowers/` remains untracked; no unrelated file is staged or committed. Do not push until the user separately authorizes or requests it.
