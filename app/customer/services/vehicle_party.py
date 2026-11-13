"""VehicleParty writes (ADR-064; split from customer.py, KAN-617): allocation
and the close rule (KAN-99, KAN-148), create/update/delete of a customer's
vehicle link, the (vehicle, role, group) advisory lock and the future-holder
rules (KAN-583, KAN-644, KAN-664).
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.core.uuid7 import uuid7
from app.customer.models.customer import (
    Customer,
    CustomerLifecycleStatus,
)
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.schemas.customer import (
    CustomerVehicleCreate,
    CustomerVehicleUpdate,
)
from app.customer.services.customer_common import _EVENT_PRODUCER, customer_display_name, get_customer_or_404
from app.customer.services.vehicle_party_labels import _write_vehicle_labels
from app.customer.services.vehicle_party_locks import (
    _lock_party,
    _lock_role_rows,
    _lock_vehicle_role_in_group,
    _refuse_merged_customer,
)
from app.customer.services.vehicle_party_reads import _holds_now_or_later, _is_open, _validate_effective_range
from app.vehicle.public import get_vehicle_mdm_or_404


def create_customer_vehicle(
    db: Session, *, customer: Customer, data: CustomerVehicleCreate, actor_id: uuid.UUID
) -> VehicleParty:
    """KAN-31: resolves against vehicle_mdm (WP-5's three-layer model),
    never the legacy `vehicle` table — that table's writes are frozen
    (ADR-021) and `LinkVehicleModal` has only ever POSTed a vehicle_mdm.id,
    so resolving against the legacy table 404'd on every real attempt.
    """

    vehicle = get_vehicle_mdm_or_404(db, data.vehicle_id)
    effective_from = data.effective_from or utcnow()
    _validate_effective_range(effective_from, data.effective_to)

    if _is_open(data.effective_to):
        # The ordinary path — every real caller (LinkVehicleModal never
        # sends effectiveTo on create). Delegates to the SAME function the
        # vehicle-side POST /vehicle-mdm/{id}/allocate calls, so both
        # directions share one ADR-064 implementation: a new holder CLOSES
        # whichever other open holder of this (vehicle, role) exists,
        # never a second silent open row. Before this fix, this endpoint
        # inserted a raw VehicleParty and never closed the incumbent —
        # exactly what ADR-064 exists to prevent.
        # KAN-148 (Anto, option A): an end date still to come is a holder
        # NOW, so it takes the same path — locked, closing the others —
        # and keeps its end date.
        return allocate_vehicle_party(
            db,
            vehicle_id=vehicle.id,
            customer_id=customer.id,
            role=data.role,
            group_id=customer.group_id,
            actor_id=actor_id,
            effective_from=effective_from,
            effective_to=data.effective_to,
        )

    # An effectiveTo already past on CREATE is a backdated, already-closed
    # row — history, not a holder: closing some OTHER open holder to make
    # room for a row that is itself already closed would misrepresent the
    # timeline. No real caller sends this today (kept for the documented API
    # contract this schema field is part of — see
    # test_create_rejects_effective_to_before_effective_from).
    # KAN-370: under the allocation's locks — the (vehicle, role, group)
    # advisory lock, then the customer row FOR SHARE — and refused for a
    # merged customer (409 `customer_merged`), so a history row never lands
    # on a merged record, and one written while a merge of this customer runs
    # either commits before the merge locks the customer (and is re-pointed
    # with the rest) or waits for it and is refused.
    _lock_vehicle_role_in_group(db, vehicle_id=vehicle.id, role=data.role, group_id=customer.group_id)
    _refuse_merged_customer(db, customer.id)
    party = VehicleParty(
        vehicle_id=vehicle.id,
        customer_id=customer.id,
        role=data.role,
        effective_from=effective_from,
        effective_to=data.effective_to,
    )
    _write_vehicle_labels(db, [party])
    db.add(party)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={
                "reason": "vehicle_party_exists",
                "vehicleId": str(vehicle.id),
                "role": data.role.value,
                "effectiveFrom": effective_from.isoformat(),
            },
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.group_id,
        action="vehicle_party_add",
        actor_id=actor_id,
        after={
            "vehicleId": str(vehicle.id),
            "role": party.role.value,
            "effectiveFrom": party.effective_from.isoformat(),
            "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        },
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.vehicle_party.linked",
            tenant_id=customer.group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="vehicle_party",
            aggregate_id=party.id,
            payload=_vehicle_party_payload(party),
        ),
    )
    db.commit()
    db.refresh(party)
    return party


def update_customer_vehicle(
    db: Session, *, party: VehicleParty, data: CustomerVehicleUpdate, actor_id: uuid.UUID, group_id: uuid.UUID
) -> VehicleParty:
    """KAN-148 (Anto, option A): a PATCH whose `effectiveTo` leaves the row
    holding its role — `null`, or an end still to come — may reopen a closed
    row (say, to undo a Disconnect clicked by mistake), and goes through the
    allocation's own close rule: under the same advisory lock on (vehicle,
    role, group), every OTHER open holder of the role in the group is closed
    in the same transaction, so the group never shows two current holders.
    A reopen publishes `customer.vehicle_party.linked`, as an allocation
    does. The advisory lock is taken before the row lock, in the order the
    allocation takes them, so the two never deadlock. The copy a customer
    merge kept (KAN-177, `merged_into_customer_id`) is never reopened: 409
    `merged_party_row`. KAN-352: a PATCH that ends a holding row (an
    `effectiveTo` now or past) publishes `customer.vehicle_party.unlinked`,
    as Disconnect does. KAN-662: a PATCH changing only `effectiveFrom` of a
    row that holds its role counts as a reopen — same lock, refusals and
    close rule; it publishes nothing new (the row held before and after)."""

    changes = data.model_dump(exclude_unset=True)
    reopens = "effective_to" in changes and _is_open(changes["effective_to"])
    if reopens or "effective_from" in changes:
        # A moved start moves the end of the holder it was planned to replace
        # (KAN-644, `_follow_planned_start`): the role's lock first, then its
        # rows in id order.
        _lock_vehicle_role_in_group(db, vehicle_id=party.vehicle_id, role=party.role, group_id=group_id)
        _lock_role_rows(db, vehicle_id=party.vehicle_id, role=party.role, group_id=group_id)
    party = _lock_party(db, party)
    old_start = party.effective_from
    if (
        "effective_from" in changes
        and not reopens
        and party.effective_to is not None
        and party.effective_to == party.effective_from
    ):
        # KAN-583: an entry cancelled before it began (an empty period) moves
        # only by being reopened (`effectiveTo`), which runs the overlap check
        # and the close rule; a start-only change would make it a second
        # current holder behind the close rule's back.
        raise BadRequestError(
            "This entry was cancelled before it began; reopen it with effectiveTo to change its start.",
            details={"field": "effectiveFrom"},
        )
    # An empty period (a cancelled planned row, KAN-583) never held the role,
    # so reopening it publishes `linked`.
    held_before = _is_open(party.effective_to) and party.effective_to != party.effective_from
    # KAN-662: a start-only PATCH of a row that holds its role (now or from
    # its start on) is a holder's period changing as much as a reopen is: a
    # start moved earlier can reach into another holder's period. It takes the
    # same refusal (KAN-583) and close rule (KAN-148, KAN-644) — decided on
    # the locked row. A row that holds nothing (history) closes nobody.
    holds_after = reopens or ("effective_to" not in changes and "effective_from" in changes and held_before)
    if holds_after and party.merged_into_customer_id is not None:
        # KAN-177 keeps the duplicate's copy closed at its start so it never
        # reads as a current party again; reopening it would also close the
        # survivor's holder of the same role.
        raise ConflictError(
            "This row is the copy a customer merge kept; it cannot hold its role again.",
            details={
                "reason": "merged_party_row",
                "mergedIntoCustomerId": str(party.merged_into_customer_id),
            },
        )
    if holds_after:
        # KAN-365: checked after KAN-177's row-level refusal, so the copy a
        # merge kept still answers `merged_party_row`.
        _refuse_merged_customer(db, party.customer_id)
    before = {
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
    }

    new_effective_from = changes.get("effective_from", party.effective_from)
    new_effective_to = changes.get("effective_to", party.effective_to)
    _validate_effective_range(new_effective_from, new_effective_to)

    # KAN-583: a future holder the reopened period overlaps refuses the PATCH
    # before anything is changed.
    to_close = (
        _current_holders_refusing_future_overlap(
            db,
            _open_holders_in_group(
                db, vehicle_id=party.vehicle_id, role=party.role, group_id=group_id, excluding=party.id
            ),
            starts=new_effective_from,
            ends=new_effective_to,
        )
        if holds_after
        else []
    )

    for field, value in changes.items():
        setattr(party, field, value)

    if "effective_from" in changes:
        # KAN-644: the holders this row was planned to replace end at its new
        # start first — with or without an `effectiveTo` in the same PATCH —
        # so the close below neither spares them at their old end (a gap) nor
        # closes one twice.
        _follow_planned_start(db, planned=party, old_start=old_start, actor_id=actor_id, group_id=group_id)
        to_close = [row for row in to_close if _is_open(row.effective_to)]
    _close_incumbents(
        db,
        to_close,
        new_effective_from=new_effective_from,
        actor_id=actor_id,
        group_id=group_id,
        new_party_id=party.id,
    )
    if "effective_to" in changes:
        # A row given its own end is no longer «closed at a planned start».
        party.closed_for_party_id = None

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={"reason": "vehicle_party_exists", "vehicleId": str(party.vehicle_id), "role": party.role.value},
        ) from exc

    # KAN-352: the holder changes in either direction, so the event does
    # too — a reopen is `linked`, an end now or in the past is `unlinked`, as
    # Disconnect publishes it. A PATCH that leaves the row holding (or not)
    # publishes nothing new.
    ends_now = held_before and "effective_to" in changes and not holds_after
    if (holds_after and not held_before) or ends_now:
        publish_event(
            db,
            OutboxEvent(
                event_type="customer.vehicle_party.linked" if holds_after else "customer.vehicle_party.unlinked",
                tenant_id=group_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="vehicle_party",
                aggregate_id=party.id,
                payload=_vehicle_party_payload(party),
            ),
        )
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=group_id,
        action="vehicle_party_update",
        actor_id=actor_id,
        before=before,
        after={
            "effectiveFrom": party.effective_from.isoformat(),
            "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        },
    )
    db.commit()
    db.refresh(party)
    return party


def delete_customer_vehicle(db: Session, *, party: VehicleParty, actor_id: uuid.UUID, group_id: uuid.UUID) -> None:
    """"Disconnect" (FR-V-05) — CLOSES the allocation by setting
    effective_to; the row is never deleted, so history survives (WP-5
    PR-9, ADR-064). This was a hard db.delete() before PR-9 — a real
    correctness gap against ADR-064's explicit "never overwritten, never
    deleted" rule, fixed here rather than left for whoever built PR-9's
    party-role work to rediscover. list_customer_vehicles' default
    (open-only) view already filters closed rows out, so callers see the
    same "it's gone" result as before; GET .../vehicles?includeClosed=true
    is the only way the row becomes visible again.

    Idempotent against a second call on an already-closed row — the
    closing timestamp isn't allowed to drift forward on a repeat request.
    KAN-148: the row is locked and re-read first, so a Disconnect racing
    another Disconnect, or an allocation closing this row, waits for it and
    then finds the row closed — one close, one `unlinked` event.
    """

    # The (vehicle, role, group) lock first, as every writer of the role
    # takes it, then the row; planned or not is decided on the locked row, so
    # a start moved meanwhile is seen (never an end before the start).
    _lock_vehicle_role_in_group(db, vehicle_id=party.vehicle_id, role=party.role, group_id=group_id)
    _lock_role_rows(db, vehicle_id=party.vehicle_id, role=party.role, group_id=group_id)
    party = _lock_party(db, party)
    if not _is_open(party.effective_to) or party.effective_to == party.effective_from:
        return

    # KAN-664: one clock for the whole decision, so a start passing between
    # «is it planned» and «which rows does it link» cannot cancel the planned
    # row and give nobody the role back.
    now = utcnow()
    if party.effective_from > now:
        # KAN-583 (Anto, 2026-11-12, ruling 1A): «Eintrag beenden» on a holder
        # whose start is still to come cancels it. The row is kept in the
        # history as «planned, never started»: closed at its own start, an
        # empty period, as a merge's kept copy is (KAN-177). Never deleted
        # (ADR-064), never an end before its start.
        _close_vehicle_party(
            db,
            party=party,
            actor_id=actor_id,
            group_id=group_id,
            effective_to=party.effective_from,
            reason="planned_never_started",
        )
        _restore_holders_ended_by(db, planned=party, actor_id=actor_id, group_id=group_id, now=now)
    else:
        _close_vehicle_party(db, party=party, actor_id=actor_id, group_id=group_id)
    db.commit()


def _linked_holders(
    db: Session, *, planned: VehicleParty, starts: dt.datetime, now: dt.datetime | None = None
) -> list[VehicleParty]:
    """The rows `_close_incumbents` closed at `planned`'s start, locked — while
    the link is live: the row still ends exactly at that start (`starts`, the
    planned row's start before this write) and the start is still to come. A
    handover that has happened, or an end changed since, leaves the link
    dead: such a row is history and never given the role back or moved."""

    if starts <= (now or utcnow()):
        return []
    return list(
        db.scalars(
            select(VehicleParty)
            .where(VehicleParty.closed_for_party_id == planned.id, VehicleParty.effective_to == starts)
            .order_by(VehicleParty.id)
            .with_for_update()
        ).all()
    )


def _restore_holders_ended_by(
    db: Session, *, planned: VehicleParty, actor_id: uuid.UUID, group_id: uuid.UUID, now: dt.datetime
) -> None:
    """KAN-583 + KAN-644: recording `planned` closed the current holder of the
    role at `planned`'s start and linked it (`closed_for_party_id`).
    Cancelling the planned handover takes that end back: exactly the linked
    rows are open again, audited `vehicle_party_update` with reason
    `planned_handover_cancelled`. They never stopped holding the role and no
    `unlinked` was published for them, so none is now. An end a user typed
    carries no link and is never touched. The caller holds the (vehicle,
    role, group) lock and its rows.

    KAN-664: «open again» is up to the next holder still to come — when
    another holder of the role is already recorded from a later date, the
    restored row ends at that start and is linked to it, exactly what that
    holder's own allocation would have written: one current holder at a
    time (KAN-644), never two from that start on."""

    for row in _linked_holders(db, planned=planned, starts=planned.effective_from, now=now):
        before = {
            "effectiveFrom": row.effective_from.isoformat(),
            "effectiveTo": row.effective_to.isoformat() if row.effective_to else None,
        }
        upcoming = db.scalar(
            select(VehicleParty)
            .join(Customer, Customer.id == VehicleParty.customer_id)
            .where(
                VehicleParty.vehicle_id == row.vehicle_id,
                VehicleParty.role == row.role,
                Customer.group_id == group_id,
                VehicleParty.id.not_in([row.id, planned.id]),
                VehicleParty.effective_from > now,
                VehicleParty.effective_from > row.effective_from,
                _holds_now_or_later(now),
            )
            .order_by(VehicleParty.effective_from, VehicleParty.id)
            .limit(1)
        )
        row.effective_to = upcoming.effective_from if upcoming is not None else None
        row.closed_for_party_id = upcoming.id if upcoming is not None else None
        record_audit_event(
            db,
            entity_type="customer",
            entity_id=row.customer_id,
            tenant_id=group_id,
            action="vehicle_party_update",
            actor_id=actor_id,
            before=before,
            after={
                "effectiveFrom": row.effective_from.isoformat(),
                "effectiveTo": row.effective_to.isoformat() if row.effective_to else None,
            },
            reason="planned_handover_cancelled",
        )


def _follow_planned_start(
    db: Session, *, planned: VehicleParty, old_start: dt.datetime, actor_id: uuid.UUID, group_id: uuid.UUID
) -> None:
    """KAN-644: a PATCH moving a planned holder's start moves the end of the
    holders it was planned to replace, so the history keeps no gap and never
    two current holders. A start moved to now or earlier is the handover
    itself: they end there (never before their own start), lose the link, and
    `unlinked` is published as for any end now or past (KAN-352). The caller
    holds the (vehicle, role, group) lock."""

    now = utcnow()
    for row in _linked_holders(db, planned=planned, starts=old_start, now=now):
        before = {
            "effectiveFrom": row.effective_from.isoformat(),
            "effectiveTo": row.effective_to.isoformat() if row.effective_to else None,
        }
        ends = max(planned.effective_from, row.effective_from)
        if ends > now:
            row.effective_to = ends
            record_audit_event(
                db,
                entity_type="customer",
                entity_id=row.customer_id,
                tenant_id=group_id,
                action="vehicle_party_update",
                actor_id=actor_id,
                before=before,
                after={"effectiveFrom": row.effective_from.isoformat(), "effectiveTo": ends.isoformat()},
                reason="planned_handover_moved",
            )
        else:
            _close_vehicle_party(db, party=row, actor_id=actor_id, group_id=group_id, effective_to=ends)


def _close_vehicle_party(
    db: Session,
    *,
    party: VehicleParty,
    actor_id: uuid.UUID | None,
    group_id: uuid.UUID,
    effective_to: dt.datetime | None = None,
    merged_into_customer_id: uuid.UUID | None = None,
    reason: str | None = None,
    closed_for_party_id: uuid.UUID | None = None,
) -> None:
    """Sets effective_to and writes the audit row and the unlinked outbox
    event — WITHOUT committing, so allocate_vehicle_party can close the
    incumbent and insert the new holder in one transaction (KAN-99). A
    commit between the two would leave the vehicle with no holder at all
    if the insert then failed. `group_id` must be the group of the
    customer on `party`: it stamps the audit row and the event.

    `effective_to` defaults to now. A customer merge (KAN-177) passes the
    row's own start and the survivor it merged into, which the audit row
    and the event carry as `mergedIntoCustomerId`.
    """

    before = {
        "vehicleId": str(party.vehicle_id),
        "role": party.role.value,
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
    }
    party.effective_to = effective_to or utcnow()
    # KAN-644: a row closed at a planned holder's start names it; any other
    # close ends the row for its own reason and drops a link it had.
    party.closed_for_party_id = closed_for_party_id
    after: dict[str, Any] = {"effectiveTo": party.effective_to.isoformat()}
    if merged_into_customer_id is not None:
        party.merged_into_customer_id = merged_into_customer_id
        after["mergedIntoCustomerId"] = str(merged_into_customer_id)

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=group_id,
        action="vehicle_party_remove",
        actor_id=actor_id,
        before=before,
        after=after,
        reason=reason,
    )
    # KAN-352/KAN-644: `unlinked` says the row stopped holding its role. A
    # close at a date still to come (a current holder until a planned
    # handover) publishes nothing now, as a PATCH with a future end does not.
    if party.effective_to > utcnow() and party.effective_to != party.effective_from:
        return
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.vehicle_party.unlinked",
            tenant_id=group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="vehicle_party",
            aggregate_id=party.id,
            payload=_vehicle_party_payload(party),
        ),
    )


def allocate_vehicle_party(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    customer_id: uuid.UUID,
    role: VehiclePartyRole,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    effective_from: dt.datetime | None = None,
    effective_to: dt.datetime | None = None,
) -> VehicleParty:
    """The actual ADR-064 allocation: setting a new holder for a role
    CLOSES whichever OTHER open holder of that same (vehicle, role)
    currently exists — never an update of the existing row, never a
    silent overwrite. If the SAME customer already holds this role
    (re-confirming, not a handover), this is a no-op returning the
    existing open row rather than closing-then-reopening an identical
    allocation. One dialog, reachable from either the vehicle or the
    customer (FR-V-05) — this is the function both call.

    KAN-99 — holders are per dealer group (Anto's ruling, 2026-10-04;
    ADR-014): vehicle_mdm is a global fact (ADR-022) and VehicleParty has
    no group column, so two groups can each hold the same role on one
    VIN. The customer is resolved in the caller's group first (404, never
    a cross-group link — a caller may pass a request-body id; since KAN-189
    the trade-in no longer allocates), and
    the incumbents are looked up through Customer.group_id, the same
    intra-context join as list_vehicle_parties. So an allocation closes
    EVERY open holder of (vehicle, role) in the caller's group — a
    backdated create or a reopened row can leave more than one — and
    never touches another group's timeline. The close and the insert
    commit together: a failure leaves nothing committed, and the
    request's session rolls it back.

    Concurrency: a row lock cannot serialise this — a concurrent request
    never sees the row the other one is inserting, with or without an
    incumbent — so on Postgres a transaction-scoped advisory lock on
    (vehicle, role, group) is taken before the incumbent lookup. A second
    allocation waits for the first to commit, then sees its row and
    closes it. The incumbents are also locked FOR UPDATE, so an allocation
    never re-closes a row a concurrent Disconnect has just closed, and a
    Disconnect locks its row too (KAN-148), so it never re-closes one an
    allocation has just closed.

    KAN-148 (Anto, option A): `effective_to` is an end still to come (a
    create with a future end date) — the row holds the role now, so the
    incumbents are closed exactly as for an open-ended holder. The
    same-customer no-op applies only to an open-ended request: an end date
    the incumbent lacks is a new statement, so it is a new row.
    """

    get_customer_or_404(db, group_id, customer_id)
    _lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
    _refuse_merged_customer(db, customer_id)
    _lock_role_rows(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
    effective_from = effective_from or utcnow()
    incumbents = _current_holders_refusing_future_overlap(
        db,
        _open_holders_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id),
        starts=effective_from,
        ends=effective_to,
    )
    if effective_to is None and len(incumbents) == 1 and incumbents[0].customer_id == customer_id:
        incumbent = incumbents[0]
        # KAN-84: a re-confirmed row written before KAN-84 has no label yet;
        # the response is built from it, so label it now.
        if incumbent.vehicle_label_refreshed_at is None:
            _write_vehicle_labels(db, [incumbent])
            db.commit()
        return incumbent

    party = _open_vehicle_party(
        db,
        vehicle_id=vehicle_id,
        customer_id=customer_id,
        role=role,
        group_id=group_id,
        actor_id=actor_id,
        effective_from=effective_from,
        effective_to=effective_to,
        incumbents=incumbents,
    )
    db.commit()
    db.refresh(party)
    return party


def resolve_live_customer_id(db: Session, *, group_id: uuid.UUID, customer_id: uuid.UUID) -> uuid.UUID | None:
    """The customer a record now lives on: `customer_id` itself, or the
    survivor its merge chain ends at (KAN-383 keeps "merged into" naming a
    live customer). None when the chain leaves `group_id` or does not end."""

    current = customer_id
    for _ in range(32):
        row = db.execute(
            select(Customer.lifecycle_status, Customer.duplicate_of_customer_id, Customer.group_id).where(
                Customer.id == current
            )
        ).one_or_none()
        if row is None or row.group_id != group_id:
            return None
        if row.lifecycle_status != CustomerLifecycleStatus.MERGED:
            return current
        if row.duplicate_of_customer_id is None:
            return None
        current = row.duplicate_of_customer_id
    return None


def _open_holders_in_group(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    role: VehiclePartyRole,
    group_id: uuid.UUID,
    excluding: uuid.UUID | None = None,
) -> list[VehicleParty]:
    """Every row holding (vehicle, role) now in `group_id`, oldest first,
    locked FOR UPDATE — found through Customer.group_id (KAN-99), never
    another group's. The caller holds the (vehicle, role, group) advisory
    lock, so no holder can be inserted behind its back. `excluding` leaves
    out the row a reopen is about to make the holder (KAN-148)."""

    stmt = (
        select(VehicleParty)
        .join(Customer, Customer.id == VehicleParty.customer_id)
        .where(
            VehicleParty.vehicle_id == vehicle_id,
            VehicleParty.role == role,
            Customer.group_id == group_id,
            _holds_now_or_later(),
        )
        .order_by(VehicleParty.effective_from)
        .with_for_update(of=VehicleParty)
    )
    if excluding is not None:
        stmt = stmt.where(VehicleParty.id != excluding)
    return list(db.scalars(stmt).all())


def _current_holders_refusing_future_overlap(
    db: Session, holders: list[VehicleParty], *, starts: dt.datetime, ends: dt.datetime | None
) -> list[VehicleParty]:
    """KAN-583 (Anto, 2026-11-10, option A): of `_open_holders_in_group`'s
    rows, those holding the role now — the ones an allocation or a reopen
    closes (KAN-148). A row whose start is still to come does not hold it yet:
    closing it at now would end it before it began (`effective_to <
    effective_from`, a period that never existed). When the new period
    [`starts`, `ends`) overlaps such a future holder, the write is refused,
    409 `vehicle_party_future_holder` naming that holder and its start: the
    user ends or moves one of the two entries — nothing changes behind their
    back. A future holder the new period ends before is left as it is.

    A row with an empty period (closed at its own start — the copy a merge
    kept, KAN-177) holds nothing, now or later, and is neither closed nor in
    the way. The caller holds the (vehicle, role, group) advisory lock, so no
    holder appears behind this check."""

    now = utcnow()
    current: list[VehicleParty] = []
    for holder in holders:
        if holder.effective_to is not None and holder.effective_to <= holder.effective_from:
            continue
        if holder.effective_from <= now:
            current.append(holder)
            continue
        overlaps = (ends is None or holder.effective_from < ends) and (
            holder.effective_to is None or holder.effective_to > starts
        )
        if overlaps:
            future = db.get(Customer, holder.customer_id)
            raise ConflictError(
                "Another customer is recorded in this role for this vehicle from a later date; "
                "end or move that entry first.",
                details={
                    "reason": "vehicle_party_future_holder",
                    "vehicleId": str(holder.vehicle_id),
                    "role": holder.role.value,
                    "vehiclePartyId": str(holder.id),
                    "customerId": str(holder.customer_id),
                    "customerName": customer_display_name(future) if future is not None else None,
                    "effectiveFrom": holder.effective_from.isoformat(),
                    "effectiveTo": holder.effective_to.isoformat() if holder.effective_to else None,
                },
            )
    return current


def _close_incumbents(
    db: Session,
    incumbents: list[VehicleParty],
    *,
    new_effective_from: dt.datetime,
    actor_id: uuid.UUID,
    group_id: uuid.UUID,
    new_party_id: uuid.UUID,
) -> None:
    """Closes the current holders a new (or reopened) holder replaces —
    KAN-148's close rule, at the moment KAN-644 rules.

    KAN-644 (Anto, 2026-11-11, option A; 2026-11-12, A): a holder recorded
    from a date still to come does not end the current one today. The current
    holder keeps the role until the new one starts: its row is closed at the
    new holder's `effective_from`, so the history has no gap, and until then
    it is the one current holder (the new one is upcoming: its start is still
    to come). A current holder ending on or before that start does not overlap
    and is left as it is; closing it «at the new start» would lengthen it. A
    new holder starting now or earlier closes them now, as before.

    A row closed at a future start names the new holder
    (`closed_for_party_id`): cancelling that planned handover gives it the
    role back, and moving the planned start moves its end
    (`_follow_planned_start`). It is still holding, so no `unlinked` is
    published now (KAN-352)."""

    close_at = new_effective_from if new_effective_from > utcnow() else None
    for incumbent in incumbents:
        if close_at is not None and incumbent.effective_to is not None and incumbent.effective_to <= close_at:
            continue
        _close_vehicle_party(
            db,
            party=incumbent,
            actor_id=actor_id,
            group_id=group_id,
            effective_to=close_at,
            closed_for_party_id=new_party_id if close_at is not None else None,
        )


def _open_vehicle_party(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    customer_id: uuid.UUID,
    role: VehiclePartyRole,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    effective_from: dt.datetime,
    effective_to: dt.datetime | None,
    incumbents: list[VehicleParty],
) -> VehicleParty:
    party = VehicleParty(
        id=uuid7(),
        vehicle_id=vehicle_id,
        customer_id=customer_id,
        role=role,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    _close_incumbents(
        db,
        incumbents,
        new_effective_from=effective_from,
        actor_id=actor_id,
        group_id=group_id,
        new_party_id=party.id,
    )
    _write_vehicle_labels(db, [party])
    db.add(party)
    try:
        db.flush()
    except IntegrityError as exc:
        # uq_vehicle_party_scope: this customer already has a row for this
        # role starting at exactly this effectiveFrom (a client-sent date).
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={
                "reason": "vehicle_party_exists",
                "vehicleId": str(vehicle_id),
                "role": role.value,
                "effectiveFrom": effective_from.isoformat(),
            },
        ) from exc

    after: dict[str, Any] = {
        "vehicleId": str(vehicle_id),
        "role": role.value,
        "effectiveFrom": effective_from.isoformat(),
    }
    if effective_to is not None:
        after["effectiveTo"] = effective_to.isoformat()
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer_id,
        tenant_id=group_id,
        action="vehicle_party_add",
        actor_id=actor_id,
        after=after,
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.vehicle_party.linked",
            tenant_id=group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="vehicle_party",
            aggregate_id=party.id,
            payload=_vehicle_party_payload(party),
        ),
    )
    return party


def _vehicle_party_payload(party: VehicleParty) -> dict[str, Any]:
    return {
        "vehiclePartyId": str(party.id),
        "vehicleId": str(party.vehicle_id),
        "customerId": str(party.customer_id),
        "role": party.role.value,
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        # KAN-177: set only on a copy a customer merge kept closed.
        "mergedIntoCustomerId": str(party.merged_into_customer_id) if party.merged_into_customer_id else None,
    }
