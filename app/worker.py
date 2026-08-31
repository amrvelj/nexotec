"""Outbox worker entrypoint (PR-4, Decision 3) — `python -m app.worker`.
Separate Render background-worker process from the web service: same
codebase, same database, different entrypoint. Not a second deployable in
the ADR-015 sense — same code, same database, the bounded-context
boundary is unchanged; this is process topology, not architecture.

Polls every 1s, batch 100 (Decision 4). No LISTEN/NOTIFY — that trades a
poll-interval of latency we aren't paying for against a failure mode we
would be. No graceful-shutdown/signal handling: each poll cycle is one
Postgres transaction, so a bare kill mid-cycle just rolls that one cycle
back, which the at-least-once design already tolerates by construction —
deliberate, not an oversight.
"""

import logging
import os
import time

from app.core.daily_scheduler import register_daily_job, run_due_daily_jobs
from app.core.observability import (
    record_consumer_lag_seconds,
    record_dead_letter_count,
    record_outbox_lag_seconds,
)
from app.core.outbox import consumer_lag_seconds, dead_letter_count, oldest_pending_age_seconds
from app.core.outbox_transport import InProcessTransport
from app.core.outbox_worker import poll_once
from app.db import SessionLocal
from app.integration.daily_jobs import run_daily_catalogue_sync_and_alarm
from app.inventory.consumers import handle_sales_contract_confirmed_message
from app.sales.consumers import handle_stock_item_purchased_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("app.worker")

_POLL_INTERVAL_SECONDS = 1.0
_HEARTBEAT_INTERVAL_SECONDS = 30.0


def register_handlers(transport: InProcessTransport) -> None:
    """WP-7 PR-2 registers the first real consumer here — sales emits no
    such event yet (see app.inventory.services.pipeline's own docstring),
    so this is genuinely forward-compatible infrastructure, exercised
    today only by tests/test_inventory_pipeline_consumer.py's directly-
    constructed synthetic events.
    """

    transport.register(
        "sales.contract.confirmed",
        consumer_name="inventory.sales_contract_confirmed",
        handler=handle_sales_contract_confirmed_message,
    )
    # WP-8 PR-6 — the second real consumer: sales.stock_item_purchased
    # maintains its own local is_invoiceable replica (ADR-052).
    transport.register(
        "inventory.stock_item.purchased",
        consumer_name="sales.stock_item_purchased",
        handler=handle_stock_item_purchased_message,
    )

    if os.environ.get("DMS_OUTBOX_WORKER_CI_SMOKE_TEST_PROBE") == "1":
        _register_ci_smoke_test_probe(transport)


def _register_ci_smoke_test_probe(transport: InProcessTransport) -> None:
    """CI-only, gated behind DMS_OUTBOX_WORKER_CI_SMOKE_TEST_PROBE=1 — never
    set in a real deployment. Lets the CI job that runs this actual
    process (not poll_once() called directly from pytest) have something
    to dispatch, proving the real entrypoint end to end. The handler does
    nothing beyond succeeding: outbox_message.status and processed_event
    are both real production tables, so no domain-side-effect table is
    needed for this specific proof — that coverage already lives in
    tests/test_outbox.py, which uses tests/demo_models.py's DemoWidget.
    """

    def _probe_handler(db, message):
        pass

    transport.register("test.probe.ci_smoke_test", consumer_name="ci.smoke_test_probe", handler=_probe_handler)


def _heartbeat(db, transport: InProcessTransport) -> None:
    lag = oldest_pending_age_seconds(db)
    dead = dead_letter_count(db)
    record_outbox_lag_seconds(lag)
    record_dead_letter_count(dead)

    consumer_lags: dict[str, float | None] = {}
    for consumer_name in sorted(transport.registered_consumer_names()):
        consumer_lag = consumer_lag_seconds(db, consumer_name=consumer_name)
        consumer_lags[consumer_name] = consumer_lag
        record_consumer_lag_seconds(consumer_name, consumer_lag)

    logger.info(
        "outbox heartbeat",
        extra={"oldestPendingAgeSeconds": lag, "deadLetterCount": dead, "consumerLagSeconds": consumer_lags},
    )


def register_daily_jobs() -> None:
    """WP-6 PR-4's first daily job: per-tenant catalogue delta sync plus
    the A-12 sync-age alarm, for every tenant with an enabled auto-i-dat-
    family connection. PR-6 registers its own retention/notification jobs
    the same way, alongside this one.
    """

    register_daily_job("integration.catalogue_sync_and_alarm", run_daily_catalogue_sync_and_alarm)


def run(*, max_iterations: int | None = None) -> None:
    transport = InProcessTransport(SessionLocal)
    register_handlers(transport)
    register_daily_jobs()

    iterations = 0
    last_heartbeat = 0.0
    while max_iterations is None or iterations < max_iterations:
        db = SessionLocal()
        try:
            result = poll_once(db, transport)
        finally:
            db.close()

        daily_db = SessionLocal()
        try:
            ran = run_due_daily_jobs(daily_db)
        finally:
            daily_db.close()
        if ran:
            logger.info("daily jobs ran", extra={"jobs": ran})

        if result.claimed:
            logger.info(
                "outbox poll",
                extra={
                    "claimed": result.claimed,
                    "published": result.published,
                    "retried": result.retried,
                    "dead": result.dead,
                },
            )

        now = time.monotonic()
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL_SECONDS:
            heartbeat_db = SessionLocal()
            try:
                _heartbeat(heartbeat_db, transport)
            finally:
                heartbeat_db.close()
            last_heartbeat = now

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            time.sleep(_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    _max_iterations_env = os.environ.get("DMS_OUTBOX_WORKER_MAX_ITERATIONS")
    _max_iterations = int(_max_iterations_env) if _max_iterations_env else None
    logger.info("outbox worker starting", extra={"maxIterations": _max_iterations})
    run(max_iterations=_max_iterations)
