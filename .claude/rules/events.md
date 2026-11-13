---
paths:
  - "app/core/outbox*.py"
  - "app/core/consumer.py"
  - "app/core/processed_event_model.py"
  - "app/core/reconciliation*.py"
  - "app/worker.py"
  - "app/reconciliation_runner.py"
  - "app/*/services/**"
  - "app/*/consumers.py"
  - "app/**/*consumer*.py"
  - "app/**/*reconciliation*.py"
  - "app/core/daily_scheduler*.py"
  - "app/*/daily_jobs.py"
  - "app/core/observability.py"
  - "scripts/*outbox*.py"
  - "app/core/idempotent_route.py"
---
<!-- Maintainer note. Rules 4-6, 10, 12; ADR-006/046/047/081/084/107/126. Loads for every service
file, so it holds the invariants only: worker and job detail is events-worker.md, Stock/Sales fact
flows events-stock-sales.md (KAN-615). Last verified against main@73baf3f (2026-10-25). -->

# Events, outbox, consumers and reconciliation

- **Outbox:** the business row and the outbox row in **one local transaction**, never a dual
  write. The harness (`app/core/outbox*.py`, `consumer.py`, `processed_event_model.py`) is
  preserved verbatim.
- **Names are facts in the past tense.** New types are `<context>.<entity>.<verb>`; existing
  two-part names (`customer.created`, `valuation.used`) stay. Subscribers and pending
  `outbox_message.event_type` rows carry the name, so a rename is a contract change: an ADR
  naming every subscriber and how pending rows migrate (ADR-126).
- **Consumers are idempotent** by `eventId`; delivery is at-least-once and unordered. Every
  registered consumer needs its fixture in `tests/test_consumer_redelivery_idempotency.py`,
  which fails without one.
- **Every envelope field carries meaning** and one entity's events share a payload shape. A
  `correlationId` minted per event correlates nothing (the outbox still defaults one; KAN-125).
- **A write spanning two contexts** is a call with a compensating action, each side committing
  its own work (ADR-047, rule 12). Two contract events, never one (ADR-046).
- **Staging runs no worker** (ADR-081 option D; ADR-084): consumers and daily jobs run only
  where `python -m app.worker` runs, so "works on staging" proves nothing about them.
- **Reconciliation reads across, never writes** (ADR-107): `app/<context>/reconciliation.py` is
  the one place a context's SQL names another context's model
  (`test_no_cross_context_orm_query.py`). It **monitors, never repairs**: derived state is
  derived on read, dead letters alert. A repair is a daily job of the owning context with its
  own record, registered after `reconciliation.run_all` (ADR-047 addendum). A label refresh is
  not a repair (`events-worker.md`).
- **A context learns that another holds its record from the holder's own fact**, never inside
  the holder's transaction (ADR-047, rule 2) — e.g. Vehicle's `first_taken_at`
  (`vehicle-configurator.md`).
- **Outbox now, broker later** (ADR-006): the Postgres transport stays until a third
  independently deployed service exists.
- Worker, daily jobs, label refreshes: `events-worker.md`. Stock ↔ Sales fact flows
  (`commercial_changed`, the contract projection): `events-stock-sales.md`.
