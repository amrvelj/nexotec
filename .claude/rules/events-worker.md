---
paths:
  - "app/worker.py"
  - "app/reconciliation_runner.py"
  - "app/core/outbox*.py"
  - "app/core/consumer.py"
  - "app/core/reconciliation*.py"
  - "app/core/daily_scheduler*.py"
  - "app/core/observability.py"
  - "app/*/daily_jobs.py"
  - "app/*/consumers.py"
  - "app/**/*consumer*.py"
  - "app/**/*reconciliation*.py"
  - "app/*/services/*label*.py"
  - "app/platform/*/services/*label*.py"
  - "scripts/*outbox*.py"
  - "tests/cross_context/test_consumer_redelivery_idempotency.py"
  - "tests/**/test_*label*.py"
  - "tests/core/test_outbox.py"
  - "tests/core/test_daily_scheduler.py"
  - "tests/core/test_worker_observability.py"
  - "tests/cross_context/test_reconciliation*.py"
---
<!-- Maintainer note. Moved out of events.md by KAN-615 (2026-11-13) so the broad file stays
small; ADR-047 addendum, ADR-081, ADR-105, ADR-107. Last verified against main@32ad509 (2026-11-25); planning's jobs added by KAN-808 PR-1b. -->

# The worker, daily jobs and label refreshes

- **Registration:** consumers in `app/worker.py::register_handlers` (event type +
  `consumer_name`), daily jobs in `register_daily_jobs`, run in registration order by
  `app/core/daily_scheduler.py`. A repair is registered after `reconciliation.run_all`;
  Vehicle's configuration purge is last (retention, not a repair).
- **A daily job that raises is retried the same day**, 1 min doubling to 1 h: the first failure
  of an episode logs ERROR, retries WARNING, a still-failing episode re-alarms after 24 h, the
  first success logs "daily job recovered". The episode lives in process memory.
- The worker reports ERROR lines to Sentry and exports the heartbeat's gauges over OTLP, each
  inert while its `DMS_*` variable is unset, as its own service `<DMS_OTEL_SERVICE_NAME>-worker`
  beside the web's (ADR-105).
- **No query string leaves the process** (KAN-829, revDSG): a search's `?q=` carries names, phones, e-mails, UIDs.
  `app/core/observability.py` scrubs it from uvicorn's access line (`QueryStringAccessLogFilter`, on the
  `uvicorn.access` logger whatever the start command), from every server span (`instrument_app`'s request hook)
  and from Sentry events (`before_send`: the query string and the `Referer`'s query — the same-origin SPA writes a
  list's search into its own URL); `tests/core/test_kan829_no_query_in_logs_or_traces.py` starts the server as
  production does.
- **Repairs today** (each the owner's daily job with its own record): Stock's orphan-reservation
  sweep `inventory.orphaned_reservations.release` (`inventory.md`); Stock's `inventory.merged_record_items.repair` (KAN-724: re-runs the merge consumer's move for an item left on a merged record, once nothing blocks it); `platform.user_expiry`'s
  retry of a hand-over a module refused (30 days; the Sales and Contacts StateChecks name the
  record meanwhile); `platform.connected_account.repair`, ending at the provider a revoked grant
  still holding a reference (the platform StateCheck names it first).
- **A label refresh is not a repair.** A three-column-pattern label is a copy, refreshed on
  purpose; its age is the gauge `dms.label.age_seconds` (threshold in README's alarm table).
  Jobs: `contact.vehicle_party_labels`, `contact.advisor_labels`, `sales.owner_labels`,
  `sales.contact_labels`, `inventory.configuration_labels`, `integration.location_labels`
  (KAN-222: a connection's and, KAN-226, a Business Profile binding's copy of its location's name), `inventory.location_labels` (KAN-739: a
  stock item's copy of its location's name), `inventory.supplier_labels` (KAN-465: its supplier contact's), `planning.person_labels` and
  `planning.location_labels` (KAN-808: a planner resource's user and location names). Shared rules: no `version` bump, a
  current label only restamped, a deactivated user's label kept, an id naming nothing left
  alone, batches `FOR NO KEY UPDATE SKIP LOCKED` (the vehicle-party job locks in id order and
  waits — `contact-vehicle-party.md`).
- **A label may also follow its owner's event** (`vehicle.vehicle_mdm.summary_changed` →
  `contact.vehicle_party_labels_on_summary_changed`): the consumer re-reads the label through
  the owner's `public`, never trusting the payload (delivery is unordered); the nightly job stays
  the backstop.
- **Vehicle keeps a configuration a host took** from the hosts' facts (five consumers
  `vehicle.configuration_taken_by_*`); `vehicle.configurations.purge_unattached` asks the hosts'
  read-only `referenced_configuration_ids` before deleting one never taken, 30 days after
  creation (`vehicle-configurator.md`).
