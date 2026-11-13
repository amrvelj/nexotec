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
- `inventory.stock_item.purchased` as "customer.stock_item_bookings_on_purchased"
  and `inventory.stock_item.storno` as "customer.stock_item_bookings_on_storno"
  (KAN-632): the booking record and nothing else, registered before the
  trade-in consumers of the same event.
- `inventory.stock_item.purchased` as "customer.trade_in_purchased" (KAN-189).
- `inventory.stock_item.storno` as "customer.trade_in_storno" (KAN-572).

KAN-632 (ADR-131): customer sits below sales and inventory and imports
neither. The two trade-in consumers take what they need from the event —
who traded the car in and when the contract was signed
(`tradeInCustomerId`, `tradeInSignedAt`, Stock's copy of
`sales.contract.confirmed`'s `customerId` / `signedAt`) — and judge whether
the event was overtaken from this context's own record of Stock's bookings
(`services/stock_item_booking.py`). An event without the seller (published
before KAN-632, or of a trade-in whose pipeline item was created before it)
is answered by `seller_of_old_event`, which the composition root hands in
(`app/worker.py` asks Sales); without one such an event fails loudly, so it
is redelivered and dead-lettered with its alert, never silently dropped.
"""

import dataclasses
import datetime as dt
import logging
import uuid
from collections.abc import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.outbox_model import OutboxMessage
from app.customer.models.stock_item_booking import StockItemBookingKind
from app.customer.services.stock_item_booking import (
    lock_stock_item,
    purchase_overtaken,
    record_booking,
    storno_overtaken,
)
from app.customer.services.trade_in_holders import record_former_holder_on_purchase, reopen_former_holder_after_storno
from app.customer.services.vehicle_party import resolve_live_customer_id
from app.customer.services.vehicle_party_labels import relabel_vehicle_parties_for_vehicle
from app.platform.public import get_dealership_or_404

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class TradeInSeller:
    """The customer who traded the car in, and since when the system knows
    they held it: the contract's signature (KAN-189)."""

    customer_id: uuid.UUID
    known_since: dt.datetime


SellerOfOldEvent = Callable[[Session, uuid.UUID, uuid.UUID], TradeInSeller | None]
"""(db, tenant_id, contract_id) -> the trade-in's seller, for an event that
does not carry it; None when the Betrieb has no such contract or it names no
customer."""


class OldTradeInEventWithoutSellerLookup(RuntimeError):
    """An event without `tradeInCustomerId` reached a consumer that was given
    no `seller_of_old_event` — a wiring fault, raised so the event is retried
    and dead-lettered rather than dropped."""


def _trade_in_seller(
    db: Session, message: OutboxMessage, *, contract_id: uuid.UUID, seller_of_old_event: SellerOfOldEvent | None
) -> TradeInSeller | None:
    """The seller the event names (KAN-632), else the old event's answer."""

    payload = message.payload or {}
    customer_id, signed_at = payload.get("tradeInCustomerId"), payload.get("tradeInSignedAt")
    if customer_id and signed_at:
        return TradeInSeller(customer_id=uuid.UUID(customer_id), known_since=dt.datetime.fromisoformat(signed_at))
    if seller_of_old_event is None:
        raise OldTradeInEventWithoutSellerLookup(
            f"{message.event_type} message {message.id} names no trade-in seller and no lookup was given."
        )
    assert message.tenant_id is not None
    return seller_of_old_event(db, message.tenant_id, contract_id)


def handle_vehicle_summary_changed_message(db: Session, message: OutboxMessage) -> None:
    """KAN-186 — every party row naming the vehicle takes its current label
    (CLAUDE.md rule 2's three-column pattern, KAN-84). The event says which
    vehicle; the label itself is re-read through `app.vehicle.public`, so a
    late or reordered delivery writes the current label, never an older one.
    Flushes only; consume_once commits it together with the processed_event
    row. A vehicle no party row names changes nothing."""

    relabel_vehicle_parties_for_vehicle(db, vehicle_id=message.aggregate_id)


def _record_booking(db: Session, message: OutboxMessage, kind: StockItemBookingKind) -> None:
    if message.tenant_id is None:
        return
    lock_stock_item(db, stock_item_id=message.aggregate_id)
    record_booking(db, message=message, kind=kind)


def handle_stock_item_booking_purchased_message(db: Session, message: OutboxMessage) -> None:
    """KAN-632 — every purchase event, trade-in or not, is recorded in this
    context's booking record, in a consumer of its own: a trade-in step that
    fails (retried, dead-lettered) never takes the row with it, so the
    ordering guard of the trade-in consumers always knows the booking.
    Registered before `customer.trade_in_purchased`; commit-free."""

    _record_booking(db, message, StockItemBookingKind.PURCHASED)


def handle_stock_item_booking_storno_message(db: Session, message: OutboxMessage) -> None:
    """KAN-632 — every storno event, returned or not, is recorded in the
    booking record, as the purchase's is. Registered before
    `customer.trade_in_storno`; commit-free."""

    _record_booking(db, message, StockItemBookingKind.STORNOED)


def handle_stock_item_purchased_message(
    db: Session, message: OutboxMessage, *, seller_of_old_event: SellerOfOldEvent | None = None
) -> None:
    """KAN-189 (Anto, 2026-11-02, option A): when the Betrieb books the
    purchase of a trade-in's pipeline item, the contract's customer becomes
    the car's former owner and keeper, ended at the purchase. Every other
    purchase is ignored.

    The trade-in itself creates no vehicle-mdm record (ADR-070 as amended),
    so this is the first moment the car has one to link to: the purchase
    promotes the item (ADR-103) and the event names the record
    (`vehicleId`). The contract's customer and signature are Sales' facts,
    carried by the event since KAN-632 (`tradeInCustomerId`,
    `tradeInSignedAt`; an older event's come from `seller_of_old_event`); the
    write is this context's own, in this consumer's transaction with its
    processed_event row (rules 4, 6 and 12: nothing is written in another
    context's transaction). Flushes only; consume_once commits. A customer
    merged since the contract is followed to its survivor.

    Whether the purchase was overtaken is judged from the booking record
    (KAN-632), which `customer.stock_item_bookings_on_*` keep; this consumer
    writes none of it.
    """

    payload = message.payload or {}
    is_trade_in = payload.get("originRole") == "trade_in" and bool(payload.get("originContractId"))
    if message.tenant_id is None:
        if is_trade_in:
            raise ValueError(f"inventory.stock_item.purchased message {message.id} has no tenant_id.")
        return
    lock_stock_item(db, stock_item_id=message.aggregate_id)
    overtaken = purchase_overtaken(db, message=message)
    if not is_trade_in:
        return
    if not payload.get("vehicleId"):
        # A purchased item is in stock, and promotion gave it its record; an
        # event without one was published before KAN-189 added the field.
        logger.warning("Trade-in purchase %s carries no vehicleId; no former owner recorded.", message.id)
        return

    # KAN-572: delivery is unordered after a failed delivery, so a purchase
    # event may arrive after its storno (or after the car was booked again).
    # KAN-632: judged by the bookings this context has learnt from Stock's
    # events (`services/stock_item_booking.py`), never by asking Stock: a
    # purchase a later storno or booking overtook no longer stands — a later
    # booking publishes its own event, and a car that went back to its
    # customer must not show a handover.
    if overtaken:
        logger.info("Trade-in purchase %s was overtaken by a later booking; no former owner recorded.", message.id)
        return

    contract_id = uuid.UUID(payload["originContractId"])
    seller = _trade_in_seller(db, message, contract_id=contract_id, seller_of_old_event=seller_of_old_event)
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


def handle_stock_item_storno_message(
    db: Session, message: OutboxMessage, *, seller_of_old_event: SellerOfOldEvent | None = None
) -> None:
    """KAN-572 (Anto, 2026-11-07, option C): Stock cancelled the purchase of a
    trade-in and said the car went back to the customer who traded it in
    (`returnedToCustomer`). That customer holds it again: the owner and
    keeper rows the purchase closed (KAN-189) are reopened. A storno that
    does not say so changes nothing here — the customer did hand the car
    over; only the dealer's booking was reversed. Nor does a stale one: a
    storno a later purchase booking overtook (Stock asked, as Sales does),
    whether that booking stands or was stornoed in turn.

    The contract's customer is Sales' fact, carried by the event since
    KAN-632 (an older event's comes from `seller_of_old_event`), and followed
    through a merge to its survivor, as the purchase consumer does. Both
    consumers judge by the bookings this context has learnt from Stock's
    events, so a storno consumed before its purchase (a failed delivery)
    finds nothing to reopen and the purchase consumed after it records
    nothing — the residual customer-vehicle-party.md states. The record is kept by
    `customer.stock_item_bookings_on_*`, not here. Flushes only;
    consume_once commits."""

    payload = message.payload or {}
    overtaken = False
    if message.tenant_id is not None:
        lock_stock_item(db, stock_item_id=message.aggregate_id)
        overtaken = storno_overtaken(db, message=message)
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
    # A storno may be consumed long after it happened (a failed delivery, a
    # dead letter replayed, a backlog): the car may have been bought again
    # since — and that booking may itself have been stornoed. If a later
    # purchase of the car is known here, the storno is stale and says nothing
    # about who holds the car now.
    if overtaken:
        logger.info("Trade-in storno %s: a later purchase overtook it; no holder reopened.", message.id)
        return

    contract_id = uuid.UUID(payload["originContractId"])
    seller = _trade_in_seller(db, message, contract_id=contract_id, seller_of_old_event=seller_of_old_event)
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
