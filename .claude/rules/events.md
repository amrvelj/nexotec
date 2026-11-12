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
<!-- Maintainer note (stripped before Claude sees it). Summarises rules 4–6, 10 and 12, ADR-006,
ADR-046, ADR-047, ADR-081; also cites ADR-084, ADR-126. Verified against main@73baf3f on 2026-10-25 (every present-tense
claim checked against the code); the event-rename sentence re-checked against main@e37e36f on 2026-11-10
for ADR-126 (KAN-638). Fix this file in the same PR as any change to what it states. -->

# Events, outbox, consumers and reconciliation

- **Outbox:** the business row and the outbox row are written in **one local transaction**;
  never a dual write. The outbox and consumer harness (`app/core/outbox*.py`,
  `app/core/consumer.py`, `app/core/processed_event_model.py`) is preserved verbatim.
- **Names are facts in the past tense, never commands.** New event types use
  `<context>.<entity>.<verb>` (`inventory.stock_item.purchased`, `sales.contract.confirmed`).
  Existing two-part names stay as they are (`customer.created`, `valuation.used`,
  `configuration.matched`, …): consumers subscribe by the name (`transport.register` in
  `app/worker.py`) and pending `outbox_message` rows carry it in `event_type`, so a rename is a
  contract change: a technical decision (ADR-126) — an ADR naming every subscriber and how
  pending rows migrate, and one sentence to Anto.
- **Consumers are idempotent** by `eventId` against the processed-events table; delivery is
  at-least-once. Every consumer has a redelivery-idempotency test on the Postgres lane:
  `tests/test_consumer_redelivery_idempotency.py` takes the list from `register_handlers`,
  delivers each consumer's real message twice through the transport, and fails for a
  registered consumer without a fixture there — a new consumer adds one.
- **Payload shapes are consistent** across the event types of one entity, and every envelope
  field carries meaning — a `correlationId` generated fresh per event correlates nothing
  (`app/core/outbox.py` still defaults it to a fresh `uuid7()` and no producer passes one —
  KAN-125).
- **Cross-context writes** are a call with a compensating action, never a shared transaction
  (ADR-047, CLAUDE.md rule 12) — each side commits its own work. Two contract events, never
  one (ADR-046).
- Consumers are registered in `app/worker.py::register_handlers` (event type + `consumer_name`);
  daily jobs in `register_daily_jobs` (e.g. `integration.daily_jobs`, `reconciliation.run_all`),
  run in registration order by `app/core/daily_scheduler.py`. Both run only where
  `python -m app.worker` runs — docker-compose (and CI's worker smoke test); **staging runs no
  worker** (ADR-081 option D; the SPA says so, ADR-084), so "works on staging" proves nothing
  about a consumer or a daily job. A daily job that raises is retried the same day on a backoff
  (1 min doubling to 1 h): the first failure of an episode logs ERROR, retries WARNING, a
  still-failing episode re-alarms after 24 h, and the first success logs "daily job recovered"
  (KAN-303; the episode lives in process memory). The worker reports ERROR lines to Sentry and
  exports the heartbeat's gauges over OTLP, each inert while its `DMS_*` variable is unset
  (KAN-248), as its own service `<DMS_OTEL_SERVICE_NAME>-worker` (`dms-platform-worker`;
  KAN-304) beside the web's `dms-platform`.
- **Reconciliation may read other contexts' tables, never write them** (ADR-107, Anto 2026-11-01,
  KAN-121 option B): `app/<context>/reconciliation.py` is the one place a context's SQL names
  another context's model — checking integrity across contexts is its job (rule 10) — and it is
  rewritten against `public` functions only when a context is extracted. Everywhere else the
  owner answers through `public` with plain data (`tests/architecture/test_no_cross_context_orm_query.py`).
- **Reconciliation monitors; it does not repair.** Derived state (such as a valuation's status)
  is derived on read, never "fixed" by a nightly job. Dead letters alert. A job that re-reads a
  three-column-pattern **label** from its owner (`customer.vehicle_party_labels`, KAN-84;
  `customer.advisor_labels`, KAN-403; `sales.owner_labels`, KAN-404; `sales.customer_labels`, KAN-590; `inventory.configuration_labels`, KAN-534) is not
  a repair: the label is a copy, refreshed on purpose, and its age is a gauge
  (`dms.label.age_seconds`, alert threshold in README's alarm table). A label may also follow
  its owner's event — `vehicle.vehicle_mdm.summary_changed` →
  `customer.vehicle_party_labels_on_summary_changed` (KAN-186) — whose consumer re-reads the label
  through the owner's `public` rather than trusting the payload (delivery is unordered); the
  nightly job stays as the backstop.
- **A repair the nightly run makes (ADR-047: "a failure after commit is repaired by the nightly
  reconciliation job") is a daily job of the owning context with its own record, never part of
  reconciliation** — finding and fixing stay apart (Anto, 2026-10-08, KAN-122; ADR-047
  addendum). It is registered after `reconciliation.run_all`. Three today: Stock's
  orphan-reservation sweep (`inventory.orphaned_reservations.release`; see
  `.claude/rules/inventory.md`), the expiry job's retry of a hand-over a module refused
  (`platform.user_expiry`, KAN-200, 30 days; the Sales and Customers StateChecks name the record
  meanwhile) and the end at the provider of a revoked connected-account grant still holding a
  reference (`platform.connected_account.repair`, KAN-559; the platform StateCheck names it first).
- **A context learns that another holds its record from the holder's own fact, never inside the
  holder's transaction** (KAN-270, ADR-047, rule 2): Vehicle stamps `vehicle_configuration.first_taken_at`
  from `sales.offer.configuration_attached`, `sales.contract.configuration_attached`,
  `inventory.stock_item.added`, `inventory.stock_item.configuration_attached` and `valuation.created`
  (each carrying `configurationId` when the host row has one; five consumers
  `vehicle.configuration_taken_by_*`). Its daily job `vehicle.configurations.purge_unattached`
  (registered last; retention, not a repair) asks the hosts' read-only
  `referenced_configuration_ids` as a safety net before it deletes a configuration no host ever
  took, 30 days after it was created (`vehicle-configurator.md`).
- **«Fahrzeug im Lager geändert»** (KAN-553, ADR-115): Stock publishes
  `inventory.stock_item.commercial_changed` (`inventory/services/commercial_change.py`: state before,
  `publish_if_changed` after, in the write's transaction) when list/effective price, the option set,
  colour, odometer or configuration label change — PATCH, pricing dialog, «Konfiguration zuordnen»,
  the nightly label refresh; never purchase or storno. `sales.stock_item_commercial_changed`
  (`sales/services/vehicle_change.py`) flags draft/open offers and pending contracts frozen before the
  change (`vehicle_changed_at`/`_fields`, audit row, deal row; no version bump; a contract from a
  flagged offer takes it). `POST …/{offers|contracts}/{id}/vehicle-refresh[/dismiss]` need an editable
  deal and a flag (409 `vehicle_not_changed`); refresh re-freezes with `force=True`, keeping the
  seller's line edits; dismiss keeps the accepted prices. Reconciliation reports an open deal whose
  frozen (or accepted) price differs, unflagged. After the deploy, run
  `scripts/backfill_vehicle_changed_flags.py` (dry run, then `--commit`) once per environment.
- **Stock's projection of Sales' contracts** (KAN-630, ADR-145; ADR-131: Stock imports nothing from Sales):
  `inventory_sales_contract` (inventory migration `c3b63d706c11`), fed by `sales.contract.created` /
  `.confirmed` / `.invoice_requested` / `.cancelled` (consumers `inventory.sales_contract_projection_on_*`,
  `inventory/services/sales_contract.py`); an order-free merge (status only rises, cancelled final).
  The orphan sweep and «Einkauf stornieren» read it **plus the facts the consumer has not applied**
  (no processed-event row and not published, or published before the consumers existed until a
  backfill run is recorded in `inventory_sales_contract_backfill`) — fail safe against the outbox's
  delay: an unapplied fact keeps the car reserved and refuses the storno. The outbox is read
  first, then the projection: the reverse order lets a fact applied between the two escape both. Nightly reconciliation
  reports a row differing from its contract (contract unchanged for an hour), a contract over an hour
  old with no row, and a row naming no contract or Betrieb. After the deploy, once the old worker has
  stopped, run `scripts/backfill_inventory_sales_contract_projection.py` (dry run, then `--commit`)
  once per environment.
- **Outbox now, broker later** (ADR-006): the Postgres-backed transport stays until the third
  independently deployed service exists.
