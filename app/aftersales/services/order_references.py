"""A workshop order's references follow their owners (KAN-837, follow-up of
KAN-810; slice section 6).

The order names its customer, invoice recipient and driver (contact), its car
(vehicle) and its location (platform) by id with a label — rule 2's
three-column pattern. Aftersales reads them only through each owner's
`public` and writes only its own rows, in its own transaction (ADR-047).

- **A contact merge** (`repoint_orders_on_contact_merge`, the consumer
  `aftersales.contact_merged`): every order naming the duplicate as
  customer, invoice recipient or driver moves to the end of the survivor's
  merge chain, whatever its state — an order ready for billing still names its
  invoice recipient, and a cancelled one is history that should resolve
  without a hop. The rate proposed from then on asks for the survivor's
  agreement (decision 66); a price already on the order keeps its price
  (ruling 178): nothing here touches a position.
- **A vehicle merge** (`repoint_orders_on_vehicle_merge`, the consumer
  `aftersales.vehicle_merged`): every order naming the merged record moves to
  the record the car lives on now.
- **Which labels follow** — as Sales' open work does (Anto, 2026-11-10, ruling
  1A, applied to the order): an open order (draft, accepted) takes the new
  name, on a merge and nightly; a frozen one (ready for billing, cancelled)
  keeps the name it was frozen with and is only restamped («checked, kept on
  purpose»), so its stamp never makes the label-age alarm fire.
- **Nightly** (`refresh_order_labels`, the daily job `aftersales.order_labels`,
  `.claude/rules/events-worker.md`): no `version` bump, a current label only
  restamped, an id naming nothing or a merged record left alone (the merge
  consumers move it), batches `FOR NO KEY UPDATE SKIP LOCKED`.
- **A write racing a merge** is serialised by a transaction advisory lock on
  the contact's or the car's id (`service_order.lock_order_contacts`, `lock_order_vehicle`),
  which creating an order and changing its people take before they read the
  contact or the car, and the consumers before they read the orders: the
  write commits first and its order is moved, or it waits and reads the
  contact or the car merged.

Every order moved by a merge gets an audit row and a new version (an open
screen then saves as stale), with the system actor. Flushes only; the
consumer's harness commits with the processed-event row.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import ServiceOrder
from app.aftersales.services.service_order import (
    OPEN_STATES,
    lock_order_contacts,
    lock_order_vehicle,
    record_order_change,
    snapshot,
    vehicle_label,
)
from app.contact.public import (
    contact_display_name,
    get_contact_or_404,
    get_contact_sales_labels,
    resolve_live_contact_id,
)
from app.core.base import utcnow
from app.core.errors import NotFoundError
from app.core.outbox_model import OutboxMessage
from app.platform.public import get_location_labels
from app.vehicle.public import find_merged_vehicle_ids, get_vehicle_summaries, resolve_merged_vehicle_id

_BATCH = 500

# (id column, label column, stamp column) of each contact the order names.
_CONTACT_ROLES = (
    ("contact_id", "contact_label", "contact_label_refreshed_at"),
    ("invoice_recipient_contact_id", "invoice_recipient_label", "invoice_recipient_label_refreshed_at"),
    ("driver_contact_id", "driver_label", "driver_label_refreshed_at"),
)


def _is_open(order: ServiceOrder) -> bool:
    return order.state in OPEN_STATES


# --- merges -------------------------------------------------------------------------------


def repoint_orders_on_contact_merge(
    db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID, target_label: str | None
) -> list[uuid.UUID]:
    """Move every order naming `duplicate_id` in any of its three roles to
    `target_id`. An open order takes `target_label` (None: a survivor the
    group cannot read — the id moves, the label waits for the nightly job).
    Returns the ids of the orders moved."""

    if duplicate_id == target_id:
        return []
    orders = list(
        db.scalars(
            select(ServiceOrder)
            .where(
                or_(
                    ServiceOrder.contact_id == duplicate_id,
                    ServiceOrder.invoice_recipient_contact_id == duplicate_id,
                    ServiceOrder.driver_contact_id == duplicate_id,
                )
            )
            .order_by(ServiceOrder.id)
            .with_for_update()
        )
    )
    now = utcnow()
    for order in orders:
        before = snapshot(order)
        for id_column, label_column, stamp_column in _CONTACT_ROLES:
            if getattr(order, id_column) != duplicate_id:
                continue
            setattr(order, id_column, target_id)
            if target_label is not None and _is_open(order):
                setattr(order, label_column, target_label[:200])
                setattr(order, stamp_column, now)
        record_order_change(
            db, order, action="service_order_contact_merged", actor_id=None, before=before, after=snapshot(order),
            reason=f"contact {duplicate_id} merged into {target_id}",
        )
    db.flush()
    return [order.id for order in orders]


def handle_contact_merged(db: Session, message: OutboxMessage) -> list[uuid.UUID]:
    """`contact.merged` → `aftersales.contact_merged`. Delivery is unordered:
    the survivor is re-read from its owner (the end of its merge chain), as
    Sales' and Stock's consumers do; a chain that leaves the group or does not
    end falls back to the payload's survivor. Idempotent: a redelivery finds
    nothing left on the duplicate."""

    if message.tenant_id is None:
        raise ValueError(f"contact.merged message {message.id} has no tenant_id.")
    payload = message.payload or {}
    duplicate_id = uuid.UUID(payload["contactId"])
    survivor_id = uuid.UUID(payload["duplicateOfContactId"])
    lock_order_contacts(db, [duplicate_id])
    target_id = resolve_live_contact_id(db, group_id=message.tenant_id, contact_id=survivor_id) or survivor_id
    try:
        label: str | None = contact_display_name(get_contact_or_404(db, message.tenant_id, target_id))
    except NotFoundError:
        label = None
    return repoint_orders_on_contact_merge(db, duplicate_id=duplicate_id, target_id=target_id, target_label=label)


def repoint_orders_on_vehicle_merge(
    db: Session, *, duplicate_vehicle_id: uuid.UUID, survivor_vehicle_id: uuid.UUID
) -> list[uuid.UUID]:
    """Move every order naming the merged record onto the record the car lives
    on now (a merge delivered after a later one lands on the live record). An
    open order takes that record's label. Returns the ids moved."""

    live_id = resolve_merged_vehicle_id(db, survivor_vehicle_id)
    if live_id == duplicate_vehicle_id:
        return []
    orders = list(
        db.scalars(
            select(ServiceOrder)
            .where(ServiceOrder.vehicle_id == duplicate_vehicle_id)
            .order_by(ServiceOrder.id)
            .with_for_update()
        )
    )
    if not orders:
        return []
    summary = get_vehicle_summaries(db, [live_id]).get(live_id)
    now = utcnow()
    for order in orders:
        before = snapshot(order)
        order.vehicle_id = live_id
        if summary is not None and _is_open(order):
            order.vehicle_label = vehicle_label(summary)
            order.vehicle_label_refreshed_at = now
        record_order_change(
            db, order, action="service_order_vehicle_merged", actor_id=None, before=before, after=snapshot(order),
            reason=f"vehicle {duplicate_vehicle_id} merged into {live_id}",
        )
    db.flush()
    return [order.id for order in orders]


def handle_vehicle_merged(db: Session, message: OutboxMessage) -> list[uuid.UUID]:
    """`vehicle.vehicle_mdm.merged` → `aftersales.vehicle_merged`."""

    survivor = (message.payload or {}).get("mergedIntoVehicleId")
    if not survivor:
        raise ValueError(f"vehicle.vehicle_mdm.merged message {message.id} names no mergedIntoVehicleId.")
    lock_order_vehicle(db, message.aggregate_id)
    return repoint_orders_on_vehicle_merge(
        db, duplicate_vehicle_id=message.aggregate_id, survivor_vehicle_id=uuid.UUID(survivor)
    )


# --- the nightly label refresh ------------------------------------------------------------


@dataclass
class RefreshReport:
    # (order id, column, old label, new label)
    changed: list[tuple[uuid.UUID, str, str | None, str]] = field(default_factory=list)
    restamped: int = 0
    # (order id, column): an id naming nothing, a merged contact or car, another Betrieb's location.
    unresolved: list[tuple[uuid.UUID, str]] = field(default_factory=list)


def _apply(
    order: ServiceOrder,
    label_column: str,
    stamp_column: str,
    current: str | None,
    *,
    now: dt.datetime,
    restamp: dict[str, list[uuid.UUID]],
    report: RefreshReport,
) -> None:
    """One label of one order: unresolved, restamped (current, or a frozen order
    keeping its name), or changed."""

    if current is None:
        report.unresolved.append((order.id, label_column))
        return
    if current == getattr(order, label_column) or not _is_open(order):
        restamp.setdefault(stamp_column, []).append(order.id)
        return
    report.changed.append((order.id, label_column, getattr(order, label_column), current))
    setattr(order, label_column, current)
    setattr(order, stamp_column, now)


def refresh_order_labels(db: Session, *, commit: Callable[[], None] | None = None) -> RefreshReport:
    """Re-read every order's labels — its three contacts, its car and its
    location — in id order, one `commit()` per batch read FOR NO KEY UPDATE
    SKIP LOCKED: an order another writer holds is left for the next night."""

    finish = commit or db.commit
    report = RefreshReport()
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(ServiceOrder).order_by(ServiceOrder.id).limit(_BATCH).with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(ServiceOrder.id > last_id)
        batch = list(db.scalars(stmt))
        if not batch:
            return report
        last_id = batch[-1].id
        contacts = get_contact_sales_labels(
            db, {getattr(o, column) for o in batch for column, _, _ in _CONTACT_ROLES if getattr(o, column)}
        )
        vehicle_ids = {o.vehicle_id for o in batch}
        merged_vehicles = find_merged_vehicle_ids(db, vehicle_ids)
        vehicles = get_vehicle_summaries(db, vehicle_ids - merged_vehicles)
        locations = get_location_labels(db, {o.location_id for o in batch})
        now = utcnow()
        restamp: dict[str, list[uuid.UUID]] = {}

        for order in batch:
            for id_column, label_column, stamp_column in _CONTACT_ROLES:
                contact_id = getattr(order, id_column)
                if contact_id is None:
                    continue
                found = contacts.get(contact_id)
                current = None if found is None or found.merged else found.label[:200]
                _apply(order, label_column, stamp_column, current, now=now, restamp=restamp, report=report)
            summary = vehicles.get(order.vehicle_id)
            _apply(
                order, "vehicle_label", "vehicle_label_refreshed_at", vehicle_label(summary) if summary else None,
                now=now, restamp=restamp, report=report,
            )
            location = locations.get(order.location_id)
            _apply(
                order, "location_label", "location_label_refreshed_at",
                location.label[:200] if location and location.dealership_id == order.tenant_id else None,
                now=now, restamp=restamp, report=report,
            )
        # A copy, refreshed on purpose: no version bump (an open screen would refuse its next save).
        db.flush()
        for stamp_column, order_ids in restamp.items():
            db.execute(
                update(ServiceOrder)
                .where(ServiceOrder.id.in_(order_ids))
                .values({stamp_column: now, "updated_at": ServiceOrder.updated_at})
                .execution_options(synchronize_session=False)
            )
            report.restamped += len(order_ids)
        finish()


_STAMP_COLUMNS = (
    ServiceOrder.contact_label_refreshed_at,
    ServiceOrder.invoice_recipient_label_refreshed_at,
    ServiceOrder.driver_label_refreshed_at,
    ServiceOrder.vehicle_label_refreshed_at,
    ServiceOrder.location_label_refreshed_at,
)


def oldest_order_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest label on a workshop order (any of its five) — what
    the worker heartbeat records as `dms.label.age_seconds{label="aftersales.order"}`
    (CLAUDE.md rule 10). None when there is no order."""

    stamps = [db.scalar(select(func.min(column)).where(column.is_not(None))) for column in _STAMP_COLUMNS]
    present = [stamp for stamp in stamps if stamp is not None]
    if not present:
        return None
    stamp = min(present)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()

