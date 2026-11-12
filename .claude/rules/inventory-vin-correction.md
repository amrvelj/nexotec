---
paths:
  - "app/inventory/services/stock_item.py"
  - "app/inventory/api/stock_items.py"
  - "app/inventory/reconciliation.py"
  - "app/vehicle/services/vin_correction.py"
  - "app/vehicle/services/configuration.py"
  - "app/valuation/reconciliation.py"
  - "tests/test_inventory_vin_correction.py"
  - "app/inventory/services/purchase.py"
  - "app/inventory/services/pipeline.py"
  - "tests/test_inventory_trade_in_vin.py"
  - "frontend/apps/dms/src/components/stock-detail/RecordPurchaseDialog*"
  - "frontend/apps/dms/src/components/stock-detail/VinCorrectionDialog*"
  - "frontend/apps/dms/src/components/stock-detail/useStockConfigurationEditor.tsx"
  - "frontend/apps/dms/src/components/stock-detail/StockConfiguratorContent.tsx"
  - "frontend/apps/dms/src/components/stockRowMenu.tsx"
  - "frontend/apps/dms/src/components/configurator/ConfiguratorOverlay.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises KAN-557 (ADR-116 (2)-(3); Anto's
ruling on KAN-544, 2026-11-03) and KAN-536 (Anto, 2026-11-12). Its own file because inventory.md is over the KAN-614 budget. -->

# «VIN korrigieren» on a bought car (KAN-557)

- `POST /v1/inventory/stock-items/{id}/vin-correction` (`{newVin, reason}`; If-Match, Idempotency-Key;
  matrix row `stock_vin_correction`, the dealer manager only — the ticket's «dealer manager, sales
  manager»: no sales-manager role exists) corrects the VIN on the stock item, its vehicle-mdm record and
  its configuration together (`services/stock_item.py::correct_vin`).
- **Two transactions (ADR-047).** Vehicle owns the VIN: Stock first calls
  `vehicle.public.correct_vehicle_vin` (`services/vin_correction.py`, Vehicle's own session,
  compare-and-set on the old VIN), which rewrites the record and — when Stock names it — the
  configuration (`configuration.follow_vehicle_vin_correction`: none left alone, already-new only
  re-linked, a third VIN 422), audits `vin_corrected` (reason in the row's `reason`), publishes
  `vehicle.vehicle_mdm.vin_corrected` and ADR-119's `summary_changed` (party labels follow by id), and
  commits. Then Stock writes its item, audit row and `inventory.stock_item.vin_corrected`
  (`stockNumber`, `vehicleId`, `previousVin`, `vin`, `correctedBy`, `configurationId`; never the
  reason). A failed stock write is compensated by the same call with the VINs swapped, for
  only what Vehicle reported changed (`record_changed`, `configuration_vin_changed`,
  `configuration_relinked`; audited `vin_correction_reverted`) while the item's lock is held (the stock
  writes flush inside a savepoint; a failing COMMIT itself releases it first and is compensated after; reconciliation reports what slips in) — a
  record already carrying the new VIN is never put back, a configuration only re-linked is re-linked; a retry with
  the same key completes it.
- Refused, nothing written: left stock (409 `vin_correction_left_stock`) · no vehicle-mdm record (409
  `vin_correction_needs_vehicle` — a pipeline car corrects its configuration, which wins at booking,
  KAN-575) · the same VIN (422 `vin_unchanged`) · held in stock (409 `vin_already_in_stock`) · a third
  configuration VIN (422 `configuration_vin_differs`) · another record carries it (409
  `vin_belongs_to_other_vehicle`, `vehicleNumber`: one car, merged by platform staff, FR-V-12) · the
  record changed (409 `vehicle_vin_changed`) or was merged (409 `vehicle_merged`).
- Not refused: another Betrieb's live item on the same record (naming it would confirm it exists) —
  its stock VIN then differs and is reported. A sold row keeps the VIN it was sold under.
- **Rule 10:** inventory's reconciliation reports a car in stock whose VIN its record, or (a bought
  car) its configuration, does not carry; valuation's VIN check skips a VIN the record's own
  `vin_corrected` audit row names as the one before, for a valuation made before it (never edited,
  ADR-066).
- **UI:** «VIN korrigieren» in the one stock row menu (`VinCorrectionDialog`), disabled on a car that
  left stock or has no `vehicleId`. On a car with a `vehicleId`, «Konfiguration bearbeiten» passes the
  overlay `hostOwnsVin` (`useStockConfigurationEditor`): no `vin` in its PATCH, a changed VIN opens the
  dialog prefilled after the PATCH, an emptied one is refused in place, and without the row the field
  is read-only. The configuration PATCH has no server-side host check (Vehicle cannot ask Stock);
  reconciliation is the backstop.

# A trade-in is bought under the VIN its contract holds (KAN-536; Anto, 2026-11-12, option A)

- `sales.contract.confirmed`'s `tradeIn` carries the contract's `vin` (additive, left out when it holds
  none). Stock's consumer keeps it once, on the item it creates, as `stock_item.trade_in_vin` (inventory
  migration `2001f0c37ef5`) — never as `vin`, which stays empty until the booking (ADR-045), is unique among
  live items and binds the booking — normalised, and only a valid VIN (a pre-1981 chassis number, a
  confirmation without the key and an item created before keep none and are bought as before).
- The item's read exposes it (`tradeInVin`; not in the group projection). `RecordPurchaseDialog` prefills it
  when the configuration carries no VIN; the booking still needs `vin` sent, and another is 422
  `trade_in_vin_differs` (`field: vin`), before vehicle-mdm or the item is touched. **A configuration's VIN
  decides alone** when there is one (`configuration_vin_differs`): it is where a wrong VIN is corrected, the
  signed contract's cannot be. The refusal offers no edit.
- It is never written again: kept as history through the purchase, a storno (the car's own `vin` then binds
  the second booking) and «VIN korrigieren».
