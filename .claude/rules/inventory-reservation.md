---
paths:
  - "app/inventory/services/reservation*.py"
  - "app/inventory/services/pipeline.py"
  - "app/inventory/api/reservations.py"
  - "app/inventory/schemas/reservation.py"
  - "app/inventory/models/cancelled_contract.py"
  - "app/inventory/consumers.py"
  - "app/inventory/daily_jobs.py"
  - "scripts/backfill_manual_configuration_reservations.py"
  - "tests/test_inventory_reservation*.py"
  - "tests/test_inventory_manual_*.py"
  - "tests/test_inventory_pipeline_consumer.py"
  - "tests/test_sales_lifecycle_reservation.py"
---
<!-- Maintainer note. PRD-Stock K-12, FR-I-11; ADR-047 (Pattern B). Moved out of inventory.md by
KAN-615 (2026-11-13). Last verified against main@73baf3f (2026-10-25). -->

# Reservations

- **A manual configuration's pipeline item is created reserved for its contract** (K-12, FR-I-11),
  in the consumer transaction that creates it; a trade-in's is not. Stock releases it on
  `sales.contract.cancelled` (matching `reserved_by_contract_id`) and records the cancellation in
  `inventory_cancelled_contract` (the contract number as rule 2's label), so a confirmation
  delivered after it creates the item unreserved. Both consumers take a per-contract advisory lock
  first, so two workers cannot interleave them. Reconciliation checks the record's `contract_id`
  and `tenant_id`.
- **A stock car's reservation is Sales' synchronous call** (ADR-047 Pattern B):
  `reserve_for_contract` and `release`. `reserve_for_contract` is idempotent by (item, contract):
  the contract's live reservation if it holds one, a fresh one if the item is free (a compensation
  released the first), else 409. `reserve` and `reserve_for_contract` refuse a car that has left
  stock (409 `vehicle_left_stock`), after the same-contract replay. Each compensating release is
  keyed per reservation.
- **The HTTP reserve and release endpoints** sit on the matrix's «Stock reservation» row — no user
  write, so only platform staff — and are `IdempotentRoute`s: the route holds the client's
  optional key and calls `reserve_without_key()`/`release_without_key()`, so a replay returns the
  stored response for as long as the HTTP record lives (at least 24 h). `reserve_for_contract()`
  and `release()` require their caller's key and keep their own records, never purged, for Sales and
  the orphan sweep; `reserve()` has no caller outside inventory.
- **`scripts/backfill_manual_configuration_reservations.py`** repairs items created unreserved and
  cancellations not recorded before this: it learns which contracts are cancelled from the
  `sales.contract.cancelled` events in the outbox, never from Sales' tables; a contract confirmed
  and never cancelled (invoiced included, even once its car left stock) gets its item reserved
  through `reserve_and_flush`; an item held by another contract, or one Stock released before, is
  reported, never overwritten. It publishes what the production path publishes (`migrations.md`).
