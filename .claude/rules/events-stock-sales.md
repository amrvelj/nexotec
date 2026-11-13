---
paths:
  - "app/inventory/services/commercial_change.py"
  - "app/inventory/services/sales_contract.py"
  - "app/inventory/models/sales_contract.py"
  - "app/inventory/services/reservation_sweep.py"
  - "app/inventory/services/purchase.py"
  - "app/inventory/services/stock_item.py"
  - "app/inventory/services/pricing.py"
  - "app/inventory/services/configuration_label.py"
  - "app/inventory/consumers.py"
  - "app/inventory/reconciliation.py"
  - "app/sales/services/vehicle_change.py"
  - "app/sales/consumers.py"
  - "app/sales/reconciliation.py"
  - "scripts/backfill_vehicle_changed_flags.py"
  - "scripts/backfill_inventory_sales_contract_projection.py"
  - "tests/test_sales_vehicle_changed_in_stock.py"
  - "tests/test_inventory_sales_contract_projection.py"
  - "tests/sales_contract_projection_support.py"
  - "frontend/apps/dms/src/components/deal-workspace/VehicleChangedBanner*"
  - "frontend/apps/dms/src/pages/SalesListPage.vehicleChanged.render.test.tsx"
---
<!-- Maintainer note. ADR-115, ADR-131, ADR-145. Moved out of events.md by KAN-615 (2026-11-13).
Last verified against main@73baf3f (2026-10-25); the projection lines with KAN-630's code. -->

# Stock ↔ Sales fact flows

- **«Fahrzeug im Lager geändert»** (ADR-115): Stock publishes
  `inventory.stock_item.commercial_changed` in the write's transaction (state read before,
  `publish_if_changed` after) when list or effective price, the option set, colour, odometer or
  the configuration label change — PATCH, pricing dialog, «Konfiguration zuordnen», the nightly
  label refresh; never purchase or storno. `sales.stock_item_commercial_changed` flags draft and
  open offers and pending contracts frozen before the change (audit row, deal row, **no version
  bump**; a contract from a flagged offer takes the flag). Refresh re-freezes with `force=True`,
  keeping the seller's line edits; dismiss keeps the accepted prices; both need an editable deal
  and a flag. Reconciliation reports an open deal whose frozen (or accepted) price differs,
  unflagged.
- **Stock's projection of Sales' contracts** (ADR-145; ADR-131: Stock imports nothing from
  Sales): `inventory_sales_contract`, fed by `sales.contract.created` / `.confirmed` /
  `.invoice_requested` / `.cancelled` — an order-free merge (status only rises, cancelled is
  final). The orphan sweep and «Einkauf stornieren» read it **plus the facts the consumer has not
  applied** (no processed-event row and not published, or published before the consumers existed
  until a backfill run is recorded in `inventory_sales_contract_backfill`): an unapplied fact
  keeps the car reserved and refuses the storno. **Read the outbox first, then the projection** —
  the reverse lets a fact applied between the two escape both. Reconciliation reports a row
  differing from its contract (unchanged for an hour), a contract over an hour old with no row,
  and a row naming no contract or Betrieb.
- **Vehicle's projection of the records a Betrieb holds in stock** (KAN-667, ADR-160; ADR-131: no call
  into Stock): `vehicle_stock_holding` (vehicle migration `01f4dda679ea`), fed by
  `inventory.pipeline_vehicle.vin_assigned` (with `stockNumber`) and `inventory.stock_item.sold`
  (`vehicle.stock_holding_on_*`, `vehicle/services/stock_holding.py`); a set-once, order-free merge; a
  storno keeps the hold. The identity PATCH and the detail read count it plus unapplied `vin_assigned`
  facts (outbox first). Reconciliation reports a differing row, a held item missing, dangling references.
