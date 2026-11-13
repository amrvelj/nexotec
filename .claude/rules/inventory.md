---
paths:
  - "app/inventory/**"
  - "app/integration/adapters/autoscout24.py"
  - "app/integration/adapters/marketplace_base.py"
  - "scripts/backfill_manual_configuration_reservations.py"
  - "alembic/versions/inventory/**"
  - "tests/test_inventory_*.py"
  - "tests/test_kan513_one_configuration_one_stock_item.py"
  - "tests/stock_support.py"
---
<!-- Maintainer note. PRD-Stock; ADR-045/049/052/054/055/057/062/070/074/103/116/117. KAN-615
(2026-11-13) cut it to the invariants: purchase and storno are inventory-purchase.md, reservations
inventory-reservation.md, VINs inventory-vin-correction.md, screens inventory-screens.md. Last
verified against main@73baf3f (2026-10-25). -->

# Stock and inventory

## The car's way onto stock
- **ADR-045 — pre-VIN vehicles are pipeline stock items here, never vehicle-mdm records.** A Sales
  manual configuration and a trade-in on a confirmed contract land here as pipeline items;
  promotion on VIN arrival is FR-V-04 (`services/pipeline.py::promote_and_flush`).
- **ADR-103 — purchase booking puts a car on stock:** configuration → pipeline → purchase →
  contract → invoice. A pipeline item is never bought without its VIN (FR-I-02b's «purchase first,
  VIN later» no longer exists); the booking promotes it before the purchase facts, in one
  transaction, so `inventory.stock_item.purchased` is published at once. Offer and contract stay
  possible at every phase; invoicing stays gated on the purchase. Booking and storno:
  `inventory-purchase.md`; the VIN rules: `inventory-vin-correction.md`.
- **ADR-116 — a create never puts a car on stock:** every create lands as `pipeline`, a VIN sent
  with it included — stored, checked, and **nothing written to vehicle-mdm** (a car not yet bought
  has no record). Tests needing a car in stock without a purchase use `tests/stock_support.py`.
- **Every stock vehicle a user adds enters through the configurator** (Anto, KAN-463): the create
  endpoint requires `configurationId` (the FR-I-02/02b consumers build their own create);
  `src/architecture/stock-intake-is-the-configurator.test.ts` lets only the configurator intake name
  the create path. An older item without one is repaired with «Konfiguration zuordnen» (no list
  marker). A configuration never writes vehicle-mdm (ADR-070).
- **One configuration, one stock item** (FR-C-11): create and attach refuse a configuration another
  item of the Betrieb holds (409 `configuration_held_by_stock_item`), under a per-(tenant,
  configuration) advisory lock; a partial unique index backs it for every writer. A second contract
  on the same offer reuses the free ordered car, else gets a configuration copy (Anto, KAN-513;
  `test_kan513_one_configuration_one_stock_item.py`). **Trap:** a reused car's `purchased`/`storno`
  name its first contract (`pipeline_ref`); `inventory.stock_item.reassigned` names the new one.
- An item a contract's confirmation creates names that contract on `inventory.stock_item.added`
  (`originContractId`, `originRole`); `added` carries `configurationId` whenever the item has one, and
  «Konfiguration zuordnen» publishes `inventory.stock_item.configuration_attached`, so Vehicle keeps
  the configuration (`referenced_configuration_ids` counts every stock item, whatever its lifecycle).

## Two axes, two views
- **ADR-054 — lifecycle and reservation are two independent axes**: `lifecycleStatus` (`pipeline` |
  `in_stock` | `storno_pending`) and `reservationState` (`none` | `reserved`). `sold` is not a value
  — an invoiced car has left stock (`left_stock_at`). Nothing sets it today:
  `invoicing_gate.py::apply_finance_invoice_issued` has no caller until Finance issues invoices
  (WP-9), so `request_invoice` leaves the car in stock. `storno_pending` is set by nothing.
- **ADR-055 — group-readable stock is its own enumerated projection** (`StockItemGroupRead`), never
  the tenant grid with columns removed; `tests/test_inventory_group_listing.py` asserts by name that
  prices, landed cost, notional input tax, purchase, supplier, invoicing and valuation fields are
  absent (`listPrice` is deliberately present). The dealership filter narrows the group's own roster
  only — an id outside it matches nothing, never a 403/404. Which Betriebe are in the group, and
  their labels, is platform's answer (`list_group_read_dealerships`, ADR-107); Stock never joins
  platform's tables.
- **ADR-049** — full commercial visibility inside the dealership; **ADR-029** at the group boundary.
- **Licensed with `sales`** (D-A-04): every inventory router carries the gate (404, before any
  role check; `test_sales_routers_carry_entitlement_gate.py`). Every write checks `require_write` on
  its own matrix row (`stock_items`, `stock_acquisition`, `stock_condition`,
  `marketplace_publishing`, `stock_reservation`; `tests/test_inventory_write_capabilities.py`), so
  widening one row opens no other.

## Facts other contexts read
- **ADR-052 — `is_invoiceable` is Stock's fact**, replicated to Sales through
  `inventory.stock_item.purchased` (once per booking — twice for a car bought, stornoed and bought
  again); Sales never asks on the invoicing path (`sales.md`). Reservation does not need the purchase
  (K-12); invoicing does. For a trade-in the event (and the storno) carries the contract's customer
  and signature, which the customer context uses (`customer-vehicle-party.md`).
- **A configured car is named by its configuration** (ADR-117 (2)): on an item with one,
  `vehicle_label` **is** the configuration's label — at create, at «Konfiguration zuordnen» (audited)
  and by the nightly `inventory.configuration_labels`, which counts a row current only when both
  `configuration_label` and `vehicle_label` match. A PATCH may resend the name unchanged, never
  another (422); an item without a configuration keeps its own editable text. On staging (no worker)
  a stale copy stays stale. Open offers learn a new label through ADR-115 (`events-stock-sales.md`).
- **ADR-074** — a trade-in's `valuationRef` is written by Stock's own consumer
  (`valuation-trade-in.md`).
- **Fiktiver Vorsteuerabzug** (Art. 28a MWSTG) is recorded at purchase booking
  (`services/purchase.py::record_purchase`) from the purchase price and the dealership's `vat_rate`,
  independent of `landed_cost`, and can be overridden (audited). Stock owns it; Sales only reads it.
  **There is no `vatTreatment`** (ADR-057; `test_no_vat_treatment_field.py`, and by name on the
  stock-item schemas in `tests/test_inventory_purchase.py`).

## Reservations, the sweep, marketplaces, numbers
- A manual configuration's pipeline item is created reserved for its contract; a stock car's
  reservation is Sales' synchronous call (ADR-047 Pattern B): `inventory-reservation.md`.
- **The nightly orphan sweep frees a car whose contract is not signed** (Anto):
  `inventory.orphaned_reservations.release`, registered after `reconciliation.run_all`, releases a
  reservation whose contract is cancelled, missing or pending; confirmed and invoiced keep the car;
  a pending contract's reservation from the last hour waits (a confirmation commits the reservation
  before the contract). Statuses come from Stock's contract projection — a contract with a fact not
  yet applied keeps its car (`events-stock-sales.md`). One commit per orphan; a failed release logs
  ERROR and is retried the next night.
- **Marketplaces are three** (ADR-062): AutoScout24 (AS24i v34.0, which drives the canonical field
  mapping), Carmarket, Autolina. **Full delivery:** an object no longer transmitted is **deleted**
  there with its statistics and URL, so unpublishing is a confirmed destructive action. Only
  AutoScout24 transmits; Carmarket and Autolina have no provider specification (KAN-72).
- Stock numbers are allocated per dealership inside the caller's transaction.
