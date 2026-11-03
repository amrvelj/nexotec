---
paths:
  - "app/inventory/**"
  - "frontend/apps/dms/src/**/*tock*"
  - "frontend/apps/dms/src/**/*tock*/**"
  - "frontend/apps/dms/src/pages/stock/**"
  - "frontend/packages/ui-kit/src/components/Stock*"
  - "app/integration/adapters/autoscout24.py"
  - "app/integration/adapters/marketplace_base.py"
  - "scripts/backfill_manual_configuration_reservations.py"
  - "scripts/migrate_transaction_rows.py"
  - "alembic/versions/inventory/**"
  - "tests/test_inventory_*.py"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Stock, ADR-045, ADR-049,
ADR-052, ADR-054, ADR-055, ADR-062, ADR-074, ADR-087 and the VAT reasoning of ADR-057; also cites
ADR-029, ADR-047, ADR-070. Verified against main@73baf3f on 2026-10-25 (every present-tense claim
checked against the code). "Not built"
lines cite a ticket; /drift-audit re-checks them weekly. -->

# Stock and inventory

- **ADR-045 — pre-VIN vehicles are pipeline stock items here, never vehicle-mdm records.** A
  Sales manual configuration and a trade-in on a confirmed contract both land here as pipeline
  items; promotion on VIN arrival is FR-V-04 (`services/pipeline.py::promote_and_flush`).
- **ADR-103 — purchase booking puts a car on stock** (Anto, 2026-10-25, KAN-446): the chain is
  configuration → pipeline → purchase → contract → invoice. `POST …/stock-items/{id}/purchase`
  on a pipeline item needs the VIN (`RecordPurchaseRequest.vin`; 422 `purchase_requires_vin`
  without it) and promotes the item before the purchase facts are written, in one transaction,
  so `inventory.stock_item.purchased` is published at once; on an item in stock a `vin` that is
  not the item's is 422 `purchase_vin_mismatch`. **A pipeline item is bought with its
  configuration's VIN** (KAN-510; Anto, 2026-11-01, option B then A): when the configuration
  carries one, another `vin` is 422 `configuration_vin_differs` (`field: vin`), compared in the one
  spelling (`services/stock_item.py::configuration_vin_differs`, the create's and
  «Konfiguration zuordnen»'s rule), before vehicle-mdm or the item is touched; a configuration
  without one (a factory order) takes the typed VIN, an item without a configuration is unchanged.
  `RecordPurchaseDialog` prefills it, and on a car no contract reserves the refusal offers
  «Konfiguration bearbeiten», which corrects the VIN in the configurator (vehicle.md, Host / mode
  matrix; Anto, 2026-11-02, KAN-510 option A: every stock car, pipeline or in stock, trade-ins
  included). A reserved car — a signed manual configuration's pipeline item — gets «Vertrag
  bearbeiten» on its configuration card instead, disabled with the reason: its contract is signed and
  frozen, so a wrong VIN there is corrected only after that contract is cancelled. A pipeline item is never bought without its
  VIN, so FR-I-02b's «purchase booked first, VIN later» order no longer exists. Offer and
  contract stay possible at every phase; invoicing stays gated on the purchase.
- **ADR-054 — lifecycle and reservation are two independent axes**, never one merged status:
  `lifecycleStatus` (`pipeline` | `in_stock` | `storno_pending`) and `reservationState`
  (`none` | `reserved`). `sold` is not a lifecycle value — an invoiced vehicle has left stock
  (`left_stock_at` set, FR-I-12). Today only the legacy transaction import sets it:
  `services/invoicing_gate.py::apply_finance_invoice_issued` has no production caller until
  Finance issues invoices (WP-9, `.claude/rules/finance.md`), so `request_invoice` leaves the car
  in stock. A single enum cannot express "in stock **and** reserved",
  which is the ordinary case.
- **ADR-055 — group-readable stock is its own enumerated projection** (`StockItemGroupRead`),
  not the tenant grid with columns removed. `tests/test_inventory_group_listing.py` asserts by
  name that effective and base price, landed cost, notional-input-tax, purchase, supplier,
  invoicing and valuation fields are absent from it; `listPrice` is deliberately present. In the
  UI, group stock is a scope switch (`ScopeSwitchMenu`); the dealership is a column, not
  sortable (it lives on platform's table). A dealership filter is not built (KAN-104). Which
  Betriebe are in the group, and their labels, is platform's answer
  (`platform.public.list_group_read_dealerships`, KAN-121, ADR-107): Stock never joins platform's
  tables.
- **ADR-049** — full commercial visibility inside the dealership (margin, discounts,
  Wagenbuch); **ADR-029** unchanged at the group boundary.
- **Stock is licensed with the `sales` module** (D-A-04, KAN-198): every inventory router
  carries `require_entitlement(ModuleCode.SALES)` at router level (404 without it, before any
  role check; `tests/architecture/test_sales_routers_carry_entitlement_gate.py`), and customer's
  `GET /v1/customers/{id}/vehicles` embeds no stock summary without it (`holds_entitlement`,
  KAN-378). Every inventory write checks `require_write` on its own matrix row — `stock_items`
  for the item's own record, `stock_acquisition`, `stock_condition`, `marketplace_publishing`,
  `stock_reservation` (KAN-376; `tests/test_inventory_write_capabilities.py`), so widening one
  row opens no other write. Details in `.claude/rules/platform.md`.
- **ADR-052 — `is_invoiceable` is Stock's fact**, replicated to Sales through
  `inventory.stock_item.purchased` (published once, by `mark_purchased_if_ready`; the legacy
  transaction migration publishes nothing and writes Sales' replica row itself); Sales keeps it
  per stock item and never queries Stock synchronously for it. Reservation does not need the
  purchase (K-12); Sales' invoicing does (KAN-100).
- **A stock item's configuration label follows the configuration within a day** (KAN-534, rule 2):
  `configuration_label` and `configuration_label_refreshed_at` are written when a configuration is
  attached or a pipeline item created, and re-read nightly by `inventory.configuration_labels`
  (`services/configuration_label.py`, after `sales.owner_labels`) through
  `vehicle.public.get_configuration_labels` — the other label jobs' rules: `version` not bumped, a
  current label only restamped, a configuration naming nothing or another Betrieb left alone, each
  batch `FOR NO KEY UPDATE SKIP LOCKED`; gauge `dms.label.age_seconds{label="inventory.configuration"}`.
- **ADR-074** — a trade-in's `valuationRef` is written by inventory's own
  `sales.contract.confirmed` consumer when it creates the pipeline item; Sales never writes it.
  Details in `.claude/rules/valuation.md`.
- **A pipeline item can point at a configuration** (`configuration_id`, KAN-10): added from
  the Stock list in either mode (FR-C-13), or carried by a contract's confirmation for a
  configured car or a configured trade-in. It never writes vehicle-mdm (ADR-070).
- **Every stock vehicle a user adds enters through the configurator** (KAN-463; Anto,
  2026-10-26, option A): the Stock list's one «Fahrzeug aufnehmen» (header and empty state,
  `pages/stock/AddToPipelineButton.tsx`) opens it with `build` and `record`; the hand-typed
  form and `/stock/new` are gone. `POST /v1/inventory/stock-items` takes
  `StockItemCreateRequest`, whose `configurationId` is required (422
  `configuration_required`) — at the endpoint only (decision 1a): the FR-I-02/02b consumers
  build their own `StockItemCreate` and the ADR-091 import writes its rows directly. A
  configuration whose VIN contradicts the body's is 422 `configuration_vin_differs`.
  `src/architecture/stock-intake-is-the-configurator.test.ts` lets only the configurator
  intake name the create path (the server's 422 is the real guard). An item created before without a configuration is repaired on its detail with
  «Konfiguration zuordnen» (decision 2a: no list marker): `POST …/stock-items/{id}/configuration`
  (If-Match, Idempotency-Key, audit `configuration_attached`; 409
  `configuration_already_linked`, 422 `configuration_vin_differs` when both carry a VIN and they
  differ). **One configuration, one stock item** (KAN-509, FR-C-11): create and attach refuse a
  configuration another stock item of the Betrieb holds — 409 `configuration_held_by_stock_item`
  with the holder's `stockItemId` and `stockNumber` — under a per-(tenant, configuration) advisory
  lock, so two racing writers cannot both win. The FR-I-02/02b consumers do not take it (they
  key on the contract, so a second contract on the same offer would carry the offer's
  configuration into a second pipeline item); no unique index backs it yet (a migration —
  KAN-513).
- A pipeline item a contract's confirmation creates names that contract on
  `inventory.stock_item.added` (`originContractId`, `originRole`: `manual_configuration` |
  `trade_in`; additive, KAN-144). A directly added item carries neither. It carries
  `configurationId` whenever the item has one, and «Konfiguration zuordnen» publishes
  `inventory.stock_item.configuration_attached` (`stockNumber`, `configurationId`), so Vehicle keeps a
  configuration a stock item took for good (KAN-270; `inventory.public.referenced_configuration_ids`
  is Vehicle's read-only safety net — every stock item counts, whatever its lifecycle).
- **A manual configuration's pipeline item is created reserved for its contract** (K-12,
  FR-I-11; KAN-158), in the consumer transaction that creates it; a trade-in's is not. Stock
  releases it on `sales.contract.cancelled` (consumer `inventory.sales_contract_cancelled`,
  matching `reserved_by_contract_id`), and records the cancellation in
  `inventory_cancelled_contract` (with the contract number as rule 2's label), so a
  confirmation delivered after it (a retried delivery) creates the item unreserved. The
  cancellation consumer, and the confirmation consumer when it carries a manual configuration,
  take a per-contract advisory lock first, so two workers cannot interleave them. Nightly
  reconciliation checks the record's `contract_id` and `tenant_id`. A
  stock car's reservation is still made and released by Sales' synchronous calls (ADR-047
  Pattern B): `reserve_for_contract` and `release`. `reserve_for_contract` is idempotent by (item, contract): the contract's live reservation if it
  holds one, a fresh one if the item is free (a compensation released the first), else 409;
  `reserve` and `reserve_for_contract` refuse a car that has left stock (`left_stock_at` set) —
  409 `vehicle_left_stock` (KAN-147), after the same-contract replay; the KAN-166 backfill below
  still reserves an invoiced contract's own item through `reserve_and_flush`; each
  compensating release is keyed per reservation (KAN-114). The HTTP reserve and release
  endpoints sit on the matrix's "Stock reservation" row — no user write, so only platform staff
  pass (KAN-376) — and are `IdempotentRoute`s (KAN-266, Anto 2026-10-08): the route holds the client's
  optional key and calls `reserve_without_key()`/`release_without_key()`, so a replay returns
  the stored response with no side effect for as long as the HTTP record lives (at least
  24 h). `reserve_for_contract()` and `release()` still require their caller's key and keep
  their own records, never purged, for Sales and the orphan sweep; `reserve()` keeps the keyed
  form but has no caller outside inventory (KAN-114). Before KAN-158, manual items were created
  unreserved and cancellations were not recorded; `scripts/backfill_manual_configuration_reservations.py`
  (KAN-166, dry run unless `--commit`, re-runnable) repairs both. It learns which contracts are
  cancelled from the `sales.contract.cancelled` events in the outbox, never from Sales' tables;
  a contract confirmed and never cancelled (confirmed or invoiced, even once its car has left
  stock) gets its item reserved; an item held by another contract, or one Stock released before,
  is reported, never overwritten or re-reserved.
- **The nightly orphan sweep frees a car whose contract is not signed** (KAN-122, KAN-173):
  daily job `inventory.orphaned_reservations.release` (`daily_jobs.py`,
  `services/reservation_sweep.py`), registered after `reconciliation.run_all`. A reservation
  whose contract is cancelled, missing, or pending is released; confirmed and invoiced keep the
  car (Anto, 2026-10-07). A pending contract's reservation made in the last hour waits for the
  next run, because a confirmation commits the reservation before the contract (Anto,
  2026-10-08). Stock reads the statuses through
  `app.sales.public.get_contract_statuses` on a session of its own, and releases each orphan
  in its own commit (ADR-047). Each run logs one line with its counts in the message text; a
  failed release is logged at ERROR and retried the next night.
- **Fiktiver Vorsteuerabzug** (Art. 28a MWSTG) is recorded at purchase booking
  (`app/inventory/services/purchase.py::record_purchase`), computed from the purchase price and
  the dealership's `vat_rate`, independent of `landed_cost`; it can be overridden afterwards
  (`override_notional_input_tax`, audited). Stock owns it; Sales only reads it.
  **There is no `vatTreatment`** on a stock item (ADR-057; enforced by
  `tests/architecture/test_no_vat_treatment_field.py` over `app/` and the frontend, and by name on
  the stock-item schemas in `tests/test_inventory_purchase.py`): the notional
  credit is a purchase-side fact, never a per-vehicle VAT treatment, never on a customer document.
- **Marketplaces are three, not one** (ADR-062): AutoScout24 (AS24i v34.0, which drives the
  canonical field mapping), Carmarket and Autolina. **Full-delivery semantics:** an object no
  longer transmitted is **deleted** at the marketplace, with its statistics and its URL, so
  unpublishing is a confirmed destructive action. Today only AutoScout24 transmits
  (`services/marketplace_transmission.py`, `integration/adapters/autoscout24.py`); Carmarket and
  Autolina have no provider specification (KAN-72).
- Stock numbers are allocated per dealership inside the caller's transaction, the same idiom
  as the other number allocators.
- **A VIN is in a dealership's stock at most once** (KAN-111): unique among items that have not
  left stock (`uq_stock_item_tenant_id_vin_in_stock`, `left_stock_at IS NULL`). Creating or
  promoting a second one is 409 `vin_already_in_stock` with the holder's `stockItemId` and
  `stockNumber`, checked before vehicle-mdm is touched; the index refuses a racing writer with the
  same 409. A direct create with a VIN upper-cases it and strips whitespace, then validates it
  as vehicle-mdm does (422 otherwise), and resolves `vehicle_id` from vehicle-mdm by that VIN —
  a client `vehicleId` is ignored (Anto, 2026-10-12, KAN-244), and without a VIN it is refused,
  422 `vehicle_id_requires_vin` (Anto, 2026-10-12, KAN-324); promotion normalises its VIN the
  same way before anything else. Create and promotion resolve the
  VIN through `services/vehicle_resolution.py` on a session of their own, so vehicle-mdm's commit
  or rollback never touches the caller's work; the loser of an insert race on a VIN vehicle-mdm
  has never seen takes the winner's record and carries on — then is stocked or refused
  `vin_already_in_stock` (KAN-257; promotion runs at purchase booking, KAN-446). A car that left stock (invoiced) comes back as a new item, its sold row kept as
  history; a storno'd car (`storno_pending`, which nothing sets yet — no storno flow exists,
  KAN-146) would still block its VIN. The same car may be in pipeline twice (pipeline
  items carry no VIN until promotion). The legacy transaction import follows the same rule: a car
  the old system shows sold and later traded back in gets a second item, the sold one is never
  reopened, and a row for a car live Stock holds or has sold is reported
  (`vehicle_known_to_live_stock`), never applied — history is imported before a dealership starts
  to work (Anto, 2026-10-08; KAN-256).
