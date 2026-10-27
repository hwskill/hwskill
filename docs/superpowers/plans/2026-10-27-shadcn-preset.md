# shadcn preset integration implementation plan

**Goal:** Integrate official shadcn/ui preset b7ClMyaTC without changing catalog contracts, translations or contribution behavior.

**Architecture:** Astro renders React UI primitives at build time; Tailwind and semantic tokens style the site. Native browser scripts retain search and clipboard behavior.

**Spec:** docs/superpowers/specs/2026-10-27-shadcn-preset-design.md

## Completed implementation

- [x] Configure Astro React integration, Tailwind and aliases; initialize Base UI preset and six official UI primitives.
- [x] Apply the preset palette, local-first Chinese font stack and self-hosted font fallback.
- [x] Migrate cards, buttons, search controls and contribution fields while preserving IDs, labels, Pagefind metadata and event handlers.
- [x] Unify layout and article styles; implement system/default and optional manual theme selection.
- [x] Run supported Node 22.19 toolchain tests, Python suite, build, Pagefind and browser checks.

The related card, ordering and theme interaction checklist is in `2026-10-27-ui-consistency.md`; final evidence is in `docs/validation/2026-10-27-ui-consistency.md`.
