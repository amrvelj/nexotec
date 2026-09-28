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
  `inventory.stock_item.purchased`; Sales never queries Stock synchronously for it.
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
