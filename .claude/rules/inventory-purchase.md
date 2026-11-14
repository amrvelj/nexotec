---
paths:
  - "app/inventory/services/purchase.py"
  - "app/inventory/services/ledger.py"
  - "app/inventory/api/stock_items.py"
  - "app/inventory/api/ledger.py"
  - "app/inventory/schemas/purchase.py"
  - "app/inventory/schemas/ledger.py"
  - "app/inventory/models/stock_item_ledger.py"
  - "tests/test_inventory_purchase*.py"
  - "tests/test_inventory_ledger.py"
  - "frontend/apps/dms/src/components/stock-detail/PurchaseStorno*"
  - "frontend/apps/dms/src/components/stock-detail/RecordPurchaseDialog*"
  - "frontend/apps/dms/src/components/stock-detail/RecordCostDialog*"
  - "frontend/apps/dms/src/components/stock-detail/WagenbuchTab.tsx"
---
<!-- Maintainer note. PRD-Stock FR-I-05; ADR-052, ADR-103; Anto's rulings on KAN-146 and KAN-572.
Moved out of inventory.md by KAN-615 (2026-11-13). Last verified against main@73baf3f (2026-10-25). -->

# The purchase booking and «Einkauf stornieren»

- **A purchase is booked once** (409 `purchase_already_booked` for a second). The booking posts the
  purchase price to the Wagenbuch as an automatic `einstandspreis` (`ledger.py::post_purchase_entries`,
  key `purchase:<uuid7>`, dated the purchase date's start in Switzerland) and audits it.
  `einstandspreis` is automatic-only — no hand booking — and a hand booking may not use a
  `purchase:`/`storno:` key (422 `source_ref_reserved`).
- **«Einkauf stornieren» reverses it** (`stock_acquisition`, If-Match, a required reason), in one
  transaction: one negated counter-entry per automatic purchase entry not yet reversed (key
  `storno:<its key>`; a hand-booked `einstandspreis` from before keyed `storno:<the entry's id>`),
  the purchase facts and `is_invoiceable` cleared, the car back in `pipeline` **keeping its VIN and
  `vehicle_id`** — it still blocks the VIN and is booked again without a new one (`record_purchase`
  restocks it without promoting; another VIN is 422 `purchase_vin_mismatch`) — audited with the
  reason, and `inventory.stock_item.storno` published.
- **A trade-in may go back to its customer** (Anto, KAN-572 option C): `returnedToCustomer: true` on
  the storno (default false; refused on any other car), audited with it; the customer context then
  reopens that customer as owner and keeper (`customer-vehicle-party.md`).
- A reserved car may be stornoed; the reservation stays. **Refused:** no booked purchase, a car that
  left stock, or a contract on the car that requested its invoice (from Stock's contract
  projection, `events-stock-sales.md`).
- **Built as the safe default, open with Anto on KAN-146:** «Rechnung angefordert» counting as
  invoiced (Finance issues none yet); a published car staying published (the feed keeps sending
  it, though a pipeline car could not be published); `einstandspreis` closed to hand booking with
  the two keys reserved; the days in stock restarting at the next booking. A storno and «Rechnung
  anfordern» can still race (no shared lock); Sales' reconciliation reports the result
  (`sales-reconciliation.md`).
- **A pre-1981 car is bought under its chassis number** (KAN-535, ADR-103 as amended; Anto,
  2026-11-13, option A). `vin` takes `NormalisedVinOrChassisNumber`; a chassis number needs the
  item's configuration to carry it (422 `chassis_number_requires_configuration`), with a first
  registration before 1981 and a make (`chassis_number_requires_pre_1981_registration`,
  `chassis_number_requires_make`), all before anything is written. Promotion resolves it through
  `vehicle.public.resolve_vehicle_mdm_by_chassis_number`: a record of `identifier_kind`
  `chassis_number`, unique by the number and `chassis_make_key` (the make, case, spaces, hyphens and
  accents ignored; `uq_vehicle_mdm_chassis_number_make`; a VIN is unique among VIN records,
  `uq_vehicle_mdm_vin`; a CHECK holds the pre-1981 date). Never found by its number alone (lookup,
  search, `match_vehicle`); the identity PATCH and «VIN korrigieren» do not change its number or
  move its registration past 1980 (422 `chassis_number_identity_fixed`). Stock counts an ISO VIN
  once per Betrieb (`uq_stock_item_tenant_id_iso_vin_in_stock`) and a chassis-number car once by its
  record (`uq_stock_item_tenant_id_chassis_vehicle_in_stock`): two makes may share a number.
