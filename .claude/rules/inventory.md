---
paths:
  - "app/inventory/**"
  - "frontend/apps/dms/src/**/*tock*"
  - "frontend/apps/dms/src/**/*tock*/**"
  - "frontend/apps/dms/src/pages/stock/**"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises PRD-Stock, ADR-045, ADR-049,
ADR-052, ADR-054, ADR-055, ADR-062 and the VAT reasoning of ADR-057. Verified against
main@568f416 on 2026-09-28 (every present-tense claim checked against the code). "Not built"
lines cite a ticket; /drift-audit re-checks them weekly. -->

# Stock and inventory

- **ADR-045 — pre-VIN vehicles are pipeline stock items here, never vehicle-mdm records.** A
  Sales manual configuration and a trade-in on a confirmed contract both land here as pipeline
  items; promotion on VIN arrival is FR-V-04.
- **ADR-054 — lifecycle and reservation are two independent axes**, never one merged status:
  `lifecycleStatus` (`pipeline` | `in_stock` | `storno_pending`) and `reservationState`
  (`none` | `reserved`). `sold` is not a lifecycle value — an invoiced vehicle has left stock
  (`left_stock_at` set, FR-I-12). A single enum cannot express "in stock **and** reserved",
  which is the ordinary case.
- **ADR-055 — group-readable stock is its own enumerated projection** (`StockItemGroupRead`),
  not the tenant grid with columns removed. `tests/test_inventory_group_listing.py` asserts by
  name that price, cost, notional-input-tax, purchase, supplier, invoicing and valuation fields
  are absent from it. In the UI, group stock is a scope switch with the dealership as a filter.
- **ADR-049** — full commercial visibility inside the dealership (margin, discounts,
  Wagenbuch); **ADR-029** unchanged at the group boundary.
- **ADR-052 — `is_invoiceable` is Stock's fact**, replicated to Sales through
  `inventory.stock_item.purchased` (published once, by `mark_purchased_if_ready`; the legacy
  transaction migration publishes nothing and writes Sales' replica row itself); Sales keeps it
  per stock item and never queries Stock synchronously for it. Reservation does not need the
  purchase (K-12); Sales' invoicing does (KAN-100).
- **A pipeline item can point at a configuration** (`configuration_id`, KAN-10): added from
  the Stock list in either mode (FR-C-13), or carried by a contract's confirmation for a
  configured car or a configured trade-in. It never writes vehicle-mdm (ADR-070).
- A pipeline item a contract's confirmation creates names that contract on
  `inventory.stock_item.added` (`originContractId`, `originRole`: `manual_configuration` |
  `trade_in`; additive, KAN-144). A directly added item carries neither.
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
  holds one, a fresh one if the item is free (a compensation released the first), else 409; each
  compensating release is keyed per reservation (KAN-114). The HTTP endpoint keeps `reserve()`,
  whose replayed key returns the stored response with no side effect. Before KAN-158, manual items were created
  unreserved and cancellations were not recorded; `scripts/backfill_manual_configuration_reservations.py`
  (KAN-166, dry run unless `--commit`, re-runnable) repairs both. It learns which contracts are
  cancelled from the `sales.contract.cancelled` events in the outbox, never from Sales' tables;
  a contract confirmed and never cancelled (confirmed or invoiced, even once its car has left
  stock) gets its item reserved; an item held by another contract, or one Stock released before,
  is reported, never overwritten or re-reserved.
- **Fiktiver Vorsteuerabzug** (Art. 28a MWSTG) is recorded at purchase booking
  (`app/inventory/services/purchase.py::record_purchase`), computed from the purchase price and
  the dealership's `vat_rate`, independent of `landed_cost`. Stock owns it; Sales only reads it.
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
  same 409 — except two promotions racing on a VIN vehicle-mdm has never seen, where vehicle-mdm's
  own uniqueness refuses the second first, with its 409 and no `reason` (KAN-257; promotion has
  no caller yet). A car that left stock (invoiced) comes back as a new item, its sold row kept as
  history; a storno'd car still blocks its VIN. The same car may be in pipeline twice (pipeline
  items carry no VIN until promotion). The legacy transaction import still reopens a sold car's
  row instead (KAN-256).
