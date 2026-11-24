---
paths:
  - "frontend/packages/ui-kit/src/datagrid/**"
  - "frontend/apps/dms/src/pages/*ListPage*"
  - "frontend/apps/dms/src/modules/*/pages/*ListPage*"
  - "frontend/apps/dms/src/pages/listGrids*"
  - "frontend/apps/dms/src/pages/listRowActions*"
  - "frontend/apps/dms/src/modules/stock/components/GroupStockGrid*"
  - "frontend/apps/dms/src/**/*Grid*"
  - "frontend/apps/dms/src/*Options.ts"
  - "frontend/apps/dms/src/modules/*/*Options.ts"
  - "frontend/apps/dms/src/hooks/useDataGridLabels.ts"
  - "frontend/apps/dms/src/hooks/useColumnPanelLabels.ts"
  - "frontend/apps/dms/src/hooks/useGridPreferences.ts"
  - "frontend/apps/dms/src/hooks/*ersistedPreference*"
  - "frontend/apps/dms/src/utils/gridExport*"
  - "frontend/apps/dms/src/utils/columnRegistry*"
  - "frontend/apps/dms/src/hooks/useSortInUrl*"
  - "frontend/apps/dms/src/pages/StandardTextsPage*"
  - "frontend/apps/dms/src/pages/ReferenceDataPage*"
  - "frontend/apps/dms/src/pages/AccountRecoveryPage.tsx"
  - "frontend/apps/dms/src/pages/IntegrationPlatformView.tsx"
  - "frontend/apps/dms/src/components/translations/UiStringsPanel*"
  - "frontend/apps/dms/src/components/translations/MissingTranslationsCard*"
  - "frontend/apps/dms/src/components/translations/StandardTextSeedsPanel*"
  - "frontend/apps/dms/src/architecture/grid-*"
  - "frontend/apps/dms/src/modules/vehicle/components/MappingGapsQueue*"
  - "frontend/apps/dms/src/modules/vehicle/components/catalogue/BrandsAdmin.tsx"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/ExternalIdsTab.tsx"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/VehiclesTab*"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/OffersContractsTab.tsx"
  - "frontend/apps/dms/src/modules/contact/components/contact-detail/offersContracts*"
  - "frontend/apps/dms/src/modules/stock/components/stock-detail/WagenbuchTab.tsx"
---
<!-- Maintainer note. ADR-060, ADR-092; UI/UX Core Principles (grids).
Split out of ui.md by KAN-615 (2026-11-13); filters, views and URL state split out to
ui-grid-filters.md by KAN-743 (2026-11-17), so a plain grid (a Contact 360 tab) loads only this.
Last verified against main@3b2bf69 (2026-11-18). -->

# Data grids

Filters, saved views, URL state and the list-query layer: `ui-grid-filters.md`.

- **Every grid** (UI/UX Core Principles): every column sortable server-side (the short administration lists sort in the browser: kit `sortRowsInBrowser`, sort in the URL via `useSortInUrl`; ADR-158, KAN-516); column order,
  visibility, width and pinning configurable and persisted per user; a three-dots row menu on every
  row; cursor-based lazy loading; maximum density; Shift + wheel scrolls horizontally. The live
  catalogue grid sorts only on the fields the identAPI sorts; a stored sort on another field is
  narrowed away like an unoffered predicate, never sent.
- **ADR-060** — every persisted field is available as a column; a documented subset is visible by
  default; a stored field that cannot be put on screen is a defect. A grid with a Columns panel
  passes `gridPrefs.columnLayout ?? defaultColumnLayout(columnRegistry)` to both `DataGrid` and the
  panel — without a layout `DataGrid` renders every column
  (`src/architecture/grid-default-column-layout.test.ts`, which accepts only a `const` bound to
  that expression). `DataGrid` itself stays layout-agnostic: a grid without a panel shows every
  column (ADR-092).
- Group stock is a scope switch, not a second grid with columns removed (`inventory.md`).
