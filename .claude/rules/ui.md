---
paths:
  - "frontend/**"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises Notion "UI/UX Specification —
the one UI page", UI/UX Core Principles, ADR-044, ADR-056…ADR-063 and KAN-35. Verified against
main@7805816 on 2026-10-10. Fix this file in the same PR as any change to what it states;
/drift-audit re-checks it weekly. -->

# Anything a user can see

The failure these rules prevent is silent: a screen that works, passes review, and looks like
it came from a different product.

## Two binding references, in this order

1. **UI/UX Specification — the one UI page** (Notion, linked in CLAUDE.md): design tokens, the
   shell, the action bar, the data grid, detail screens, forms, component contracts and the
   screen inventory. It **wins over a module PRD on presentation** — a PRD says *what* a
   screen does, the spec says *how*.
2. **The interactive prototype** — the reference implementation, normative for presentation.
   Drive folder (path in CLAUDE.md) → `dms-platform/ui-prototype/nexotec-prototype.html`.

Where the spec page and the prototype disagree, one of them has a defect. **Stop and ask.** Do
not pick whichever is closer to hand. Where the prototype and a PRD disagree on a *rule* (not
on presentation), that is a conflict to surface, not a preference to pick.

## Using the prototype

- Open it in the desktop preview (`.claude/launch.json` → `nexotec-prototype`) and click
  through the screens you are about to build.
- **Never read the built HTML** — about 860 KB of concatenated output. Grep `src/` beside it:
  a route (`#/valuations`), a component (`NX.rowGroup`), an i18n key (`val.st.expired`).
  `HOW-TO-USE.md` in that folder is the short version.
- **A build session does not change the prototype.** It is maintained from the spec side; if
  it looks wrong, that is a spec question for Anto. Only when Anto asks: edit `src/` and run
  `python3 build.py` in the prototype folder — never the built file (a hook blocks it).

## Components come from the UI kit

Grids, filter controls, detail-screen headers, pickers, form dialogs and document renderers
come from WP-6c, `frontend/packages/ui-kit` (`datagrid/`, `detail/`, `shell/`, `wizard/`,
`components/`, tokens in `tokens.css`/`tokens.ts`). If you are writing one inside a module
package you are in the wrong package. If the kit cannot do what you need, that is a ui-kit
change plus an ADR — never a local component. No colour outside the tokens.

## The rulings that bite most often

- **ADR-056** — grid state (search, filter, sort, tab, scope) lives in the URL; the URL is the
  shareable unit. Layout and density stay out of it: they belong on the user preference record.
- **ADR-058** — views and filters are **one control**, labelled with the current view. A
  user-defined filter is **one predicate** (field, operator, value), never a typed expression.
  Every filter is a chip (FR-UI-06).
- **ADR-059** — opening a record from inside a process renders it as an **overlay**, not a
  navigation. Losing a half-built offer is a defect. The naive version (set the hash, set it
  back) fires the router and destroys the screen underneath — the bug the component prevents.
- **ADR-060** — every persisted field is available as a grid column; a documented subset is
  visible by default. A stored field that cannot be put on screen is a defect.
- **ADR-061** — every detail screen: **one primary action, one alternative, and an overflow
  carrying the entity's full row menu** — same items, same order, same
  disabled-with-explanation entries. The row menu is the single definition of what can be done
  to an entity; list and detail both render from it.
- **ADR-063** — generating an offer is **two steps**: build, then review the rendered document
  in the customer's correspondence language with the seller-only margin panel *beside* it,
  never on it.
- Grids (UI/UX Core Principles): every column sortable server-side; column order, visibility,
  width and pinning configurable and persisted per user; a three-dots row menu on every row;
  cursor-based lazy loading; maximum information density; Shift + wheel scrolls horizontally.
- The dashboard is the landing page (FR-UI-07) and should be the breadcrumb root — not built:
  breadcrumbs start at the nav group and are not links (KAN-104). Account chrome lives only in
  the sidebar footer; the top bar carries the breadcrumb.

## i18n

- Bundles: `frontend/apps/dms/src/i18n/locales/{de,en,fr,it}.json`. A new key goes into all
  four in the same change; `localeKeyParity.test.ts` fails otherwise.
- A key missing from every bundle renders `⚠ MISSING I18N KEY: <key>`; one missing only from
  the active locale falls back to English (`fallbackLng: 'en'`), never German —
  `localeKeyParity.test.ts` keeps that from shipping. No hardcoded user-visible string,
  including labels in dialogs and wizards.
- The customer's **correspondence language** is not the user's **UI language** — never the
  same control, never the same stored field.
- Canonical reference data is translated by us; provider option text is stored and rendered
  **as delivered**, never translated (ADR-044).
- New users should default to the dealership's language — not built: `useUiPreferences.ts`
  hard-defaults `uiLanguage` to `'de'` (KAN-104).

## Tests and evidence

- Render-level tests use jsdom + @testing-library. The default Vitest environment is `node`;
  a file opts in with `// @vitest-environment jsdom`. Find a component's render tests with
  `git grep -l <Component> -- '*.render.test.tsx'` (they are not always siblings) before
  assuming it can only be checked by eye; a change to a component that has one updates it in
  the same PR.
- Every visible change ends with a screenshot of the changed screen, taken in the desktop
  preview after the last code change. The hand-over gate checks for it.

## API types (KAN-35)

`src/api/schema.d.ts` is generated from the backend's own `app.openapi()` and diffed in CI
(`frontend-openapi-drift`); editing it is blocked. `src/api/types.ts` derives every
request/response type from it, except the documented FRONTEND-ONLY `ApiErrorBody` (the error
envelope has no OpenAPI schema). A shape the backend under-types (a `str` whose real values are
a literal union) is a `NARROWED` override in `types.ts`, commented as such — never a
hand-written interface. After a backend schema change: `make generate-frontend-types`, then
commit the result.
