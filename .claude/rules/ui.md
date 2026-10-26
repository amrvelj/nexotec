---
paths:
  - "frontend/**"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises Notion "UI/UX Specification —
the one UI page", UI/UX Core Principles, ADR-044, ADR-056…ADR-063, ADR-076, ADR-084, ADR-092 and
KAN-35. Verified against main@73baf3f on 2026-10-25. Fix this file in the same PR as any change to what
it states; /drift-audit re-checks it weekly. -->

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
`components/`, `format/` — `ChfInput`, the Swiss number format —, tokens in `tokens.css`/`tokens.ts`). If you are writing one inside a module
package you are in the wrong package. If the kit cannot do what you need, that is a ui-kit
change plus an ADR — never a local component. No colour outside the tokens.
A kit component never ships a user-visible string of its own, accessible names included: it
takes required labels, so `tsc` refuses a caller without translations. True today for the
badges (a required `label`), `ColumnConfigPanel` (the app passes `useColumnPanelLabels()`),
`ViewsAndFilters` with its `FilterBuilder` (`useViewsAndFiltersLabels()`), the condition names
and a saved filter's text included — one translated template per condition, never field +
condition + value glued together in the kit (KAN-415) — `RepeatableRowGroup`
(a customer's contact rows pass `contactPointRowLabels(t)`), `OverlayProvider`'s
`closeLabel`, `ActionBar` (`useActionBarLabels()`), `SelectionBar` and `FilterChips` (KAN-110,
KAN-181, KAN-334, KAN-362, KAN-364, KAN-416). **Not yet true for the rest of the kit (KAN-364's
remaining PRs):** `DataGrid`, `Wizard`, `GlobalSearch`, `Picker`, `InlineEditField`,
`DocumentPreview` and `PresentMode` still merge English defaults — pass every label a component
takes.

**The kit's `Picker` is the entity-selection gesture** ("which customer?", "which vehicle?": a ranked
list in a panel, a create path, autofocus). A type-ahead that fills a form's own fields from an
external service and selects no record — the address search field, `components/AddressSearchField.tsx`
(KAN-37, ADR-101), on Mantine's `Combobox` — is a form input like `PhoneInput` and lives in the app;
it takes every string from the bundles like any input. A second such field reuses that one, never a
second local combobox.

## The rulings that bite most often

- **A field's hint is an info icon beside the label** (KAN-443; Anto, 2026-10-25: "we want less
  text"): in the app `const hint = useFieldHint()` then `{...hint(t('label'), t('labelHint'))}` on
  any Mantine input (the kit's `fieldHint` underneath), never a `description` line between label
  and input or under it. The hint shows in a tooltip on hover and keyboard focus and still
  reaches assistive technology as a visually hidden span: a wrapper input is linked by Mantine
  (`aria-describedby`); a Checkbox or Switch is not, so it passes `{ descriptionId: useId() }`
  as the third argument and the hint is linked that way (a Switch also reads it inside its
  label). The icon is named `common.fieldHintIcon` ("Hinweis") and joins the field's accessible
  name, so a role query matches the label as a prefix — `{ name: (n) => n.startsWith(label) }`,
  never an unescaped `RegExp` (labels contain parentheses). Choice cards (the customer-type
  tiles) are not fields and keep their text.
- **ADR-056** — grid state (search, filter, sort, tab, scope) lives in the URL; the URL is the
  shareable unit. Layout and density stay out of it: they belong on the user preference record.
- **ADR-058** — views and filters are **one control**, labelled with the current view. A
  user-defined filter is **one predicate** (field, operator, value), never a typed expression.
  Every filter is a chip (FR-UI-06).
- **ADR-102 (proposed, KAN-458) — a grid shows only the filters it applies.** Every filterable
  grid narrows the predicates it reads (URL, saved view) with the kit's `offeredPredicates`: an
  unoffered field or condition is neither sent nor shown; a select/text/boolean field keeps its
  first predicate; number and date bounds on one field are ANDed (`utils/filterParams.ts`). A
  field offers only the conditions its endpoint applies exactly — inclusive `…Min`/`…Max` are
  «mindestens»/«höchstens» (`atLeast`/`atMost`), and no grid offers «ist nicht» yet (KAN-476).
  `src/architecture/grid-filters-are-applied.test.ts` runs each grid's mapper over every offered
  field × condition against the endpoint's parameters in `schema.d.ts`; a new filterable grid
  adds its mapper there.
- **ADR-059** — opening a record from inside a process renders it as an **overlay**, not a
  navigation. Losing a half-built offer is a defect. The naive version (set the hash, set it
  back) fires the router and destroys the screen underneath — the bug the component prevents.
- **ADR-060** — every persisted field is available as a grid column; a documented subset is
  visible by default. A stored field that cannot be put on screen is a defect. A grid with a
  Columns panel passes `gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry)` to both
  `DataGrid` and the panel: without a layout `DataGrid` renders every column (KAN-361).
  `src/architecture/grid-default-column-layout.test.ts` parses every file that renders the panel
  with the TypeScript compiler and requires the panel's `layout` and each `DataGrid`'s
  `columnLayout` to be an identifier bound (by scope) to a `const` whose value is
  `… ?? defaultColumnLayout(…)`, directly or as `useMemo(() => …)`'s body — comments are not
  code, and a `let`, destructured or parameter binding never passes (KAN-367). `DataGrid` itself stays layout-agnostic: a
  grid without a panel shows every column (ADR-092).
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
- **ADR-076 — every CHF amount reads `CHF 12'500.00`, inputs included** (KAN-168): a CHF input
  is the kit's `ChfInput` (or `chfInlineEditor()` inside an `InlineEditField`), never a bare
  `NumberInput` or `<input type="number">`; `no-bare-money-number-input.test.ts` names every
  NumberInput that is not money (quantity, months, km, days, a percentage, spec figures). A
  number filter's saved text formats its value too (KAN-421): a `FilterFieldDef` declares
  `numberFormat` — `grouped` (the default, `formatNumber`), `chf`, or `ungrouped` for a year;
  never guessed from the label. The FilterBuilder's own value input is still a bare
  `<input type="number">`, money fields included (KAN-454).
- **ADR-084 — one environment-notice slot in the shell.** `AppShell`'s optional `notice` strip
  (ui-kit `shell/EnvironmentNotice.tsx`, between the top bar and the content region,
  `role="status"`, no dismiss control) is the only place for a persistent deployment-level
  notice — D-A-05's support-access banner uses it when built, never a second banner. A
  build-time setting switches it on (`VITE_OUTBOX_WORKER_ABSENT=true` in `render.yaml`,
  `src/config/deployment.ts`), never the environment's name; its text is an i18n key in all
  four bundles. The top bar stays breadcrumb + search only. The UI/UX Specification's
  Application Shell section does not show the strip yet (ADR-084's consequence; KAN-316).
- **Every screen but the sign-in loads on demand** (KAN-233): a new route's page goes into
  `App.tsx` through `lazyPage(() => import('./pages/X'), 'X')`, never a static import —
  `LoginPage`, `ProtectedRoute`, `RequireEntitlement`, `AuthProvider`, `DmsShell` and `RouteSuspense`
  stay eager (beside `App.tsx` and `lazyPage` itself), and `src/architecture/lazy-routes.test.ts`
  checks that none of those six, nor `App.tsx` beyond `LoginPage`, imports a page statically (it
  reads import lines only; `lazyPage.ts` is not in its list). `RouteSuspense`
  (`src/layout/`) shows the loading state on a hard load and, if a chunk cannot be fetched (a tab
  older than the last deploy), a translated reload screen. On in-app navigation the router's
  transition keeps the previous screen up until the next chunk arrives — never blank, but no
  loader either (React Router 7's default `startTransition` navigation; no render test pins it).

## i18n

- Bundles: `frontend/apps/dms/src/i18n/locales/{de,en,fr,it}.json`. A new key goes into all
  four in the same change; `localeKeyParity.test.ts` fails otherwise.
- A key missing from every bundle renders `⚠ MISSING I18N KEY: <key>`; one missing only from
  the active locale falls back to English (`fallbackLng: 'en'`), never German —
  `localeKeyParity.test.ts` keeps that from shipping. No hardcoded user-visible string,
  including labels in dialogs and wizards.
- **An API refusal is shown in the UI language** (Anto, 2026-10-23, KAN-398): map
  `details.reason` (and a stale `If-Match` 409) to a key in all four bundles, anything else to
  a translated generic message — never `err.message`, which is the API's English text, for logs
  only. The one helper is `utils/apiRefusal.ts::apiRefusalText(t, err, fallbackKey)` (KAN-437):
  a body-less response (`unknown_error`, KAN-271) is `common.requestFailed`; then a stale
  If-Match 409; a known `details.reason` (its table; `apiRefusal.test.ts` fails on a literal
  `"reason": "…"` in `app/` it does not translate); a 403, a 404 and a schema 422
  (`details.errors`) by status; anything else — a business rule refused **without** a reason
  included — is the screen's own key. Such a refusal loses its meaning on screen: give it a
  `details.reason` (KAN-472 lists today's). A screen with its own wording for a reason (merge,
  contract confirmation, business data) keeps its helper.
  `src/architecture/no-api-error-message-render.test.ts` refuses a new `.message` render in the
  app; its allow-list holds only `ConfiguratorOverlay`'s `HostCommitError`, built from `t(...)`.
  It does not scan `packages/ui-kit`: `InlineEditField` still shows a thrown error's `message`
  (KAN-473).
- The customer's **correspondence language** is not the user's **UI language** — never the
  same control, never the same stored field.
- **DE, FR and IT person nouns use the generic masculine** (Anto, 2026-10-12, KAN-171; the
  UI/UX Specification's i18n row): "Verkaufsberater", "Conseiller de vente", "Amministratore" —
  no slash, pair, parenthesis or point-médian form in the bundles.
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
- Every visible change ends with a screenshot of the changed screen, taken after the last code
  change — in the desktop preview, or in a cloud session a Playwright capture of localhost; any
  other capture is registered with `scripts/dev/gate evidence screenshot <file>`. The hand-over
  gate checks for one of this exact tree.

## Dates

A date-only value (a backend `dt.date`: birth date, purchase date, first registration, a
plate's validity, …) names a calendar day and renders through `formatDateOnly`; `formatDate`
and `formatDateTime` are for timestamps, which they render in the browser's zone (KAN-180,
KAN-333). `src/architecture/date-only-fields-format.test.ts` judges a field by its name
(`…Date`, `…Since`, `…FollowUp`, `expectedDelivery`, `vehicleFirstRegistration`) and refuses
it in four places: inside `formatDate`/`formatDateTime`, in CustomersListPage's `date('…', …)`
column helper without `formatDateOnly`, as a raw JSX child (`>{r.purchaseDate}`, with or
without a `?? '—'` / `?? t(…)` fallback), and as a grid column's `cell:` renderer that returns
the bare field (KAN-358). It is a source scan with known misses (its KAN-358 comment
lists them: a field joined into a string or passed to a row helper, an `InlineEditField` whose
`value=` is also its display, a JSX child sharing its element with text, a `?? null` or
`?? CONST` fallback), and it cannot judge a name at all for `validFrom`/`validTo` (a date on a
plate, an accessory, a user's access window and a valuation — the last rendered by no screen yet;
a timestamp on a contact channel) or `lastDeltaCursor` (KAN-363) — the ones a screen renders,
and every screen that renders a date-only field, are checked by that screen's render test under
`TZ=Pacific/Honolulu` (`dateOnlyTabs`, `UsersListPage.dateOnly`, `IntegrationPlatformView.pending`). Today's date as a pre-fill is
`todayLocalIsoDate()` (`utils/format.ts`), the user's calendar day — never
`toISOString().slice(0, 10)`, the UTC day, which is yesterday between midnight and 01:00/02:00
Swiss time (KAN-358; `src/architecture/no-utc-calendar-day.test.ts` refuses it).

## API types (KAN-35)

`src/api/schema.d.ts` is generated from the backend's own `app.openapi()` and diffed in CI
(`frontend-openapi-drift`); editing it is blocked. `src/api/types.ts` derives every
request/response type from it, except the documented FRONTEND-ONLY `ApiErrorBody` (the error
envelope has no OpenAPI schema). A shape the backend under-types (a `str` whose real values are
a literal union) is a `NARROWED` override in `types.ts`, commented as such — never a
hand-written interface. After a backend schema change: `make generate-frontend-types`, then
commit the result.
