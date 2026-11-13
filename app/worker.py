"""Outbox worker entrypoint (PR-4, Decision 3) — `python -m app.worker`.
A separate background process from the web service: same codebase, same
database, different entrypoint. Provisioned as docker-compose's `worker`
service; on Render's staging it is declared in render.yaml but commented
out and not provisioned (KAN-234, ADR-081). Not a second deployable in
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
import uuid

from sqlalchemy.orm import Session

from app.core.daily_scheduler import register_daily_job, run_due_daily_jobs
from app.core.deployment_environment import require_valid_deployment_environment
from app.core.idempotent_route import run_daily_idempotency_record_purge
from app.core.observability import (
    configure_logging,
    configure_sentry,
    configure_tracing,
    record_consumer_lag_seconds,
    record_dead_letter_count,
    record_label_age_seconds,
    record_outbox_lag_seconds,
    record_reconciliation_age_seconds,
)
from app.core.outbox import consumer_lag_seconds, dead_letter_count, oldest_pending_age_seconds
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.outbox_worker import poll_once
from app.core.reconciliation import seconds_since_last_reconciliation
from app.customer.consumers import (
    TradeInSeller,
    handle_stock_item_booking_purchased_message,
    handle_stock_item_booking_storno_message,
    handle_vehicle_summary_changed_message,
)
from app.customer.consumers import handle_stock_item_purchased_message as handle_trade_in_purchased_message
from app.customer.consumers import handle_stock_item_storno_message as handle_trade_in_storno_message
from app.customer.public import (
    oldest_advisor_label_age_seconds,
    oldest_vehicle_party_label_age_seconds,
    refresh_advisor_labels,
    refresh_vehicle_party_labels,
)
from app.customer.services.vehicle_merge import handle_vehicle_merged_message
from app.db import SessionLocal
from app.integration.daily_jobs import run_daily_integration_jobs
from app.inventory.consumers import (
    handle_sales_contract_cancelled_message,
    handle_sales_contract_confirmed_message,
    handle_sales_contract_fact_message,
    handle_stock_item_published_message,
    handle_stock_item_unpublished_message,
)
from app.inventory.daily_jobs import run_daily_reservation_sweep
from app.inventory.public import (
    SALES_CONTRACT_PROJECTION_CONSUMERS,
    oldest_configuration_label_age_seconds,
    refresh_configuration_labels,
)
from app.inventory.public import referenced_configuration_ids as referenced_by_stock_items
from app.ownership_registry import register_ownership_providers
from app.platform.consumers import (
    handle_connected_account_revoked_message,
    handle_digest_released_message,
    handle_notification_delivered_message,
)
from app.platform.daily_jobs import (
    run_daily_notification_digest,
    run_daily_notification_purge,
    run_daily_revoked_grant_repair,
    run_daily_user_expiry,
)
from app.reconciliation_runner import run_all_daily
from app.sales.consumers import (
    handle_customer_merged_message,
    handle_stock_item_added_message,
    handle_stock_item_commercial_changed_message,
    handle_stock_item_purchased_message,
    handle_stock_item_storno_message,
)
from app.sales.public import (
    get_trade_in_seller,
    oldest_customer_label_age_seconds,
    oldest_owner_label_age_seconds,
    refresh_customer_labels,
    refresh_owner_labels,
)
from app.sales.public import referenced_configuration_ids as referenced_by_offers_and_contracts
from app.valuation.public import referenced_configuration_ids as referenced_by_valuations
from app.vehicle.consumers import handle_configuration_taken_message, handle_invoice_requested_message
from app.vehicle.daily_jobs import run_daily_unattached_configuration_purge
from app.vehicle.public import run_daily_plate_lookup_purge

logger = logging.getLogger("app.worker")

_POLL_INTERVAL_SECONDS = 1.0
_HEARTBEAT_INTERVAL_SECONDS = 30.0


def _trade_in_seller_from_sales(db: Session, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> TradeInSeller | None:
    """KAN-632 (ADR-131): the seller of a trade-in whose purchase or storno
    event does not carry it — published before KAN-632, or of a pipeline item
    created before it, when Sales' confirmation named nobody to keep. Asked
    here, at the composition root, because customer sits below sales and
    never imports it; the answer is the one the customer consumers read
    before (`sales.public.get_trade_in_seller`, a SELECT only, in the
    consumer's transaction — nothing of Sales' is written). Once no trade-in
    item without a kept seller remains unbought and no such event is
    undelivered, this fallback can go (KAN-674)."""

    seller = get_trade_in_seller(db, tenant_id=tenant_id, contract_id=contract_id)
    return None if seller is None else TradeInSeller(customer_id=seller.customer_id, known_since=seller.known_since)


def _customer_trade_in_purchased(db: Session, message: OutboxMessage) -> None:
    handle_trade_in_purchased_message(db, message, seller_of_old_event=_trade_in_seller_from_sales)


def _customer_trade_in_storno(db: Session, message: OutboxMessage) -> None:
    handle_trade_in_storno_message(db, message, seller_of_old_event=_trade_in_seller_from_sales)


def configure_observability() -> None:
    """KAN-248 — the set-up app/main.py runs for the web process: JSON log
    lines, Sentry (an ERROR log line becomes an event, so the reconciliation
    alarm, a failing daily job and a failed reservation release reach
    someone) and the OTLP providers (so the heartbeat's alarm gauges are
    exported). Each is inert while its DMS_* variable is unset. Called at
    startup, not at import, so importing this module (the tests do) changes
    no process-wide state."""

    configure_logging()
    configure_sentry()
    # KAN-304: reported as its own service, never under the web's name.
    configure_tracing(process="worker")


def register_handlers(transport: InProcessTransport) -> None:
    """WP-7 PR-2 registered the first real consumer here: inventory turns
    `sales.contract.confirmed` (emitted by app.sales.services.contract.
    confirm_contract) into pipeline stock items — and, since KAN-101, gives
    a trade-in's item its valuation pointer.
    """

    transport.register(
        "sales.contract.confirmed",
        consumer_name="inventory.sales_contract_confirmed",
        handler=handle_sales_contract_confirmed_message,
    )
    # KAN-158 — Stock releases what a cancelled contract still holds: the
    # reservation it made itself on a manual configuration's pipeline item.
    transport.register(
        "sales.contract.cancelled",
        consumer_name="inventory.sales_contract_cancelled",
        handler=handle_sales_contract_cancelled_message,
    )
    # KAN-630 — Stock's projection of Sales' contracts, which its orphan sweep
    # and «Einkauf stornieren» read instead of asking Sales (ADR-131).
    for event_type, consumer_name in SALES_CONTRACT_PROJECTION_CONSUMERS.items():
        transport.register(event_type, consumer_name=consumer_name, handler=handle_sales_contract_fact_message)
    # WP-8 PR-6 — the second real consumer: sales.stock_item_purchased
    # maintains its own local is_invoiceable replica (ADR-052).
    transport.register(
        "inventory.stock_item.purchased",
        consumer_name="sales.stock_item_purchased",
        handler=handle_stock_item_purchased_message,
    )
    # KAN-146 (ADR-052: storno "sets it back") — a stornoed purchase deletes
    # Sales' replica row, so every contract on the car derives «nicht
    # fakturierbar» until it is bought again.
    transport.register(
        "inventory.stock_item.storno",
        consumer_name="sales.stock_item_storno",
        handler=handle_stock_item_storno_message,
    )
    # KAN-553 (ADR-115) — Stock changed a car's commercial data: Sales
    # flags the open offers and pending contracts on it.
    transport.register(
        "inventory.stock_item.commercial_changed",
        consumer_name="sales.stock_item_commercial_changed",
        handler=handle_stock_item_commercial_changed_message,
    )
    # KAN-185 (ADR-047; Anto, 2026-10-31, option A) — a customer merge moves
    # Sales' records of the duplicate (legacy transactions, offers,
    # contracts, deals) to the survivor in Sales' own transaction; the merge
    # writes nothing of Sales'. A failure is redelivered on the outbox's
    # backoff and dead-lettered (with its alert) after the last attempt.
    transport.register(
        "customer.merged",
        consumer_name="sales.customer_merged",
        handler=handle_customer_merged_message,
    )
    # KAN-632 (ADR-147) — the customer context's record of Stock's bookings,
    # which the two trade-in consumers below judge an event's order by. Its
    # own consumers, so a failing trade-in step never loses the row; and
    # registered first: the transport runs one event type's consumers in
    # registration order, each in its own committed transaction, so an
    # event's booking row is there before its own trade-in step runs.
    transport.register(
        "inventory.stock_item.purchased",
        consumer_name="customer.stock_item_bookings_on_purchased",
        handler=handle_stock_item_booking_purchased_message,
    )
    transport.register(
        "inventory.stock_item.storno",
        consumer_name="customer.stock_item_bookings_on_storno",
        handler=handle_stock_item_booking_storno_message,
    )
    # KAN-189 (Anto, 2026-11-02, option A) — a bought trade-in's customer is
    # recorded as the car's former owner and keeper, in the customer
    # context's own transaction.
    transport.register(
        "inventory.stock_item.purchased",
        consumer_name="customer.trade_in_purchased",
        handler=_customer_trade_in_purchased,
    )
    # KAN-572 (Anto, 2026-11-07, option C) — a trade-in whose purchase was
    # stornoed and which went back to its customer reopens that customer as
    # owner and keeper, in the customer context's own transaction.
    transport.register(
        "inventory.stock_item.storno",
        consumer_name="customer.trade_in_storno",
        handler=_customer_trade_in_storno,
    )
    # KAN-144 — a manual configuration's contract learns the pipeline stock
    # item its confirmation created, so the purchase gate can apply to it.
    transport.register(
        "inventory.stock_item.added",
        consumer_name="sales.stock_item_added",
        handler=handle_stock_item_added_message,
    )
    # KAN-513 — a second contract on the same configuration reuses the
    # ordered car while it is free on stock; Sales links it the same way.
    transport.register(
        "inventory.stock_item.reassigned",
        consumer_name="sales.stock_item_reassigned",
        handler=handle_stock_item_added_message,
    )
    # KAN-27 (WP-7, ADR-062) — the first consumer to perform outbound I/O
    # to a third party. Both events funnel into the same full-feed
    # recompute (marketplace_transmission.assemble_and_transmit) — see
    # app.inventory.consumers's own docstring for why unpublish needs a
    # consumer at all when it emits the "same" minimal payload as publish.
    transport.register(
        "inventory.stock_item.published",
        consumer_name="inventory.marketplace_transmission_on_publish",
        handler=handle_stock_item_published_message,
    )
    transport.register(
        "inventory.stock_item.unpublished",
        consumer_name="inventory.marketplace_transmission_on_unpublish",
        handler=handle_stock_item_unpublished_message,
    )
    # KAN-523 — the plate a sold car leaves with, entered at «Rechnung
    # anfordern», is recorded on its vehicle-mdm record in the group register.
    transport.register(
        "sales.contract.invoice_requested",
        consumer_name="vehicle.plate_on_invoice_requested",
        handler=handle_invoice_requested_message,
    )
    # KAN-186 — a vehicle's changed label (a VIN correction, a new first
    # registration) reaches every party row naming it when the event is
    # delivered; the nightly customer.vehicle_party_labels stays the backstop.
    transport.register(
        "vehicle.vehicle_mdm.summary_changed",
        consumer_name="customer.vehicle_party_labels_on_summary_changed",
        handler=handle_vehicle_summary_changed_message,
    )
    # KAN-634 (ADR-131) — a vehicle merge (FR-V-12) moves customer's party
    # rows from the duplicate onto the survivor when the fact is delivered;
    # vehicle publishes it and never calls customer.
    transport.register(
        "vehicle.vehicle_mdm.merged",
        consumer_name="customer.vehicle_parties_on_vehicle_merged",
        handler=handle_vehicle_merged_message,
    )
    # KAN-270 — a host took a configuration: vehicle stamps it taken, so the
    # daily purge of configurations no host ever took keeps it for good.
    for event_type, consumer_name in (
        ("sales.offer.configuration_attached", "vehicle.configuration_taken_by_offer"),
        ("sales.contract.configuration_attached", "vehicle.configuration_taken_by_contract"),
        ("inventory.stock_item.added", "vehicle.configuration_taken_by_added_stock_item"),
        ("inventory.stock_item.configuration_attached", "vehicle.configuration_taken_by_stock_item"),
        ("valuation.created", "vehicle.configuration_taken_by_valuation"),
    ):
        transport.register(event_type, consumer_name=consumer_name, handler=handle_configuration_taken_message)
    # KAN-216 (D-A-40, FR-A-49/50) — Web Push is a pointer to the inbox: the
    # title and link of a delivered notification, and one pointer per digest.
    transport.register(
        "platform.notification.delivered",
        consumer_name="platform.web_push_on_notification",
        handler=handle_notification_delivered_message,
    )
    transport.register(
        "platform.notification_digest.released",
        consumer_name="platform.web_push_on_digest",
        handler=handle_digest_released_message,
    )
    # KAN-412 (FR-AU-24) — a revoked connected account is ended at the
    # provider by its registered revoker; a refusal is retried, then
    # dead-letters.
    transport.register(
        "platform.connected_account.revoked",
        consumer_name="platform.connected_account_provider_revocation",
        handler=handle_connected_account_revoked_message,
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

    reconciliation_age = seconds_since_last_reconciliation(db)
    record_reconciliation_age_seconds(reconciliation_age)

    vehicle_party_label_age = oldest_vehicle_party_label_age_seconds(db)
    record_label_age_seconds("vehicle_party.vehicle", vehicle_party_label_age)
    advisor_label_age = oldest_advisor_label_age_seconds(db)
    record_label_age_seconds("customer.advisor", advisor_label_age)
    owner_label_age = oldest_owner_label_age_seconds(db)
    record_label_age_seconds("sales.owner", owner_label_age)
    customer_label_age = oldest_customer_label_age_seconds(db)
    record_label_age_seconds("sales.customer", customer_label_age)
    configuration_label_age = oldest_configuration_label_age_seconds(db)
    record_label_age_seconds("inventory.configuration", configuration_label_age)

    logger.info(
        "outbox heartbeat",
        extra={
            "oldestPendingAgeSeconds": lag,
            "deadLetterCount": dead,
            "consumerLagSeconds": consumer_lags,
            "reconciliationAgeSeconds": reconciliation_age,
            "vehiclePartyLabelAgeSeconds": vehicle_party_label_age,
            "advisorLabelAgeSeconds": advisor_label_age,
            "ownerLabelAgeSeconds": owner_label_age,
            "salesCustomerLabelAgeSeconds": customer_label_age,
            "configurationLabelAgeSeconds": configuration_label_age,
        },
    )


# KAN-633 (ADR-131): every context whose rows store a configuration id, handed
# to vehicle's purge — vehicle sits below them and names none. A new host
# (aftersales, parts) adds its `referenced_configuration_ids` here, its
# `configuration_attached` fact, and the reconciliation check that its rows
# name no configuration vehicle has not stamped as taken.
CONFIGURATION_HOSTS = (
    referenced_by_offers_and_contracts,
    referenced_by_stock_items,
    referenced_by_valuations,
)


def _purge_unattached_configurations(db: Session) -> None:
    run_daily_unattached_configuration_purge(db, held_by_hosts=CONFIGURATION_HOSTS)


def _refresh_vehicle_party_labels(db: Session) -> None:
    changed = refresh_vehicle_party_labels(db)
    logger.info("customer.vehicle_party_labels: %d party row(s) relabelled", changed)


def _refresh_advisor_labels(db: Session) -> None:
    changed = refresh_advisor_labels(db)
    logger.info("customer.advisor_labels: %d customer(s) relabelled", changed)


def _refresh_owner_labels(db: Session) -> None:
    changed = refresh_owner_labels(db)
    logger.info("sales.owner_labels: %d offer, contract or deal row(s) relabelled", changed)


def _refresh_customer_labels(db: Session) -> None:
    changed = refresh_customer_labels(db)
    logger.info("sales.customer_labels: %d offer, contract or deal row(s) relabelled", changed)


def _refresh_configuration_labels(db: Session) -> None:
    changed = refresh_configuration_labels(db)
    logger.info("inventory.configuration_labels: %d stock item(s) relabelled", changed)


def register_ownership() -> None:
    """KAN-200: the expiry job hands a leaver's records over through the
    ownership registry, which the composition root fills."""

    register_ownership_providers()


def register_daily_jobs() -> None:
    """Fifteen daily jobs, run in registration order once per day on this
    process (app.core.daily_scheduler):

    1. ``integration.daily_jobs`` — ADR-025's expiry warnings, ADR-024's
       retention purge and the support digest (PR-6). One composition root
       (app/integration/daily_jobs.py), one registration. It ran the
       catalogue delta sync too until no provider was asked for its whole
       catalogue any more (2026-10-29, KAN-485).
    2. ``customer.vehicle_party_labels`` — KAN-84: re-reads every
       VehicleParty's denormalised vehicle label (VIN, number, make, model,
       year, trim) through app.vehicle.public, so a catalogue match or VIN
       correction made after the link shows within a day.
    3. ``customer.advisor_labels`` — KAN-403: re-reads the name of every
       customer's advisor through app.platform.public, so a renamed user's
       new name reaches the customer cards within a day. A deactivated
       user's label is kept as it was.
    4. ``sales.owner_labels`` — KAN-404: the same for the responsible
       seller's name on every offer, contract and deal row (KAN-200's
       ``owner_label``), so a renamed seller's new name reaches the Sales grid
       within a day.
    5. ``sales.customer_labels`` — KAN-590: the same for the customer's
       name (and correspondence language) on open work — an offer no
       signed contract was made from, a pending contract and their deal
       rows — through
       app.customer.public, so a corrected name reaches the Sales list and the
       next document within a day. A signed contract keeps the name the buyer
       signed under (Anto, 2026-11-10).
    6. ``inventory.configuration_labels`` — KAN-534: the same for Stock's
       copy of each stock item's configuration label, so a configuration
       edited after it was attached («Konfiguration bearbeiten», KAN-510)
       shows its new label in the stock list's «Konfiguration» column within
       a day. The item's own ``vehicle_label`` («Fahrzeug») is not touched.
    7. ``reconciliation.run_all`` — the nightly cross-context reconciliation
       (P-10, CLAUDE.md rule 10). There are no cross-context foreign keys
       anywhere in this codebase, so the database cannot detect a dangling
       reference; this job walking every context's ReferenceCheck list is
       the entire compensating control. A finding is logged and swallowed (see
       app.reconciliation_runner.run_all_daily), never re-run the same day.
    8. ``inventory.orphaned_reservations.release`` — KAN-122: Stock frees a
       car whose reservation no longer has a signed contract behind it
       (app.inventory.daily_jobs). A repair, so not part of the read-only
       reconciliation; after it, so that, when reconciliation ran first that
       day, a reservation naming a missing contract is recorded as a
       dangling reference before it is released.
    9. ``vehicle.plate_lookup_cache.purge`` — deletes plate-lookup cache rows
       past their 30-day TTL (KAN-42, FR-C-02; revDSG data minimisation).
    10. ``core.idempotency_records.purge`` — deletes the Idempotency-Key
       records of HTTP requests older than 24 hours (KAN-119): a retry comes
       within minutes, and a stored response can carry personal data. The
       records a context keeps under its own path (inventory's
       ``inventory.reserve:<item>``) are left alone.
    11. ``platform.user_expiry`` — KAN-200 (FR-A-12): a user whose
       ``valid_to`` has passed is deactivated and their open records released
       to the modules' queues; a release a module refused is retried for 30
       days (a repair of the owning step, after reconciliation like Stock's
       sweep, so the night's report still names the record first).
    12. ``platform.notifications.digest`` — KAN-216 (FR-A-48): notifications
       held for a family set to ``digest`` reach the inbox; one push each.
    13. ``platform.notifications.purge`` — KAN-216 (FR-A-50, FR-A-49): the
       inbox keeps 90 days; no push subscription outlives a revoked session.
    14. ``platform.connected_account.repair`` — KAN-559 (FR-AU-24, ADR-118):
       every revoked connected-account grant still holding a secret reference
       is ended at the provider again (an outage longer than the outbox's
       retries, a grant revoked before its revoker was registered). A repair,
       so after reconciliation: the night's report names the grant first.
    15. ``vehicle.configurations.purge_unattached`` — KAN-270 (Anto,
       2026-11-03, option C): a configuration no host ever took is deleted
       30 days after it was created, with an audit row and
       ``vehicle.configuration.discarded``; one a host holds is stamped taken
       and kept for good (app.vehicle.daily_jobs; the hosts it asks are
       ``CONFIGURATION_HOSTS``, KAN-633). Retention, not a repair.
    """

    register_daily_job("integration.daily_jobs", run_daily_integration_jobs)
    register_daily_job("customer.vehicle_party_labels", _refresh_vehicle_party_labels)
    register_daily_job("customer.advisor_labels", _refresh_advisor_labels)
    register_daily_job("sales.owner_labels", _refresh_owner_labels)
    register_daily_job("sales.customer_labels", _refresh_customer_labels)
    register_daily_job("inventory.configuration_labels", _refresh_configuration_labels)
    register_daily_job("reconciliation.run_all", run_all_daily)
    register_daily_job("inventory.orphaned_reservations.release", run_daily_reservation_sweep)
    register_daily_job("vehicle.plate_lookup_cache.purge", run_daily_plate_lookup_purge)
    register_daily_job("core.idempotency_records.purge", run_daily_idempotency_record_purge)
    # 11. KAN-200 (FR-A-12): a user whose `valid_to` has passed is deactivated
    #    and their open records released to the modules' queues.
    register_daily_job("platform.user_expiry", run_daily_user_expiry)
    # 12./13. KAN-216 (FR-A-48, FR-A-50): the day's digests reach the inbox
    #    with one push each; the inbox keeps 90 days.
    register_daily_job("platform.notifications.digest", run_daily_notification_digest)
    register_daily_job("platform.notifications.purge", run_daily_notification_purge)
    # 15. KAN-270 (registered last): a configuration no host ever took goes after 30 days.
    # 14. KAN-559: a revoked grant still holding a reference is ended at the provider.
    register_daily_job("platform.connected_account.repair", run_daily_revoked_grant_repair)
    register_daily_job("vehicle.configurations.purge_unattached", _purge_unattached_configurations)


def run(*, max_iterations: int | None = None) -> None:
    transport = InProcessTransport(SessionLocal)
    register_handlers(transport)
    register_ownership()
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
    configure_observability()
    # KAN-499, as app/main.py; after observability, so a refused start is reported.
    require_valid_deployment_environment()
    _max_iterations_env = os.environ.get("DMS_OUTBOX_WORKER_MAX_ITERATIONS")
    _max_iterations = int(_max_iterations_env) if _max_iterations_env else None
    logger.info("outbox worker starting", extra={"maxIterations": _max_iterations})
    run(max_iterations=_max_iterations)
