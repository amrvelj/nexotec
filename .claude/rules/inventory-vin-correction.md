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
  - "tests/test_inventory_vin*.py"
  - "tests/test_inventory_purchase_configuration_vin.py"
  - "tests/test_inventory_create_with_vin_is_pipeline.py"
  - "frontend/apps/dms/src/components/stock-detail/RecordPurchaseDialog*"
  - "frontend/apps/dms/src/components/stock-detail/VinCorrectionDialog*"
  - "frontend/apps/dms/src/components/stock-detail/useStockConfigurationEditor.tsx"
  - "frontend/apps/dms/src/components/stock-detail/StockConfiguratorContent.tsx"
  - "frontend/apps/dms/src/components/stockRowMenu.tsx"
  - "frontend/apps/dms/src/components/configurator/ConfiguratorOverlay.tsx"
---
<!-- Maintainer note. KAN-557 (ADR-116 (2)-(3); Anto's ruling on KAN-544, 2026-11-03), KAN-536
(Anto, 2026-11-12); the VINs-in-stock section moved here from inventory.md by KAN-615 (2026-11-13).
Last verified against main@73baf3f (2026-10-25). -->

# VINs in stock

- **A VIN is in a dealership's stock at most once:** unique among items that have not left stock
  (`uq_stock_item_tenant_id_vin_in_stock`); a second one, created or promoted, is 409
  `vin_already_in_stock` with the holder, checked before vehicle-mdm is touched, and the index
  refuses a racing writer with the same 409. A VIN sent with a create is upper-cased, stripped and
  validated as vehicle-mdm does; a client `vehicleId` is ignored (the item's `vehicle_id` is resolved
  from the VIN at purchase booking) and without a VIN refused (`vehicle_id_requires_vin`).
  Promotion normalises its VIN first and resolves it through
  `vehicle.public.resolve_vehicle_mdm_by_vin` (Vehicle's own session, so its commit never touches
  the caller's work); the loser of an insert race takes the winner's record and carries on.
  Promotion leaves the item itself out of its uniqueness check, and the record it creates names the
  booking user.
- A car that left stock comes back as a new item, its sold row kept as history; a stornoed car is
  back in pipeline with its VIN and still blocks it. The same car may be in pipeline twice (an item
  a confirmation creates carries no VIN until promotion).
- **A pipeline item is bought with its configuration's VIN** (Anto, KAN-510): when the
  configuration carries one, another `vin` is 422 `configuration_vin_differs`, compared in the one
  spelling (`services/stock_item.py::configuration_vin_differs`, also the create's and «Konfiguration
  zuordnen»'s rule), before vehicle-mdm or the item is touched; a configuration without one (a
  factory order) takes the typed VIN; without a configuration, the trade-in rule below. On an item
  in stock, a `vin` not the item's is 422 `purchase_vin_mismatch`; the booking may leave `vin` out.
- **While never bought, its configuration's VIN wins** (Anto, KAN-575): once the configuration's VIN
  is corrected, or a factory order's configuration gets one, the booking (no `vin`, or the
  configuration's) books the car under it, corrects the item's in promotion's savepoint
  (`correct_vin`; a VIN another item holds is 409) and audits `vin_corrected_at_purchase`; a third
  VIN is 422 `configuration_vin_differs`. A stornoed car (it kept its record) is not corrected so.

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
  reconciliation is the backstop. **The vehicle-mdm identity PATCH does check it** (Anto, KAN-667 A;
  ADR-160): a VIN change on a record the caller's Betrieb holds in stock is 409
  `vin_change_needs_vin_correction` (`stockItemId`, `stockNumber`), read from Vehicle's projection
  `vehicle_stock_holding` (`events-stock-sales.md`); Vehicle 360 shows that VIN read-only, linking the stock
  car's «VIN korrigieren» (`stockOpenState('correctVin')`). Another Betrieb's stock never refuses nor shows.

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
  signed contract's cannot be. The refusal offers no edit; its text names the way out (Anto, 2026-11-13):
  «Konfiguration zuordnen» — or «Konfiguration bearbeiten» on an item that has one — with the right VIN.
- It is never written again: kept as history through the purchase, a storno (the car's own `vin` then binds
  the second booking) and «VIN korrigieren».
