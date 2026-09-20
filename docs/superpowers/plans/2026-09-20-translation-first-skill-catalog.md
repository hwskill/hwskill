# Translation First Skill Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate HWSkill to Entry and Catalog v2, publish reviewed Chinese translations as each skill page's primary body, remove durable verification state, require a PR validation report, and keep recommendations searchable.

**Architecture:** Treat Entry YAML and reviewed translation Markdown as the public source of truth. Build a self-contained Catalog v2 without resolving upstream revisions. Keep deterministic checks in the public repository, while a separate private validator owns installation and behavioral checks and writes only a PR Check.

**Tech Stack:** Python 3.10+, PyYAML 6, jsonschema 4, Astro 7, Node 22.19, markdown-it 15, Pagefind 1.5, unittest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-20-translation-first-skill-catalog-design.md`

## Global Constraints

- An external Git locator may omit `ref` or specify a branch, tag, or commit. Never resolve or persist an effective commit in Catalog v2.
- Do not publish `source_identity`, `verification_summary`, derived `install_capability`, or validation reports in generated catalog, feed, or site data.
- Every ready skill has exactly one `translations/<namespace>/<name>.md` file. Builds remain offline and never fetch or translate upstream text.
- A detail page labels the body as a Chinese translation and links to the exact upstream `SKILL.md`; an omitted ref uses GitHub `blob/HEAD`.
- Preserve useful metadata: scope, recommended usage, examples, compatibility, dependencies, license, limitations, lifecycle, and declared installation instructions.
- Keep Catalog v1 fixtures readable as historical data. New builds and feeds emit v2 only.
- Do not remove the public verification package until the private validator has passed an end-to-end dry run and can write the required Check.
- Do not stage or modify the existing untracked `.superpowers/` directory.

## Review Focus

- Optional refs must work for default branch, branch, tag, and commit values without allowing option injection, traversal, control characters, or malformed source URLs.
- A ready Entry without a matching, licensed translation must fail deterministically; a draft can remain unpublished while authorization is unresolved.
- Markdown must render headings, lists, tables, code, and links while disabling raw HTML and unsafe URL protocols.
- The site must contain no verification badge, verification filter, resolved revision, version pin promise, or durable validation status.
- Recommendation results must open from site search and associated recommendation links must remain valid.
- PR report validation must read the GitHub event JSON directly and must never interpolate an untrusted PR body into a shell command.
- Historical v1 parsing and new v2 generation require separate fixtures so compatibility code cannot silently reintroduce removed fields.

---

## Human Readiness Gate

These inputs cannot be created or authoritatively approved by an Agent. Prepare them before the dependent task starts.

| ID | Human-provided item | Owner | Needed before | Acceptance evidence |
|---|---|---|---|---|
| H1 | Confirm translation and redistribution rights for `local/gitcode-discussion-fetch` and `local/gitcode-pr-review-fetch`, whose current license status is unknown | Repository owner | Task 5 | License identifier or written permission recorded in each Entry and its PR |
| H2 | Approve the ref migration ledger: keep a branch, tag, or commit where intended, or omit `ref` to follow the default branch | Maintainer | Task 4 | Reviewed `docs/migrations/catalog-v2-source-refs.csv` committed with one decision per external skill |
| H3 | Review Chinese translations for semantic accuracy, preserved warnings, code blocks, and links | Bilingual maintainer or upstream owner | Tasks 5-7 | PR review approval covering every changed translation file |
| H4 | Approve the one-release Catalog v2 cutover and notify any known feed consumers | Maintainer | Task 10 | Release note approval and consumer acknowledgement, if consumers exist |
| H5 | Complete private validator readiness items PV1-PV7 and its end-to-end dry run from the companion plan | Organization administrator and maintainer | Task 10 | Successful private validation Check on a test PR |
| H6 | Add `hwskill-validation` to branch protection after the end-to-end test | Organization administrator | Task 10 | Main branch rule shows the exact required Check name |

Agent work that can be prepared immediately: generate the source-ref ledger, draft all translations, verify file/link/code-block coverage, implement schemas and UI, and prepare the branch-protection runbook. H1-H3 remain human judgments; absence of approval keeps affected Entries in draft.

## Delivery Order

1. Complete Tasks 1-4 in this repository.
2. Draft Tasks 5-7 while humans review H1-H3.
3. Complete Tasks 8-9 and private validator Tasks 1-7 in parallel.
4. Run Task 10 only after H5, then obtain H6 before merging the cutover.
5. Complete Task 11 and release Catalog v2 and the site from the same commit.

---

### Task 1: Add the Translation Source Contract

**Files:**
- Create: `src/hwskill/directory/translations.py`
- Create: `schemas/translation-source.schema.json`
- Create: `src/hwskill/directory/schemas/translation-source.schema.json`
- Modify: `src/hwskill/directory/schema.py`
- Modify: `src/hwskill/directory/entries.py`
- Modify: `tests/directory/test_entries.py`
- Modify: `tests/directory/test_packaging.py`

**Interfaces:**
- `load_translation(path: Path) -> dict[str, Any]` returns validated Frontmatter plus normalized `body` and `body_format: "markdown"`.
- `translation_path(root: Path, skill_id: str) -> Path` maps `namespace/name` to the only permitted Markdown path.
- `TranslationContractError(code: str, field: str, message: str)` becomes a stable `DirectoryIssue`.

- [ ] **Step 1: Write failing contract tests** for CRLF input, missing delimiters, duplicate keys, mismatched `skill_id`, invalid date, empty body, extra Frontmatter fields, and missing translation for a ready Entry.

```python
def test_translation_requires_matching_skill_id(self) -> None:
    path = self.root / "translations" / "local" / "hosted.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nschema_version: 1\nskill_id: local/other\n"
        "translated_at: 2026-09-20\n---\n\n# 译文\n",
        encoding="utf-8",
    )
    issues = validate_repository(self.root)
    self.assertIn("translation-path-mismatch", {issue.code for issue in issues})
```

- [ ] **Step 2: Run the focused tests and confirm failure.**

```bash
PYTHONPATH=src:. python -m unittest tests.directory.test_entries -v
```

Expected: FAIL because the translation loader and schema do not exist.

- [ ] **Step 3: Implement strict parsing.** Reuse `load_yaml_text()` from `directory/yaml_io.py`. Normalize line endings, require Frontmatter on line 1, require a closing delimiter, reject whitespace-only bodies, and append one final newline.

- [ ] **Step 4: Add the source schema.** Require only `schema_version: 1`, `skill_id`, and ISO `translated_at`; set `additionalProperties: false`. Register and package both schema copies.

- [ ] **Step 5: Integrate repository validation.** Discover `translations/**/*.md`, reject orphans and duplicates, enforce ID/path equality, and require a translation for every publishable Entry.

- [ ] **Step 6: Run focused and packaging tests.**

```bash
PYTHONPATH=src:. python -m unittest tests.directory.test_entries tests.directory.test_packaging -v
```

Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add schemas/translation-source.schema.json src/hwskill/directory tests/directory
git commit -m "feat: add skill translation contract"
```

---

### Task 2: Introduce Entry and Catalog v2

**Files:**
- Modify: `schemas/entry.schema.json`
- Modify: `src/hwskill/directory/schemas/entry.schema.json`
- Modify: `schemas/catalog.schema.json`
- Modify: `src/hwskill/directory/schemas/catalog.schema.json`
- Modify: `schemas/release.schema.json`
- Modify: `src/hwskill/directory/schemas/release.schema.json`
- Modify: `src/hwskill/directory/entries.py`
- Modify: `src/hwskill/directory/catalog.py`
- Modify: `src/hwskill/directory/models.py`
- Modify: `tests/directory/test_build.py`
- Modify: `tests/directory/test_entries.py`

**Interfaces:**
- External Git locator accepts `repository`, `path`, optional `ref`, and optional `file_url` for a non-GitHub adapter.
- `build_source_url(entry: dict[str, Any]) -> str` returns the skill file URL without network access.
- Catalog v2 item contains `entry`, `entry_digest`, `lifecycle`, and `translation` only.

- [ ] **Step 1: Add failing schema tests** proving omitted, branch, tag, and commit refs are accepted and unsafe refs are rejected. Add a Catalog assertion that removed keys are absent.

```python
self.assertNotIn("source_identity", item)
self.assertNotIn("verification_summary", item)
self.assertNotIn("install_capability", item)
self.assertEqual(item["translation"]["body_format"], "markdown")
```

- [ ] **Step 2: Run the tests and confirm current v1 output fails.**

```bash
PYTHONPATH=src:. python -m unittest tests.directory.test_entries tests.directory.test_build -v
```

- [ ] **Step 3: Change Entry v2.** Rename `requested_ref` to optional `ref`; retain declared installation metadata and real limitations; remove fields that exist only to express verification state.

- [ ] **Step 4: Add deterministic original-file URLs.** GitHub uses `https://github.com/<owner>/<repo>/blob/<ref-or-HEAD>/<path>/SKILL.md`. Encode path segments, reject traversal, and use `file_url` when an adapter cannot derive a URL.

- [ ] **Step 5: Change Catalog and release generation.** Set `schema_version` to 2, load the matching translation, calculate only the Entry digest needed for repository consistency, and stop generating verification, resolved revision, capability, or external content digest values.

- [ ] **Step 6: Simplify generated install artifacts.** `install.md` and `install.json` describe the declared source, optional ref, complete package, destination scope, and Agent checks. Remove version-verification and platform-verification language. `status.json` contains lifecycle only.

- [ ] **Step 7: Run directory tests and inspect a fixture build.**

```bash
PYTHONPATH=src:. python -m unittest tests.directory -v
tmp_dir="$(mktemp -d)"
PYTHONPATH=src python -m hwskill.directory build --repo-root . --output "$tmp_dir"
python -m json.tool "$tmp_dir/catalog.json" >/dev/null
rm -rf "$tmp_dir"
```

Expected: tests pass and generated JSON contains none of the removed keys.

- [ ] **Step 8: Commit.**

```bash
git add schemas src/hwskill/directory tests/directory
git commit -m "feat: generate translation first catalog v2"
```

---

### Task 3: Preserve Historical v1 Reads and Emit v2 Feeds

**Files:**
- Create: `src/hwskill/sharing/v1.py`
- Create: `src/hwskill/sharing/v2.py`
- Modify: `src/hwskill/sharing/validation.py`
- Modify: `src/hwskill/sharing/models.py`
- Modify: `src/hwskill/sharing/filtering.py`
- Modify: `src/hwskill/publishing/models.py`
- Modify: `src/hwskill/publishing/events.py`
- Modify: `tests/sharing/fixtures/valid-snapshot.json`
- Create: `tests/sharing/fixtures/valid-v2-snapshot.json`
- Modify: `tests/sharing/test_validation.py`
- Modify: `tests/sharing/test_filtering.py`
- Modify: `tests/publishing/test_release.py`

**Interfaces:**
- `validate_snapshot(document: Mapping[str, Any]) -> SnapshotVersion` dispatches by schema version.
- v1 remains parse-only. `publish_release()` accepts and emits Catalog v2.
- v2 filters use lifecycle and metadata; no filter may depend on verification or resolved revision.

- [ ] **Step 1: Add a frozen v1 compatibility test and a v2 output test.** Assert v1 fixture reads unchanged and all new publish events use schema version 2 without removed keys.
- [ ] **Step 2: Run sharing and publishing tests; confirm v2 fails.**
- [ ] **Step 3: Move the current v1 validation rules into `v1.py`; implement the smaller v2 rules in `v2.py`; dispatch only on integer versions 1 and 2.**
- [ ] **Step 4: Remove verification-based filtering and event payload fields from active v2 models while retaining v1 decoding.**
- [ ] **Step 5: Run tests.**

```bash
PYTHONPATH=src:. python -m unittest tests.sharing tests.publishing -v
```

- [ ] **Step 6: Commit.**

```bash
git add src/hwskill/sharing src/hwskill/publishing tests/sharing tests/publishing
git commit -m "feat: publish catalog v2 feeds"
```

---

### Task 4: Produce and Approve the Source Ref Migration Ledger

**Files:**
- Create: `scripts/migration/catalog_v2_source_refs.py`
- Create: `docs/migrations/catalog-v2-source-refs.csv`
- Create: `tests/migration/test_catalog_v2_source_refs.py`
- Modify: all `entries/**/*.yaml`
- Modify: `tests/directory/test_upstream_series.py`

**Interfaces:**
- Script output columns: `skill_id,repository,path,current_requested_ref,proposed_ref,decision,reason`.
- `decision` is one of `omit`, `branch`, `tag`, `commit`; an empty `proposed_ref` is valid only for `omit`.

- [ ] **Step 1: Test deterministic ordering, one row per external Git Entry, allowed decisions, and no Entry mutation without an approved row.**
- [ ] **Step 2: Generate the ledger from the current 42 external Entries.** Default proposal is `omit`; propose retaining a ref only when a non-default branch, release tag, or exact historical source is part of the Entry's meaning.
- [ ] **Step 3: Pause this task at H2.** Human reviews every row and commits the accepted decisions. This approval cannot be inferred by an Agent.
- [ ] **Step 4: Apply the approved ledger.** Rename `requested_ref` to `ref` only for `branch`, `tag`, and `commit` decisions; omit it otherwise. Update immutable-ref assertions in the series test to assert the approved locator values instead.
- [ ] **Step 5: Remove limitations that merely say installation or behavior was not run; retain genuine environment, account, hardware, and usage limits.**
- [ ] **Step 6: Validate and commit.**

```bash
PYTHONPATH=src:. python -m unittest tests.migration.test_catalog_v2_source_refs tests.directory.test_upstream_series -v
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
git add scripts/migration docs/migrations entries tests/migration tests/directory/test_upstream_series.py
git commit -m "data: migrate source refs to catalog v2"
```

---

### Task 5: Translate Hosted and Existing Non-Series Skills

**Files:**
- Create: `translations/local/gitcode-discussion-fetch.md`
- Create: `translations/local/gitcode-pr-review-fetch.md`
- Create: translation files for every ready non-series external Entry reported by `hwskill-directory validate`
- Modify: affected `entries/**/*.yaml`
- Create: `tests/directory/test_translation_inventory.py`

**Interfaces:**
- Inventory test requires `translation.skill_id == entry.id`, a valid date, nonempty body, preserved fenced-code balance, and exactly one source URL generated by the Entry.

- [ ] **Step 1: Add the inventory test and confirm all ready Entries without translations fail.**
- [ ] **Step 2: Resolve H1.** If rights remain unresolved, set the two hosted Entries to draft and do not publish their full translations.
- [ ] **Step 3: Draft faithful Chinese translations.** Preserve heading hierarchy, warnings, commands, placeholders, code fences, link destinations, and normative terms. Do not add installation claims absent from the original.
- [ ] **Step 4: Obtain H3 review for these files and apply review corrections.**
- [ ] **Step 5: Run validation and commit.**

```bash
PYTHONPATH=src:. python -m unittest tests.directory.test_translation_inventory -v
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
git add translations entries tests/directory/test_translation_inventory.py
git commit -m "content: add initial skill translations"
```

---

### Task 6: Translate the Superpowers Series

**Files:**
- Create: `translations/superpowers/*.md` for all 15 series Entries
- Modify: `recommendations/superpowers-engineering-workflow.md`
- Modify: `tests/directory/test_translation_inventory.py`

- [ ] **Step 1: Add the exact 15-file translation inventory assertion.**
- [ ] **Step 2: Fetch each current source chosen by H2 for drafting only; record the fetched commit in the PR validation report, never in Catalog data.**
- [ ] **Step 3: Translate all source sections, code blocks, cross-skill references, and mandatory workflow language.**
- [ ] **Step 4: Update recommendation wording to remove platform verification claims and link to translated detail pages.**
- [ ] **Step 5: Obtain H3 review, run inventory and directory tests, and commit.**

```bash
PYTHONPATH=src:. python -m unittest tests.directory.test_translation_inventory tests.directory.test_upstream_series -v
git add translations/superpowers recommendations/superpowers-engineering-workflow.md tests/directory/test_translation_inventory.py
git commit -m "content: translate superpowers skill series"
```

---

### Task 7: Translate the Matt Pocock Series

**Files:**
- Create: `translations/mattpocock/*.md` for all 25 stable series Entries
- Modify: `recommendations/matt-pocock-composable-engineering.md`
- Modify: `tests/directory/test_translation_inventory.py`

- [ ] **Step 1: Add the exact 25-file translation inventory assertion and retain exclusions for `in-progress`, `misc`, and `deprecated`.**
- [ ] **Step 2: Fetch each current source chosen by H2 for drafting only and record the observed commit only in the PR report.**
- [ ] **Step 3: Translate the complete text while preserving examples, TypeScript syntax, links, warnings, and composition guidance.**
- [ ] **Step 4: Update recommendation wording to describe differences, focus, and scenarios without durable verification claims.**
- [ ] **Step 5: Obtain H3 review, run tests, and commit.**

```bash
PYTHONPATH=src:. python -m unittest tests.directory.test_translation_inventory tests.directory.test_upstream_series -v
git add translations/mattpocock recommendations/matt-pocock-composable-engineering.md tests/directory/test_translation_inventory.py
git commit -m "content: translate matt pocock skill series"
```

---

### Task 8: Rebuild Skill Pages Around Metadata and Translation

**Files:**
- Modify: `site/src/lib/data.ts`
- Modify: `site/src/components/SkillCard.astro`
- Modify: `site/src/components/SearchFilters.astro`
- Modify: `site/src/components/InstallPrompt.astro`
- Delete: `site/src/components/VerificationMatrix.astro`
- Modify: `site/src/pages/skills/[namespace]/[name].astro`
- Modify: `site/src/pages/skills/index.astro`
- Modify: `site/src/lib/markdown.mjs`
- Create: `site/tests/translation.test.mjs`
- Modify: `site/scripts/check-build.mjs`
- Modify: `site/src/styles/global.css`

**Interfaces:**
- `CatalogItem.translation` exposes `body`, `body_format`, `translated_at`, and `source_url`.
- `renderMarkdown(markdown: string) -> string` remains the only HTML renderer for repository Markdown.
- Install prompt includes declared source and optional ref and asks the Agent to inspect, install, and report actual results.

- [ ] **Step 1: Add failing renderer and static-site tests.** Assert the translation notice appears before rendered body, “查看原文” points to `source_url`, metadata remains visible, and removed verification strings and controls do not occur.
- [ ] **Step 2: Run tests and confirm current v1 components fail.**

```bash
node --test site/tests/*.test.mjs
PYTHONPATH=src:. python -m unittest tests.site.test_static_site -v
```

- [ ] **Step 3: Replace the TypeScript data contract and helpers.** Delete verification label/state helpers and make translation required for published items.
- [ ] **Step 4: Simplify cards and filters.** Remove status dots and the verification filter. Keep use, layer, Agent, source, lifecycle, and title/summary search data.
- [ ] **Step 5: Rebuild detail layout.** Render metadata, declared install prompt, then the fixed notice: `本文为技能原文的中文译文，可能滞后于上游内容，请以原文为准。`, original link, date, and safe Markdown body.
- [ ] **Step 6: Harden Markdown tests.** Assert raw HTML, `javascript:`, `data:`, and credential-bearing links never become active markup; assert tables and fenced code render.
- [ ] **Step 7: Build and commit.**

```bash
npm --prefix site run prebuild
npm --prefix site run build
node --test site/tests/*.test.mjs
git add site
git commit -m "feat: make translations primary skill content"
```

---

### Task 9: Index Recommendations and Enforce the PR Report

**Files:**
- Modify: `site/src/layouts/BaseLayout.astro`
- Modify: `site/src/pages/recommendations/[id].astro`
- Modify: `site/src/pages/skills/index.astro`
- Modify: `site/scripts/evaluate-search.mjs`
- Create: `scripts/ci/validate_pr_report.py`
- Create: `tests/ci/test_validate_pr_report.py`
- Modify: `.github/PULL_REQUEST_TEMPLATE.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/guides/agent-contribution.md`

**Interfaces:**
- Search result model is `{kind: "skill" | "recommendation", title, summary, url, metadata}`.
- `validate_pr_report(event_path: Path, changed_paths: Sequence[str]) -> list[str]` returns stable missing-field errors.
- Required headings are exactly the nine fields defined in the design's “技能验证报告”.

- [ ] **Step 1: Add failing tests.** Cover recommendation discovery, mixed result rendering, no-result behavior, docs-only PR exemption, Entry/translation change requiring a report, empty fields, renamed heading, and multiline values.
- [ ] **Step 2: Add `kind` to Pagefind metadata and render generic recommendation results instead of cloning a skill-only card.** Update search evaluation cases to expect recommendation URLs.
- [ ] **Step 3: Implement event-safe PR report validation.** Read `GITHUB_EVENT_PATH` JSON in Python, obtain the PR body from `pull_request.body`, and compare changed paths supplied as arguments. Never pass the body through shell expansion.
- [ ] **Step 4: Update the PR template and contribution guide with the exact report.** Explain that the fetched commit documents only that PR run.
- [ ] **Step 5: Add the deterministic check to `ci.yml`.** On pull requests, obtain changed paths with `git diff --name-only "$BASE_SHA" "$HEAD_SHA"` into a newline-delimited file and pass file paths to the Python checker; push builds skip the PR-body rule.
- [ ] **Step 6: Run tests, build, and commit.**

```bash
PYTHONPATH=src:. python -m unittest tests.ci.test_validate_pr_report -v
npm --prefix site run build
npm --prefix site run index
git add site scripts/ci tests/ci .github CONTRIBUTING.md docs/guides/agent-contribution.md
git commit -m "feat: index recommendations and require validation reports"
```

---

### Task 10: Cut Over to the Private Validator and Remove Public Verification State

**Files:**
- Modify: `pyproject.toml`
- Delete: `src/hwskill/verification/`
- Delete: `tests/verification/`
- Delete: `schemas/verification.schema.json`
- Delete: `src/hwskill/directory/schemas/verification.schema.json`
- Delete: public verification scripts under `scripts/verification/`, if present
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: validation-related docs found by the audit command

**Prerequisites:** H5 is complete; a private dry run produced `hwskill-validation` for the exact test PR head SHA. H6 is performed after merge readiness, before the cutover PR is merged.

- [ ] **Step 1: Capture the public verification surface.**

```bash
rg -n "hwskill-verify|verification_summary|resolved_revision|install_capability|VerificationMatrix|核验|已验证|待验证" . \
  --glob '!site/dist/**' --glob '!docs/superpowers/specs/**' --glob '!docs/superpowers/plans/**'
```

- [ ] **Step 2: Remove the `hwskill-verify` entry point, package, tests, schemas, and obsolete scripts.** Change the package description to `Skill directory, publishing, and update-sharing tools`.
- [ ] **Step 3: Remove remaining active documentation and CI references.** Historical v1 fixtures and migration notes may retain field names only where labeled as historical compatibility.
- [ ] **Step 4: Run the full public suite before branch protection changes.**

```bash
PYTHONPATH=src:. python -m unittest discover -s tests -t . -p 'test_*.py'
npm --prefix site run build
npm --prefix site run index
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
```

- [ ] **Step 5: Ask the organization administrator to complete H6.** Verify the exact required Check is `hwskill-validation`; do not guess or silently continue if it differs.
- [ ] **Step 6: Commit.**

```bash
git add -A
git commit -m "refactor: move skill validation out of catalog"
```

---

### Task 11: Final Migration Audit and Release Evidence

**Files:**
- Create: `docs/releases/catalog-v2-migration.md`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `site/scripts/check-build.mjs`
- Modify: any file identified by the final active-field audit

- [ ] **Step 1: Add final build assertions.** Require 44 publishable Entries or the explicitly documented smaller count caused by unresolved H1, one translation per publishable Entry, all original links, four recommendations, and no removed fields in current generated artifacts.
- [ ] **Step 2: Document the cutover.** Record breaking schema changes, historical v1 read support, optional ref semantics, translation review rules, PR report scope, and the private Check workflow. Do not publish secrets, App identifiers that should stay private, validation logs, or model prompts.
- [ ] **Step 3: Run the complete verification set from a clean generated-data state.**

```bash
rm -rf site/src/generated site/dist site/.pagefind
PYTHONPATH=src:. python -m unittest discover -s tests -t . -p 'test_*.py'
npm ci --prefix site
npm --prefix site run prebuild
npm --prefix site run build
npm --prefix site run index
PYTHONPATH=src python -m hwskill.directory validate --repo-root . --json
git status --short
```

Expected: every command passes; only intended source files are tracked; `.superpowers/` remains untouched and untracked.

- [ ] **Step 4: Inspect generated output manually.** Open one hosted skill, one Superpowers skill, one Matt Pocock skill, both series recommendations, and a mixed search query. Verify metadata, prompt, notice, original link, translation, and narrow-screen layout.
- [ ] **Step 5: Obtain H4 release approval and record H3 approvals in the PR review history.**
- [ ] **Step 6: Commit final docs and assertions.**

```bash
git add README.md CONTRIBUTING.md docs/releases/catalog-v2-migration.md site/scripts/check-build.mjs
git commit -m "docs: document catalog v2 migration"
```

## Completion Criteria

- Catalog, release, feed, and site are generated as v2 from the same commit.
- Every published skill has a human-approved Chinese translation and a concrete original-file link.
- Current public data contains no durable source-resolution, install-verification, or behavior-verification status.
- Contribution PRs changing Entries or translations cannot pass deterministic CI without a complete validation report.
- The private validator writes the exact required Check and the public repository contains none of its credentials or execution code.
- Human items H1-H6 have evidence; unresolved authorization keeps only the affected skill in draft.
