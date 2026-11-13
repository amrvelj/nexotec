---
paths:
  - "app/sales/reconciliation.py"
  - "app/sales/services/deal_projection.py"
  - "app/sales/services/stock_item_purchase.py"
  - "app/sales/models/**"
  - "tests/test_sales_invoiceable_replica.py"
  - "tests/test_sales_deal_follows_live_entity.py"
  - "tests/test_trade_in_valuation_reconciliation.py"
  - "tests/architecture/test_actor_columns_reconciled.py"
---
<!-- Maintainer note. Rule 10; ADR-052, ADR-107. Moved out of sales.md by KAN-615 (2026-11-13).
Last verified against main@73baf3f (2026-10-25). Reconciliation reports; it never repairs. -->

# Sales' nightly reconciliation

- **Every cross-context id on every Sales table is reconciled:** an id naming nothing is an
  orphan (ReferenceCheck); a stock item, configuration or valuation of another dealership, or a
  customer outside the dealership's group, is a mismatch (StateCheck). A contract's
  `reservation_id` is checked only while it is confirmed (a released hold is cleared on the stock
  item). `created_by`/`updated_by` name a `user.id` or are null in every reconciled context
  (`platform.public.actor_reference_checks`; `test_actor_columns_reconciled.py`).
- **The invoiceable replica:** a replica row naming no stock item; a stock item Stock holds as
  purchased, last changed over an hour ago, with no replica row in its own dealership; a replica row
  whose item Stock does not hold as purchased for that dealership — both matched on tenant, as
  `is_invoiceable` is — unless Stock stornoed it there less than an hour ago (the storno event's
  `occurred_at`); and a contract, not cancelled, whose invoice was requested for a stock item Stock
  does not hold as purchased in its dealership (the storno / «Rechnung anfordern» race).
  **Unhandled:** a purchase whose first delivery failed, arriving after its storno, recreates the
  replica row — this check reports it once the storno is an hour old.
- **A merged customer:** a Sales row naming one, last written over an hour ago, is a missed
  `customer.merged` → `sales.customer_merged` (an attach waits on it).
- **The deal row:** a row whose number, status, buyer, locality, car, stock number, seller or
  document count differs from its live entity, or whose shape is wrong (two StateChecks).
- **The seller:** an open deal whose owner is a deactivated user, and an owner id naming no user.
- **The trade-in valuation:** a signed contract whose trade-in valuation of its own dealership is
  not stamped used (signed before confirmation consumed it, or a compensation race) — «Als
  verwendet markieren» repairs it, expired or not (`valuation-trade-in.md`).
