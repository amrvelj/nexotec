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
---
<!-- Maintainer note (stripped before Claude sees it). Summarises rules 4–6, 10 and 12, ADR-006,
ADR-046, ADR-047, Gap G-16. Verified against main@568f416 on 2026-09-28 (every present-tense
claim checked against the code). Fix this file in the same PR as any change to what it states. -->

# Events, outbox, consumers and reconciliation

- **Outbox:** the business row and the outbox row are written in **one local transaction**;
  never a dual write. The outbox and consumer harness (`app/core/outbox*.py`,
  `app/core/consumer.py`, `app/core/processed_event_model.py`) is preserved verbatim.
- **Names are facts in the past tense, never commands.** New event types use
  `<context>.<entity>.<verb>` (`inventory.stock_item.purchased`, `sales.contract.confirmed`).
  Existing two-part names stay as they are (`customer.created`, `valuation.used`,
  `configuration.matched`, …): consumers and `processed_event` key on the name, so a rename is
  a contract change for Anto to decide.
- **Consumers are idempotent** by `eventId` against the processed-events table; delivery is
  at-least-once. Every new consumer gets a redelivery-idempotency test on the Postgres lane.
- **Payload shapes are consistent** across the event types of one entity, and every envelope
  field carries meaning — a `correlationId` generated fresh per event correlates nothing
  (Gap G-16; `app/core/outbox.py` still defaults it to a fresh `uuid7()`).
- **Cross-context writes** are a call with a compensating action, never a shared transaction
  (ADR-047, CLAUDE.md rule 12) — each side commits its own work. Two contract events, never
  one (ADR-046).
- Consumers and daily jobs are registered in `app/worker.py` (`register_daily_jobs`, e.g.
  `reconciliation.run_all`).
- **Reconciliation monitors; it does not repair.** Derived state (such as a valuation's status)
  is derived on read, never "fixed" by a nightly job. Dead letters alert.
- **Outbox now, broker later** (ADR-006): the Postgres-backed transport stays until the third
  independently deployed service exists.
