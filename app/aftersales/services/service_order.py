"""The workshop order's rules (KAN-810; Aftersales build slice 1, table 3.1).

| What happens | In state | Result | Refused when |
| --- | --- | --- | --- |
| New order | – | Draft, with its number | The contact or the invoice recipient is «Blocked» (ruling 2). The car is not identified (ruling 36). |
| A job is accepted | Draft | Accepted | – |
| Jobs and positions are changed | Draft, accepted | (`positions.py`) | Ready for billing, cancelled: the order is frozen |
| «Rechnungsbereit» | Accepted | Ready for billing | (KAN-816) |
| Cancel, with a reason | Draft, accepted | Cancelled | A clock has run on the order (decision 51) |
| Contact, invoice recipient, driver changed | Draft, accepted | – | Frozen |

This module is the one place these rules live; a route only calls it. A
refusal says why in one sentence and carries a `details.reason` the SPA
says in the user's language.

**«Blocked»** is the contact's «Gesperrt» with its reason (`blocked`; Anto,
2026-11-26, KAN-810 Q1 option 1), read from `contact.public` at the moment
of the action and never copied (Contacts FR-21: «named on every refusal»).
The credit block is not «Blocked»: an order for a credit-blocked customer
exists, and the credit block only changes how it is paid (3.7, check 38). A do-not-contact contact
is not refused: do-not-contact never stops service (Contacts FR-12). A merged
contact is refused, naming the contact it was merged into.

The number comes from the Betrieb's `service_order` range inside this
transaction (ADR-109): a refused or rolled back order consumes no number.
The contacts are group-scoped (ADR-014): another group's is a 404. Every
change is an audit row on the order (its history, exit criterion 6), and
the creation and the cancellation are facts in the outbox, in the same
transaction.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Exists, Select, exists, or_, select, text
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import (
    JobAnswerState,
    ServiceOrder,
    ServiceOrderJob,
    ServiceOrderState,
)
from app.aftersales.services import work_guard
from app.contact.public import (
    ContactLifecycleStatus,
    ContactRef,
    contact_display_name,
    get_contact_or_404,
    resolve_live_contact_id,
)
from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.list_query import ListQuery, run_list_query
from app.core.outbox import OutboxEvent, publish
from app.core.relative_dates import today_in_session_zone
from app.platform.public import NumberRangeDocumentType, allocate_number, get_location_or_404
from app.vehicle.public import get_vehicle_mdm_or_404, get_vehicle_summaries, resolve_merged_vehicle_id

ENTITY_TYPE = "service_order"
PRODUCER = "aftersales"
CREATED_EVENT = "aftersales.service_order.created"
CANCELLED_EVENT = "aftersales.service_order.cancelled"

# Table 3.1: jobs, positions and the people of the order change only while it is open.
OPEN_STATES = frozenset({ServiceOrderState.DRAFT, ServiceOrderState.ACCEPTED})


# --- reading ------------------------------------------------------------------------------


def get_order_or_404(db: Session, *, tenant_id: uuid.UUID, order_id: uuid.UUID, lock: bool = False) -> ServiceOrder:
    """The Betrieb's order; another Betrieb's is a 404 (rule 7). `lock` takes the
    row for the write that follows, so two saves with one If-Match cannot both land."""

    stmt = select(ServiceOrder).where(ServiceOrder.id == order_id, ServiceOrder.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    order = db.scalar(stmt)
    if order is None:
        raise NotFoundError(f"Service order {order_id} was not found.")
    return order


def _waiting_for_answer(order_id_column: Any) -> Exists:
    """Worked out, never stored (section 3.9): a job of the order waits for the answer."""

    return exists().where(
        ServiceOrderJob.service_order_id == order_id_column,
        ServiceOrderJob.answer_state == JobAnswerState.WAITING,
    )


def waiting_for_answer_ids(db: Session, order_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not order_ids:
        return set()
    return set(
        db.scalars(
            select(ServiceOrderJob.service_order_id).where(
                ServiceOrderJob.service_order_id.in_(order_ids),
                ServiceOrderJob.answer_state == JobAnswerState.WAITING,
            )
        )
    )


# The chips of D1 this slice can work out today. «Arriving today», «pick-up today» and
# «in the workshop» are worked out from the planner and the stay (KAN-812, KAN-813).
CHIPS = ("waiting_for_answer", "ready_for_billing")


def list_orders(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    chip: str | None,
    q: str | None,
    query: ListQuery,
) -> tuple[list[ServiceOrder], str | None, int, bool]:
    """The order list (D1) on the list-query layer (KAN-848): the chip and
    the search narrow it, `query` filters and sorts over
    `order_grid.SERVICE_ORDER_COLUMNS`, scoped to the Betrieb."""

    stmt: Select = select(ServiceOrder)
    if chip == "waiting_for_answer":
        stmt = stmt.where(ServiceOrder.state.in_(OPEN_STATES), _waiting_for_answer(ServiceOrder.id))
    elif chip == "ready_for_billing":
        stmt = stmt.where(ServiceOrder.state == ServiceOrderState.READY_FOR_BILLING)
    if q:
        # `%` and `_` typed into the search are letters, not wildcards.
        stmt = stmt.where(
            or_(
                ServiceOrder.order_number.icontains(q, autoescape=True),
                ServiceOrder.contact_label.icontains(q, autoescape=True),
                ServiceOrder.invoice_recipient_label.icontains(q, autoescape=True),
                ServiceOrder.vehicle_label.icontains(q, autoescape=True),
            )
        )
    return run_list_query(
        db,
        stmt,
        model=ServiceOrder,
        scope=[ServiceOrder.tenant_id == tenant_id],
        query=query,
        count_threshold=get_settings().count_exact_threshold,
    )


# --- the people and the car -----------------------------------------------------------------


# --- a write racing a merge (KAN-837) ---------------------------------------------------


def _advisory_lock(db: Session, key: str) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(key.encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})


def lock_order_contacts(db: Session, contact_ids: Iterable[uuid.UUID | None]) -> None:
    """Serialises a write naming these contacts with `aftersales.contact_merged`
    moving them (until the transaction ends). Taken before the contact is read
    and before the order row; in id order, so two writes never wait on each other."""

    for contact_id in sorted({c for c in contact_ids if c is not None}):
        _advisory_lock(db, f"aftersales.order_contact:{contact_id}")


def lock_order_vehicle(db: Session, vehicle_id: uuid.UUID | None) -> None:
    """The same for the car and `aftersales.vehicle_merged`."""

    if vehicle_id is not None:
        _advisory_lock(db, f"aftersales.order_vehicle:{vehicle_id}")


def vehicle_label(summary) -> str:
    """The car as the order names it: make and model with the VIN, else the VIN."""

    name = " ".join(part for part in (summary.make, summary.model) if part)
    return (f"{name} · {summary.vin}" if name else summary.vin)[:200]


@dataclass(frozen=True)
class _Party:
    id: uuid.UUID
    label: str


def _party(db: Session, *, group_id: uuid.UUID, contact_id: uuid.UUID, role: str, refuse_blocked: bool) -> _Party:
    """A contact of the group for `role` (`contact`, `invoice_recipient`, `driver`).
    Another group's is a 404 (rule 7)."""

    lock_order_contacts(db, [contact_id])
    contact = get_contact_or_404(db, group_id, contact_id)
    _refuse_merged(db, group_id=group_id, contact=contact)
    if refuse_blocked and contact.blocked:
        raise ConflictError(
            f"Contact {contact.contact_number} is blocked ({contact.blocked_reason or 'no reason given'}) "
            "— no service order can be opened or held for them.",
            details={
                "reason": "service_order_contact_blocked",
                "role": role,
                "contactNumber": contact.contact_number,
                "blockedReason": contact.blocked_reason,
            },
        )
    return _Party(id=contact.id, label=contact_display_name(contact)[:200])


def _refuse_merged(db: Session, *, group_id: uuid.UUID, contact: ContactRef) -> None:
    if contact.lifecycle_status is ContactLifecycleStatus.MERGED:
        survivor_id = resolve_live_contact_id(db, group_id=group_id, contact_id=contact.id)
        raise ConflictError(
            f"Contact {contact.contact_number} has been merged into another — choose the contact it was merged into.",
            details={
                "reason": "service_order_contact_merged",
                "mergedIntoContactId": str(survivor_id) if survivor_id else None,
            },
        )


def _vehicle(db: Session, vehicle_id: uuid.UUID | None) -> _Party:
    """The car, identified (ruling 36): a record of the vehicle register, the
    live one when the id names a merged record."""

    if vehicle_id is None:
        raise UnprocessableEntityError(
            "The car is not identified — choose it among the vehicles or create it first.",
            details={"reason": "service_order_vehicle_not_identified"},
        )
    lock_order_vehicle(db, vehicle_id)
    live_id = resolve_merged_vehicle_id(db, vehicle_id)
    record = get_vehicle_mdm_or_404(db, live_id)
    summary = get_vehicle_summaries(db, [record.id]).get(record.id)
    return _Party(id=record.id, label=vehicle_label(summary) if summary else record.vin[:200])


def snapshot(order: ServiceOrder) -> dict:
    return {
        "orderNumber": order.order_number,
        "state": order.state.value,
        "locationId": str(order.location_id),
        "orderDate": order.order_date.isoformat(),
        "contactId": str(order.contact_id),
        "contactLabel": order.contact_label,
        "invoiceRecipientContactId": str(order.invoice_recipient_contact_id),
        "invoiceRecipientLabel": order.invoice_recipient_label,
        "driverContactId": str(order.driver_contact_id) if order.driver_contact_id else None,
        "driverLabel": order.driver_label,
        "vehicleId": str(order.vehicle_id),
        "vehicleLabel": order.vehicle_label,
    }


def _event_payload(order: ServiceOrder) -> dict:
    """One payload shape for every event of the order (ADR-202)."""

    return {
        "serviceOrderId": str(order.id),
        "orderNumber": order.order_number,
        "state": order.state.value,
        "locationId": str(order.location_id),
        "orderDate": order.order_date.isoformat(),
        "contactId": str(order.contact_id),
        "invoiceRecipientContactId": str(order.invoice_recipient_contact_id),
        "driverContactId": str(order.driver_contact_id) if order.driver_contact_id else None,
        "vehicleId": str(order.vehicle_id),
        "cancelledAt": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "cancelledBy": str(order.cancelled_by) if order.cancelled_by else None,
        "cancelReason": order.cancel_reason,
    }


def _publish(db: Session, order: ServiceOrder, event_type: str) -> None:
    publish(
        db,
        OutboxEvent(
            event_type=event_type,
            tenant_id=order.tenant_id,
            producer=PRODUCER,
            aggregate_type=ENTITY_TYPE,
            aggregate_id=order.id,
            payload=_event_payload(order),
        ),
    )


def record_order_change(
    db: Session,
    order: ServiceOrder,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
) -> None:
    """Every change of the order, its jobs and its positions: the order's version
    moves (the order is the aggregate) and the history gets its audit row."""

    order.version += 1
    order.updated_by = actor_id
    order.updated_at = utcnow()
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=order.id,
        tenant_id=order.tenant_id,
        action=action,
        actor_id=actor_id,
        before=before,
        after=after,
        reason=reason,
    )


def refuse_frozen(order: ServiceOrder) -> None:
    """Table 3.1: a ready-for-billing or cancelled order is frozen."""

    if order.state not in OPEN_STATES:
        raise ConflictError(
            f"Service order {order.order_number} is {order.state.value.replace('_', ' ')} and can no longer be changed.",
            details={"reason": "service_order_frozen", "state": order.state.value},
        )


# --- the rules of table 3.1 -------------------------------------------------------------------


def create_order(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    location_id: uuid.UUID,
    contact_id: uuid.UUID,
    invoice_recipient_contact_id: uuid.UUID | None,
    driver_contact_id: uuid.UUID | None,
    vehicle_id: uuid.UUID | None,
) -> ServiceOrder:
    """A new order: a draft, with its number from the range (check 1). Refused for a
    blocked contact or invoice recipient (check 2) and for a car not identified."""

    # KAN-837: every contact first, in id order (a merge consumer moves what this commits).
    lock_order_contacts(db, [contact_id, invoice_recipient_contact_id, driver_contact_id])
    location = get_location_or_404(db, dealership_id=tenant_id, location_id=location_id)
    contact = _party(db, group_id=group_id, contact_id=contact_id, role="contact", refuse_blocked=True)
    recipient = (
        contact
        if invoice_recipient_contact_id is None or invoice_recipient_contact_id == contact.id
        else _party(
            db, group_id=group_id, contact_id=invoice_recipient_contact_id, role="invoice_recipient",
            refuse_blocked=True,
        )
    )
    driver = (
        _party(db, group_id=group_id, contact_id=driver_contact_id, role="driver", refuse_blocked=False)
        if driver_contact_id is not None
        else None
    )
    car = _vehicle(db, vehicle_id)

    now = utcnow()
    order = ServiceOrder(
        tenant_id=tenant_id,
        order_number=allocate_number(
            db, dealership_id=tenant_id, document_type=NumberRangeDocumentType.SERVICE_ORDER, location_id=location.id
        ).number,
        state=ServiceOrderState.DRAFT,
        location_id=location.id,
        location_label=location.name[:200],
        location_label_refreshed_at=now,
        order_date=today_in_session_zone()[0],
        contact_id=contact.id,
        contact_label=contact.label,
        contact_label_refreshed_at=now,
        invoice_recipient_contact_id=recipient.id,
        invoice_recipient_label=recipient.label,
        invoice_recipient_label_refreshed_at=now,
        driver_contact_id=driver.id if driver else None,
        driver_label=driver.label if driver else None,
        driver_label_refreshed_at=now if driver else None,
        vehicle_id=car.id,
        vehicle_label=car.label,
        vehicle_label_refreshed_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(order)
    db.flush()
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=order.id,
        tenant_id=tenant_id,
        action="service_order_created",
        actor_id=actor_id,
        after=snapshot(order),
    )
    _publish(db, order, CREATED_EVENT)
    db.commit()
    db.refresh(order)
    return order


def change_parties(
    db: Session,
    *,
    order: ServiceOrder,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    changes: dict[str, uuid.UUID | None],
) -> ServiceOrder:
    """Contact, invoice recipient and driver (table 3.1, last row). Open orders only;
    a blocked contact or invoice recipient is refused as at creation. A price already
    entered keeps its price (ruling 178); a rate proposed from now on asks for the new
    invoice recipient's agreement (decision 66)."""

    refuse_frozen(order)
    before = snapshot(order)
    now = utcnow()
    new_contact_id = changes.get("contact_id")
    if new_contact_id is not None:
        party = _party(db, group_id=group_id, contact_id=new_contact_id, role="contact", refuse_blocked=True)
        order.contact_id, order.contact_label, order.contact_label_refreshed_at = party.id, party.label, now
    new_recipient_id = changes.get("invoice_recipient_contact_id")
    if new_recipient_id is not None:
        party = _party(
            db, group_id=group_id, contact_id=new_recipient_id, role="invoice_recipient",
            refuse_blocked=True,
        )
        order.invoice_recipient_contact_id = party.id
        order.invoice_recipient_label = party.label
        order.invoice_recipient_label_refreshed_at = now
    if "driver_contact_id" in changes:
        driver_id = changes["driver_contact_id"]
        if driver_id is None:
            order.driver_contact_id = order.driver_label = order.driver_label_refreshed_at = None
        else:
            party = _party(db, group_id=group_id, contact_id=driver_id, role="driver", refuse_blocked=False)
            order.driver_contact_id, order.driver_label, order.driver_label_refreshed_at = party.id, party.label, now
    after = snapshot(order)
    if after != before:
        record_order_change(db, order, action="service_order_parties_changed", actor_id=actor_id, before=before, after=after)
    db.commit()
    db.refresh(order)
    return order


def order_follows_accepted_job(order: ServiceOrder) -> None:
    """Table 3.1, «a job is accepted»: the first accepted job makes a draft order
    accepted (check 4). KAN-811's answer at the desk calls it."""

    if order.state is ServiceOrderState.DRAFT:
        order.state = ServiceOrderState.ACCEPTED


def cancel_order(db: Session, *, order: ServiceOrder, actor_id: uuid.UUID, reason: str) -> ServiceOrder:
    """Cancel, with a reason (check 10: who, when and why are kept). Refused on a
    frozen order, and once a clock has run on it — its jobs are declined instead
    (decision 51, check 9). Releasing its bookings comes with the planner (KAN-812)."""

    refuse_frozen(order)
    reason = reason.strip()
    if not reason:
        raise UnprocessableEntityError(
            "A cancellation needs its reason.", details={"reason": "service_order_cancel_reason_required"}
        )
    if work_guard.clock_has_run_on_order(db, tenant_id=order.tenant_id, order_id=order.id):
        raise ConflictError(
            f"Work has been clocked on service order {order.order_number}, so it cannot be cancelled — "
            "decline its jobs instead.",
            details={"reason": "service_order_clock_has_run"},
        )
    before = {"state": order.state.value}
    order.state = ServiceOrderState.CANCELLED
    order.cancelled_at = utcnow()
    order.cancelled_by = actor_id
    order.cancel_reason = reason
    record_order_change(
        db, order, action="service_order_cancelled", actor_id=actor_id, before=before,
        after={"state": order.state.value}, reason=reason,
    )
    _publish(db, order, CANCELLED_EVENT)
    db.commit()
    db.refresh(order)
    return order
