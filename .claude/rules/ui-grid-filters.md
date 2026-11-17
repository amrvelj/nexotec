---
paths:
  - "app/core/list_query.py"
  - "frontend/packages/ui-kit/src/datagrid/**"
  - "frontend/apps/dms/src/pages/*ListPage*"
  - "frontend/apps/dms/src/modules/*/pages/*ListPage*"
  - "frontend/apps/dms/src/pages/listGrids*"
  - "frontend/apps/dms/src/pages/IntegrationPlatformView*"
  - "frontend/apps/dms/src/modules/stock/components/GroupStockGrid*"
  - "frontend/apps/dms/src/modules/vehicle/components/catalogue/CatalogueBrowseGrid*"
  - "frontend/apps/dms/src/*Options.ts"
  - "frontend/apps/dms/src/modules/*/*Options.ts"
  - "frontend/apps/dms/src/shared/gridSchema.ts"
  - "frontend/apps/dms/src/hooks/useViewsAndFiltersLabels*"
  - "frontend/apps/dms/src/hooks/useSavedViews.ts"
  - "frontend/apps/dms/src/hooks/*ersistedPreference*"
  - "frontend/apps/dms/src/hooks/useSortInUrl*"
  - "frontend/apps/dms/src/hooks/useGridPreferences.ts"
  - "frontend/apps/dms/src/components/dataExport/ExportsGrid*"
  - "frontend/apps/dms/src/components/translations/UiStringsPanel*"
  - "frontend/apps/dms/src/components/translations/MissingTranslationsCard*"
  - "frontend/apps/dms/src/components/translations/StandardTextSeedsPanel*"
  - "frontend/apps/dms/src/pages/ReferenceDataPage*"
  - "frontend/apps/dms/src/pages/StandardTextsPage*"
  - "frontend/apps/dms/src/architecture/grid-filters*"
---
<!-- Maintainer note. ADR-056, ADR-058, ADR-102, ADR-179. Split out of ui-grid.md by KAN-743
(2026-11-17): what a grid with filters, views or URL state needs; every grid's rules stay there. -->

# Grid filters, views and URL state

- **List-query layer** (ADR-179, `app/core/list_query.py`): one column registry per entity; `sort` and
  `filter=field:op[:value]` over it, 422 outside, served at `…/grid-schema`. A grid on it (Stock; more in
  KAN-461 PR-2…4) takes filter fields, `sortableFields` and default visibility from the schema — never a hand list or `meta.sortField`.
- **ADR-056** — grid state (search, filter, sort, tab, scope) lives in the URL, the shareable unit.
  Layout and density stay out of it: they belong on the user preference record.
- **ADR-058** — views and filters are **one control**, labelled with the current view. A
  user-defined filter is **one predicate** (field, operator, value), never a typed expression.
  Every filter is a chip (FR-UI-06).
- **ADR-102 — a grid shows only the filters it applies:** it narrows what it reads (URL, saved
  view) with the kit's `offeredPredicates` — an unoffered field or condition is neither sent nor
  shown, quietly; a view so narrowed shows the unsaved-changes dot and no tick (the grid passes
  `narrowPredicates`). **A field holds one filter**, ranges included (the builder replaces it,
  offers no «duplicate»; a link or view with two keeps the first). A field offers only the
  conditions its endpoint applies exactly: inclusive `…Min`/`…Max` are «mindestens»/«höchstens», a
  select's «ist nicht» is its endpoint's exclusion, the live catalogue offers «ist» only; a record
  without a value is kept. An N-days count is a whole number within `MIN_FILTER_DAYS`…
  `MAX_FILTER_DAYS` (the builder refuses another and holds Save back; a link or view drops it).
  `src/architecture/grid-filters-are-applied.test.ts` checks each grid's mapper against its
  endpoint; a new filterable grid adds its mapper there.
- A number filter's saved text is formatted by its `FilterFieldDef.numberFormat` (`grouped`, `chf`,
  `ungrouped` for a year), never guessed from the label; a `chf` field's value is entered through
  `ChfInput` and stored as a plain number.
