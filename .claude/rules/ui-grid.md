---
paths:
  - "frontend/packages/ui-kit/src/datagrid/**"
  - "frontend/apps/dms/src/pages/*ListPage*"
  - "frontend/apps/dms/src/pages/listGrids*"
  - "frontend/apps/dms/src/pages/listRowActions*"
  - "frontend/apps/dms/src/pages/stock/GroupStockGrid*"
  - "frontend/apps/dms/src/**/*Grid*"
  - "frontend/apps/dms/src/*Options.ts"
  - "frontend/apps/dms/src/hooks/useDataGridLabels.ts"
  - "frontend/apps/dms/src/hooks/useColumnPanelLabels.ts"
  - "frontend/apps/dms/src/hooks/useViewsAndFiltersLabels*"
  - "frontend/apps/dms/src/hooks/useGridPreferences.ts"
  - "frontend/apps/dms/src/hooks/useSavedViews.ts"
  - "frontend/apps/dms/src/hooks/*ersistedPreference*"
  - "frontend/apps/dms/src/utils/gridExport*"
  - "frontend/apps/dms/src/utils/sortParam*"
  - "frontend/apps/dms/src/architecture/grid-*"
  - "frontend/apps/dms/src/components/MappingGapsQueue*"
  - "frontend/apps/dms/src/components/catalogue/BrandsAdmin.tsx"
  - "frontend/apps/dms/src/components/customer-detail/ExternalIdsTab.tsx"
  - "frontend/apps/dms/src/components/customer-detail/VehiclesTab*"
  - "frontend/apps/dms/src/components/customer-detail/OffersContractsTab.tsx"
  - "frontend/apps/dms/src/components/customer-detail/offersContracts*"
  - "frontend/apps/dms/src/components/stock-detail/WagenbuchTab.tsx"
---
<!-- Maintainer note. ADR-056, ADR-058, ADR-060, ADR-092, ADR-102; UI/UX Core Principles (grids).
Split out of ui.md by KAN-615 (2026-11-13); the frontend has no module folders yet (KAN-611), so
the grids are listed by file. Last verified against main@73baf3f (2026-10-25). -->

# Data grids

- **Every grid** (UI/UX Core Principles): every column sortable server-side (the short administration lists sort in the browser: kit `sortRowsInBrowser`, sort in the URL via `useSortInUrl`; ADR-158, KAN-516); column order,
  visibility, width and pinning configurable and persisted per user; a three-dots row menu on every
  row; cursor-based lazy loading; maximum density; Shift + wheel scrolls horizontally. The live
  catalogue grid sorts only on the fields the identAPI sorts; a stored sort on another field is
  narrowed away like an unoffered predicate, never sent.
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
- **ADR-060** — every persisted field is available as a column; a documented subset is visible by
  default; a stored field that cannot be put on screen is a defect. A grid with a Columns panel
  passes `gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry)` to both `DataGrid` and the
  panel — without a layout `DataGrid` renders every column
  (`src/architecture/grid-default-column-layout.test.ts`, which accepts only a `const` bound to
  that expression). `DataGrid` itself stays layout-agnostic: a grid without a panel shows every
  column (ADR-092).
- A number filter's saved text is formatted by its `FilterFieldDef.numberFormat` (`grouped`, `chf`,
  `ungrouped` for a year), never guessed from the label; a `chf` field's value is entered through
  `ChfInput` and stored as a plain number.
- Group stock is a scope switch, not a second grid with columns removed (`inventory.md`).
