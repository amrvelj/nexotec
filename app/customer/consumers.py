"""The customer context's own outbox consumers.

Registered in app.worker.register_handlers:

- `vehicle.vehicle_mdm.summary_changed` as
  "customer.vehicle_party_labels_on_summary_changed" (KAN-186). The event's
  contract, as vehicle-mdm publishes it
  (`app/vehicle/services/vehicle_mdm.py::publish_summary_changed_if_changed`;
  the vehicle-mdm record is the aggregate, `tenant_id` is null — the record is
  global, ADR-022): `vehicleId`, the record's `version` after the write,
  `changedFields` (camelCase names of the summary fields that changed) and
  `summary` (`vin`, `vehicleNumber`, `make`, `model`, `modelYear`, `trim` as
  they now stand).
- `inventory.stock_item.purchased` as "customer.trade_in_purchased" (KAN-189).
- `inventory.stock_item.storno` as "customer.trade_in_storno" (KAN-572).
"""

import datetime as dt
import logging
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.outbox_model import OutboxMessage
from app.customer.services.customer import (
    record_former_holder_on_purchase,
    relabel_vehicle_parties_for_vehicle,
    reopen_former_holder_after_storno,
    resolve_live_customer_id,
)
from app.inventory.public import purchase_booked_after, standing_purchase_date
from app.platform.public import get_dealership_or_404
from app.sales.public import get_trade_in_seller

logger = logging.getLogger(__name__)


def handle_vehicle_summary_changed_message(db: Session, message: OutboxMessage) -> None:
    """KAN-186 — every party row naming the vehicle takes its current label
    (CLAUDE.md rule 2's three-column pattern, KAN-84). The event says which
    vehicle; the label itself is re-read through `app.vehicle.public`, so a
    late or reordered delivery writes the current label, never an older one.
    Flushes only; consume_once commits it together with the processed_event
    row. A vehicle no party row names changes nothing."""

    relabel_vehicle_parties_for_vehicle(db, vehicle_id=message.aggregate_id)


def handle_stock_item_purchased_message(db: Session, message: OutboxMessage) -> None:
    """KAN-189 (Anto, 2026-11-02, option A): when the Betrieb books the
    purchase of a trade-in's pipeline item, the contract's customer becomes
    the car's former owner and keeper, ended at the purchase. Every other
    purchase is ignored.

    The trade-in itself creates no vehicle-mdm record (ADR-070 as amended),
    so this is the first moment the car has one to link to: the purchase
    promotes the item (ADR-103) and the event names the record
    (`vehicleId`). The contract's customer is Sales' fact, read through
    `app.sales.public`; the write is this context's own, in this consumer's
    transaction with its processed_event row (rules 4, 6 and 12: nothing is
    written in another context's transaction). Flushes only; consume_once
    commits. A customer merged since the contract is followed to its
    survivor.
    """

    payload = message.payload or {}
    if payload.get("originRole") != "trade_in" or not payload.get("originContractId"):
        return
    if message.tenant_id is None:
        raise ValueError(f"inventory.stock_item.purchased message {message.id} has no tenant_id.")
    if not payload.get("vehicleId"):
        # A purchased item is in stock, and promotion gave it its record; an
        # event without one was published before KAN-189 added the field.
        logger.warning("Trade-in purchase %s carries no vehicleId; no former owner recorded.", message.id)
        return

    # KAN-572: delivery is unordered after a failed delivery, so a purchase
    # event may arrive after its storno (or after the car was booked again).
    # Stock's fact is re-read as it is now: only the purchase that still
    # stands is recorded — a later booking publishes its own event, and a
    # car that went back to its customer must not show a handover. Accepted
    # residual (Anto, 2026-11-07, KAN-572 option A): after an unflagged
    # storno that overtook this event, no handover is recorded unless the
    # car is bought again — only ever after a failed delivery.
    standing = standing_purchase_date(db, tenant_id=message.tenant_id, stock_item_id=message.aggregate_id)
    reported = payload.get("purchaseDate")
    if standing is None or (reported is not None and standing.isoformat() != reported):
        logger.info("Trade-in purchase %s no longer stands in Stock; no former owner recorded for it.", message.id)
        return

    contract_id = uuid.UUID(payload["originContractId"])
    seller = get_trade_in_seller(db, tenant_id=message.tenant_id, contract_id=contract_id)
    if seller is None:
        logger.warning("Trade-in purchase %s: contract %s names no customer here.", message.id, contract_id)
        return
    group_id = get_dealership_or_404(db, message.tenant_id).dealer_group_id
    customer_id = resolve_live_customer_id(db, group_id=group_id, customer_id=seller.customer_id)
    if customer_id is None:
        logger.warning(
            "Trade-in purchase %s: customer %s is not a live customer of group %s.",
            message.id, seller.customer_id, group_id,
        )
        return

    purchased_by = payload.get("purchasedBy")
    record_former_holder_on_purchase(
        db,
        vehicle_id=uuid.UUID(payload["vehicleId"]),
        customer_id=customer_id,
        group_id=group_id,
        held_from=seller.known_since,
        ended_at=purchase_moment(
            payload.get("purchaseDate"), booked_at=message.occurred_at, known_since=seller.known_since
        ),
        actor_id=uuid.UUID(purchased_by) if purchased_by else None,
    )


def handle_stock_item_storno_message(db: Session, message: OutboxMessage) -> None:
    """KAN-572 (Anto, 2026-11-07, option C): Stock cancelled the purchase of a
    trade-in and said the car went back to the customer who traded it in
    (`returnedToCustomer`). That customer holds it again: the owner and
    keeper rows the purchase closed (KAN-189) are reopened. A storno that
    does not say so changes nothing here — the customer did hand the car
    over; only the dealer's booking was reversed. Nor does a stale one: a
    storno a later purchase booking overtook (Stock asked, as Sales does),
    whether that booking stands or was stornoed in turn.

    The contract's customer is Sales' fact, read through `app.sales.public`,
    and followed through a merge to its survivor, as the purchase consumer
    does. A storno that arrives before its purchase (unordered after a failed
    delivery) finds nothing to reopen; the purchase, when it arrives, no
    longer stands in Stock and records nothing (its own re-read). Flushes
    only; consume_once commits."""

    payload = message.payload or {}
    if not payload.get("returnedToCustomer"):
        return
    if payload.get("originRole") != "trade_in" or not payload.get("originContractId"):
        # Stock refuses `returnedToCustomer` for anything but a trade-in (422).
        logger.warning("Storno %s says returned to customer but names no trade-in contract.", message.id)
        return
    if message.tenant_id is None:
        raise ValueError(f"inventory.stock_item.storno message {message.id} has no tenant_id.")
    if not payload.get("vehicleId"):
        logger.warning("Trade-in storno %s carries no vehicleId; no holder reopened.", message.id)
        return
    # Delivery is unordered after a failed delivery (and a dead letter may be
    # replayed much later): a storno may arrive after the car was bought
    # again — and that booking may itself have been stornoed since. Stock is
    # asked whether a purchase was booked after this storno; if so the storno
    # is stale and says nothing about who holds the car now.
    if purchase_booked_after(
        db, tenant_id=message.tenant_id, stock_item_id=message.aggregate_id, after=message.occurred_at
    ):
        logger.info("Trade-in storno %s: a later purchase overtook it; no holder reopened.", message.id)
        return

    contract_id = uuid.UUID(payload["originContractId"])
    seller = get_trade_in_seller(db, tenant_id=message.tenant_id, contract_id=contract_id)
    if seller is None:
        logger.warning("Trade-in storno %s: contract %s names no customer here.", message.id, contract_id)
        return
    group_id = get_dealership_or_404(db, message.tenant_id).dealer_group_id
    customer_id = resolve_live_customer_id(db, group_id=group_id, customer_id=seller.customer_id)
    if customer_id is None:
        logger.warning(
            "Trade-in storno %s: customer %s is not a live customer of group %s.",
            message.id, seller.customer_id, group_id,
        )
        return

    stornoed_by = payload.get("stornoedBy")
    reopen_former_holder_after_storno(
        db,
        vehicle_id=uuid.UUID(payload["vehicleId"]),
        customer_id=customer_id,
        group_id=group_id,
        closed_after=seller.known_since,
        closed_until=message.occurred_at,
        actor_id=uuid.UUID(stornoed_by) if stornoed_by else None,
    )


def purchase_moment(purchase_date: str | None, *, booked_at: dt.datetime, known_since: dt.datetime) -> dt.datetime:
    """When the customer stopped holding the car (Anto, 2026-11-03, decision
    2 A): the start of the purchase date in Switzerland — the date the dealer
    booked as the purchase — or the booking itself when that date is missing,
    not after the moment the trade-in was known (a purchase dated the day the
    contract was signed), or still to come. Never later than the booking: a
    row ended in the future would be a current holder, and this path closes
    nobody (KAN-148, KAN-370)."""

    if purchase_date:
        start = dt.datetime.combine(
            dt.date.fromisoformat(purchase_date), dt.time(), tzinfo=ZoneInfo(get_settings().session_timezone)
        ).astimezone(dt.UTC)
        if known_since < start <= booked_at:
            return start
    return booked_at
