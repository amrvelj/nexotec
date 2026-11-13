---
paths:
  - "frontend/apps/dms/src/App.tsx"
  - "frontend/apps/dms/src/pages/**"
  - "frontend/apps/dms/src/components/**"
  - "frontend/apps/dms/src/modules/**"
  - "frontend/apps/dms/src/layout/**"
  - "frontend/apps/dms/src/api/**"
  - "frontend/packages/ui-kit/src/**"
  - "frontend/*"
  - "frontend/apps/dms/*"
  - "frontend/packages/ui-kit/*"
  - "frontend/apps/dms/public/favicon.svg"
  - "frontend/apps/dms/src/*.css"
  - "frontend/apps/dms/src/*.d.ts"
  - "frontend/apps/dms/src/assets/**"
  - "frontend/apps/dms/src/hooks/useIdempotencyKey*"
---
<!-- Maintainer note. UI/UX Specification (the one UI page), UI/UX Core Principles, ADR-076, KAN-35.
Split by KAN-615 (2026-11-13) into this file (what binds every screen) and ui-grid, ui-forms,
ui-detail, ui-i18n, ui-formats, ui-shell, each with narrow globs. Last verified against main@73baf3f
(2026-10-25); /drift-audit re-checks it weekly. -->

# Anything a user can see

The failure these rules prevent is silent: a screen that works, passes review, and looks like it
came from a different product. The concern files: `ui-grid.md` · `ui-forms.md` · `ui-detail.md` ·
`ui-i18n.md` · `ui-formats.md` · `ui-shell.md`.

## Two binding references, in this order

1. **UI/UX Specification — the one UI page** (Notion, linked in CLAUDE.md): tokens, shell, action
   bar, data grid, detail screens, forms, component contracts, screen inventory. It **wins over a
   module PRD on presentation** — a PRD says *what* a screen does, the spec says *how*.
2. **The interactive prototype** — the reference implementation, normative for presentation.
   Drive folder (path in CLAUDE.md) → `dms-platform/ui-prototype/nexotec-prototype.html`.

Where the spec page and the prototype disagree, one of them has a defect: **stop and ask**, never
pick whichever is closer to hand. Where the prototype and a PRD disagree on a *rule* (not on
presentation), that is a conflict to surface, not a preference to pick.

- Open the prototype in the desktop preview (`.claude/launch.json` → `nexotec-prototype`) and click
  through the screens you are about to build. **Never read the built HTML** (~860 KB): grep `src/`
  beside it — a route (`#/valuations`), a component (`NX.rowGroup`), an i18n key. `HOW-TO-USE.md`
  in that folder is the short version.
- **A build session does not change the prototype.** It is maintained from the spec side; if it
  looks wrong, that is a spec question for Anto. Only when Anto asks: edit `src/` and run
  `python3 build.py` there — never the built file (a hook blocks it).

## Components come from the UI kit

Grids, filter controls, detail headers, pickers, form dialogs and document renderers come from
`frontend/packages/ui-kit` (`datagrid/`, `detail/`, `shell/`, `wizard/`, `components/`, `format/`;
tokens in `tokens.css`/`tokens.ts`). Writing one inside a module package means you are in the wrong
package; a need the kit cannot meet is a ui-kit change plus an ADR — never a local component. No
colour outside the tokens. A kit component ships no user-visible string of its own (`ui-i18n.md`).
Money is `CHF 12'500.00` through the kit's `ChfInput`, a date-only value renders through
`formatDateOnly` (`ui-formats.md`; architecture tests enforce both).

## Where the code lives (ADR-161)

A module with a folder, `src/modules/<module>/`, keeps every screen, component, hook and helper of
its own there, never in `src/pages/`, `components/` or `hooks/`; other code reaches it only through
its `index.ts` (the router's lazy pages and tests excepted; `architecture/module-boundaries.test.ts`).

## Evidence and API types

- Every visible change ends with a screenshot of the changed screen, taken after the last code
  change — in the desktop preview, or in a cloud session a Playwright capture of localhost; any
  other capture is registered with `scripts/dev/gate evidence screenshot <file>`. The hand-over gate
  checks for one of this exact tree. Render tests: `tests-frontend.md`.
- `src/api/schema.d.ts` is generated from `app.openapi()` and diffed in CI; editing it is blocked.
  `src/api/types.ts` derives every type from it, except the FRONTEND-ONLY `ApiErrorBody`. A shape
  the backend under-types is a commented `NARROWED` override in `types.ts`, never a hand-written
  interface. After a backend schema change: `make generate-frontend-types`, then commit the result.
