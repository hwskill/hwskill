# UI consistency implementation plan

> Completed implementation checklist; integration is reviewed through the source-repository PR.

**Goal:** Unify page/card structure, add accessible theme selection, and order content newest publication first.

**Architecture:** Shared Astro cards and static React primitives; site-only publication metadata; a small native browser theme controller with first-paint restoration.

**Spec:** docs/superpowers/specs/2026-10-27-ui-consistency-design.md

## Global constraints

- Preserve current form, translations, search relevance, routes and Pagefind metadata.
- Keep preset palette, Public Sans and Chinese fallback policy.
- Default system; explicit preference wins until system is selected again.
- Sort first publication descending, unknown last; never use filesystem mtime.

## Review focus

- Missing Git history and date ties must produce stable honest output.
- Keyword relevance must survive date ordering.
- Manual light under dark OS, manual dark under light OS and blocked storage must work.
- Native theme menu and cards must remain accessible at 320px.
- Form fields/defaults and exact related-skill link sets must survive layout changes.

### Task 1: Publication metadata and tests
- [x] Write/run failing date/parser/sort tests.
- [x] Implement history reader, current-date snapshot and stable sort; integrate site data.
- [x] Select keyword candidates by relevance, then present all content lists chronologically.

### Task 2: Theme preference and tests
- [x] Write/run failing preference/storage tests.
- [x] Add first-paint restoration and header popover with light/dark/system options.
- [x] Activate palette and Tailwind dark variants from effective theme with no-JS system fallback.

### Task 3: Shared layout and cards
- [x] Reuse recommendation card on every surface and align skill cards.
- [x] Unify heading spacing, metadata, related sections, form layout and small-screen header.
- [x] Show publication dates in cards and article metadata.

### Task 4: Verification and review
- [x] Run Node/Python suites, build, Pagefind cases and browser layout/interaction checks.
- [x] Independently review the changes, resolve actionable regressions, document evidence.
