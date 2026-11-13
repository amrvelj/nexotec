---
paths:
  - "frontend/packages/ui-kit/src/detail/**"
  - "frontend/packages/ui-kit/src/shell/Overlay*"
  - "frontend/packages/ui-kit/src/shell/overlayStack*"
  - "frontend/packages/ui-kit/src/shell/escapeOwnership.ts"
  - "frontend/packages/ui-kit/src/shell/BreadcrumbContext.tsx"
  - "frontend/packages/ui-kit/src/components/RowMenu*"
  - "frontend/packages/ui-kit/src/components/DocumentPreview.tsx"
  - "frontend/apps/dms/src/pages/*DetailPage*"
  - "frontend/apps/dms/src/pages/*CreatePage*"
  - "frontend/apps/dms/src/pages/DetailHeader*"
  - "frontend/apps/dms/src/pages/DetailBreadcrumb*"
  - "frontend/apps/dms/src/pages/OfferWorkspace*"
  - "frontend/apps/dms/src/**/*owMenu*"
  - "frontend/apps/dms/src/components/*-detail/**"
  - "frontend/apps/dms/src/components/deal-workspace/**"
  - "frontend/apps/dms/src/components/OfferGenerateReviewModal*"
  - "frontend/apps/dms/src/architecture/detail-header*"
---
<!-- Maintainer note. ADR-059, ADR-061, ADR-063; FR-UI-07 (breadcrumb). Split out of ui.md by
KAN-615 (2026-11-13). Last verified against main@73baf3f (2026-10-25). -->

# Detail screens, overlays and actions

- **ADR-061** — every detail screen: **one primary action, one alternative, and an overflow
  carrying the entity's full row menu** — same items, same order, same disabled-with-explanation
  entries. The row menu is the single definition of what can be done to an entity; list and
  detail both render from it. The primary is an action, never a tab switch
  (`src/architecture/detail-header-primary-is-not-a-tab-switch.test.ts`).
- **ADR-059** — opening a record from inside a process renders it as an **overlay**, not a
  navigation; losing a half-built offer is a defect. The naive version (set the hash, set it
  back) fires the router and destroys the screen underneath — the bug the kit's overlay prevents.
- **ADR-063** — generating an offer is **two steps**: build, then review the rendered document in
  the customer's correspondence language with the seller-only margin panel *beside* it, never on
  it.
- **Breadcrumb:** a page's segment that names a screen is a `{ label, href }`
  (`BreadcrumbSegment`) and renders as a link — the list segment on every entity detail, the
  create screens, the offer workspace, a location's detail; a nav group or a section without a
  screen of its own (Stammdaten, Verwaltung, Mein Konto) and the current screen stay text. The
  root is the dashboard (`ui-shell.md`).
