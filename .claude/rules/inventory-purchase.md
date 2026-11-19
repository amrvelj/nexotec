---
paths:
  - "app/inventory/services/purchase.py"
  - "app/inventory/services/ledger.py"
  - "app/inventory/api/stock_items.py"
  - "app/inventory/api/ledger.py"
  - "app/inventory/schemas/purchase.py"
  - "app/inventory/schemas/ledger.py"
  - "app/inventory/models/stock_item_ledger.py"
  - "tests/inventory/test_inventory_purchase*.py"
  - "tests/inventory/test_inventory_ledger.py"
  - "tests/inventory/test_kan535_chassis_number_purchase.py"
  - "frontend/apps/dms/src/modules/stock/components/stock-detail/PurchaseStorno*"
  - "frontend/apps/dms/src/modules/stock/components/stock-detail/RecordPurchaseDialog*"
  - "frontend/apps/dms/src/modules/stock/components/stock-detail/RecordCostDialog*"
  - "frontend/apps/dms/src/modules/stock/components/stock-detail/WagenbuchTab.tsx"
---
<!-- Maintainer note. PRD-Stock FR-I-05; ADR-052, ADR-103; Anto's rulings on KAN-146 and KAN-572.
Moved out of inventory.md by KAN-615 (2026-11-13). Last verified against main@3b2bf69 (2026-11-18). -->

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
  left stock, a contract on the car that requested its invoice (from Stock's contract projection,
  `events-stock-sales.md`), or a car published on any marketplace (409 `purchase_storno_published`,
  `channels`; the seller unpublishes first — the storno never unpublishes). Publish takes the
  item's row lock, so it cannot race a storno past this. The Betrieb's website is not covered
  (open with Anto, KAN-766).
- **KAN-146's decisions, as Anto ruled them (KAN-747, 2026-11-18: A, C, A, A):** «Rechnung
  angefordert» counts as invoiced (1 A); a published car refuses the storno (2 C, above);
  `einstandspreis` stays closed to hand booking with the two keys reserved (3 A); the days in stock
  restart at the next booking (4 A). A storno and «Rechnung anfordern» can still race (no shared
  lock); Sales' reconciliation reports the result (`sales-reconciliation.md`).
- **A pre-1981 car is bought under its chassis number** (KAN-535, ADR-103 as amended; Anto,
  2026-11-13, option A). `vin` takes `NormalisedVinOrChassisNumber`; a chassis number needs the
  item's configuration to carry it (422 `chassis_number_requires_configuration`), with a first
  registration before 1981 and a make (`chassis_number_requires_pre_1981_registration`,
  `chassis_number_requires_make`), all before anything is written. Promotion resolves it through
  `vehicle.public.resolve_vehicle_mdm_by_chassis_number`: a record of `identifier_kind`
  `chassis_number`, unique by the number and `chassis_make_key` (the make, case, spaces, hyphens and
  accents ignored; `uq_vehicle_mdm_chassis_number_make`; a VIN is unique among VIN records,
  `uq_vehicle_mdm_vin`; a CHECK holds the pre-1981 date). Never found by its number alone (lookup,
  search, `match_vehicle`); the identity PATCH does not change its number (422
  `chassis_number_identity_fixed`) or move its registration past 1980 — «VIN korrigieren» corrects
  the number (Anto, 2026-11-14, `inventory-vin-correction.md`). Stock counts an ISO VIN
  once per Betrieb (`uq_stock_item_tenant_id_iso_vin_in_stock`) and a chassis-number car once by its
  record (`uq_stock_item_tenant_id_chassis_vehicle_in_stock`): two makes may share a number.
