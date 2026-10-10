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
---
<!-- Maintainer note (stripped before Claude sees it). Summarises rules 4–6, 10 and 12, ADR-006,
ADR-046, ADR-047. Verified against main@7805816 on 2026-10-10 (every present-tense claim
checked against the code). Fix this file in the same PR as any change to what it states. -->

# Events, outbox, consumers and reconciliation

- **Outbox:** the business row and the outbox row are written in **one local transaction**;
  never a dual write. The outbox and consumer harness (`app/core/outbox*.py`,
  `app/core/consumer.py`, `app/core/processed_event_model.py`) is preserved verbatim.
- **Names are facts in the past tense, never commands.** New event types use
  `<context>.<entity>.<verb>` (`inventory.stock_item.purchased`, `sales.contract.confirmed`).
  Existing two-part names stay as they are (`customer.created`, `valuation.used`,
  `configuration.matched`, …): consumers subscribe by the name (`transport.register` in
  `app/worker.py`) and pending `outbox_message` rows carry it in `event_type`, so a rename is a
  contract change for Anto to decide.
- **Consumers are idempotent** by `eventId` against the processed-events table; delivery is
  at-least-once. Every new consumer gets a redelivery-idempotency test on the Postgres lane
  (today the two marketplace-transmission consumers have none, and nothing makes a new consumer
  get one — KAN-124).
- **Payload shapes are consistent** across the event types of one entity, and every envelope
  field carries meaning — a `correlationId` generated fresh per event correlates nothing
  (`app/core/outbox.py` still defaults it to a fresh `uuid7()` and no producer passes one —
  KAN-125).
- **Cross-context writes** are a call with a compensating action, never a shared transaction
  (ADR-047, CLAUDE.md rule 12) — each side commits its own work. Two contract events, never
  one (ADR-046).
- Consumers are registered in `app/worker.py::register_handlers` (event type + `consumer_name`);
  daily jobs in `register_daily_jobs` (e.g. `integration.daily_jobs`, `reconciliation.run_all`).
- **Reconciliation monitors; it does not repair.** Derived state (such as a valuation's status)
  is derived on read, never "fixed" by a nightly job. Dead letters alert. A job that re-reads a
  three-column-pattern **label** from its owner (`customer.vehicle_party_labels`, KAN-84) is not
  a repair: the label is a copy, refreshed on purpose, and its age is a gauge
  (`dms.label.age_seconds`, alert threshold in README's alarm table).
- **A repair the nightly run makes (ADR-047: "a failure after commit is repaired by the nightly
  reconciliation job") is a daily job of the owning context with its own record, never part of
  reconciliation** — finding and fixing stay apart (Anto, 2026-10-08, KAN-122; ADR-047
  addendum). It is registered after `reconciliation.run_all`. The one today: Stock's
  orphan-reservation sweep (`inventory.orphaned_reservations.release`; see
  `.claude/rules/inventory.md`).
- **Outbox now, broker later** (ADR-006): the Postgres-backed transport stays until the third
  independently deployed service exists.
