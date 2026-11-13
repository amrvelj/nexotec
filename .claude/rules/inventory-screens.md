---
paths:
  - "frontend/apps/dms/src/pages/Stock*"
  - "frontend/apps/dms/src/pages/stock/**"
  - "frontend/apps/dms/src/components/stock-detail/**"
  - "frontend/apps/dms/src/components/stockRowMenu*"
  - "frontend/apps/dms/src/stockOptions.ts"
  - "frontend/apps/dms/src/architecture/stock-intake*"
  - "frontend/packages/ui-kit/src/components/Stock*"
---
<!-- Maintainer note. PRD-Stock FR-I-07; ADR-055, ADR-061. Moved out of inventory.md by KAN-615
(2026-11-13); listed by file until the frontend has module folders (KAN-611). Last verified
against main@73baf3f (2026-10-25). The backend rules are inventory.md. -->

# Stock screens

- **One stock row menu** (ADR-061): `components/stockRowMenu.tsx::buildStockRowMenu` is the one
  definition the Stock list's ⋯ menu and the stock detail's header (primary «Vertrag erstellen»,
  alternative «Offerte erstellen», overflow) render from. Both purchase actions («Einkauf
  erfassen», «Einkauf stornieren») are always listed, the one that does not apply disabled with its
  reason (Anto); a role's unusable action is hidden; a car that left stock, or a configuration a
  contract's reservation freezes, is disabled with its reason. The list opens the car with the
  action in the router state; the detail runs it once and drops it.
  `StockRowMenu.render.test.tsx` compares the two menus item by item.
- **One «Fahrzeug aufnehmen»** (header and empty state, `pages/stock/AddToPipelineButton.tsx`) opens
  the configurator with `build` and `record`; there is no hand-typed form.
- **Group stock is a scope switch** (`ScopeSwitchMenu`, ADR-055); the dealership is a column, not
  sortable (it lives on platform's table). `GroupStockGrid` offers «Betrieb ist / ist nicht» in its
  own views-and-filters control (views saved under `inventory.stock.group`), the predicate in the URL
  beside `?scope=group`; a scope switch drops the filters either way (the grids filter on different
  fields).
- **The VIN at purchase:** `RecordPurchaseDialog` prefills the configuration's VIN; on a car no
  contract reserves, the `configuration_vin_differs` refusal offers «Konfiguration bearbeiten» (every
  stock car, pipeline or in stock, trade-ins included). A reserved car — a signed manual
  configuration's pipeline item — gets «Vertrag bearbeiten» on its configuration card instead,
  disabled with the reason: its contract is signed and frozen, so a wrong VIN there is corrected only
  after that contract is cancelled.
- A configured car's name shows read-only with a hint. The purchase card and the row menu carry
  «Einkauf stornieren» (`PurchaseStornoDialog`), which asks about the return only on a trade-in's
  car. «Zustand ändern» is `ChangeConditionDialog` (`stock_condition`, If-Match).
