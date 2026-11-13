"""A bought trade-in's former owner (KAN-189) and its reopen after a returned
storno (KAN-572); split from customer.py, KAN-617.
"""

import datetime as dt
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.customer.models.customer import (
    Customer,
)
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.customer_common import _EVENT_PRODUCER
from app.customer.services.vehicle_party import _close_vehicle_party, _open_holders_in_group, _vehicle_party_payload
from app.customer.services.vehicle_party_labels import _write_vehicle_labels
from app.customer.services.vehicle_party_locks import (
    _lock_rows_of_roles,
    _lock_vehicle_role_in_group,
    _refuse_merged_customer,
)


def record_former_holder_on_purchase(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    customer_id: uuid.UUID,
    group_id: uuid.UUID,
    held_from: dt.datetime,
    ended_at: dt.datetime,
    actor_id: uuid.UUID | None,
) -> list[VehicleParty]:
    """KAN-189 (Anto, 2026-11-02, option A): the Betrieb has bought the car
    `customer_id` traded in, so that customer is recorded as its former owner
    and keeper (ADR-064), ending at the purchase (`ended_at`). This is what
    puts a traded-in car on the customer's Vehicles tab now that the trade-in
    itself creates no vehicle-mdm record and allocates no party.

    Per role: a row of this customer that holds the role at `ended_at` (an
    earlier link, open or ending later) is closed at `ended_at`; one already
    ending exactly then is left alone. Then the customer's own rows and the
    history join over [`held_from`, `ended_at`) without gap or overlap
    (KAN-566, Anto's option B): an own row starting at `held_from` and ended
    before is ended at the purchase, or at the start of the customer's next
    own row inside the period (case 1); every stretch no own row covers gets
    a closed history row (case 2) — or, where an empty own row («planned,
    never started», KAN-664) already starts at the stretch, that row is
    extended over it (the unique key allows no second row with that start).
    A merge's kept copy is never moved. Written rows are history, so they
    close nobody else (KAN-148/KAN-370: a row ended in the past is not a
    holder). Taken under
    the allocation's locks, and refused for a merged customer the same way
    (409 `customer_merged`); the caller resolves the survivor first, so that
    refusal means a merge committed in between and a redelivery will find
    the survivor.

    Flushes only: the customer context's consumer
    (app.customer.consumers) commits it with its processed_event row.
    """

    held_from = min(held_from, ended_at - dt.timedelta(seconds=1))
    # Both roles' advisory locks first, in the order a merge takes them
    # (sorted by role value, _repoint_vehicle_parties), then the customer row
    # FOR SHARE, then party rows: the allocation's lock order, so this never
    # deadlocks against a merge of this customer.
    roles = sorted((VehiclePartyRole.OWNER, VehiclePartyRole.KEEPER), key=lambda role: role.value)
    for role in roles:
        _lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
    _refuse_merged_customer(db, customer_id)
    # KAN-663: both roles' rows in id order, in one statement, before any is
    # written — never the keeper rows and then the owner rows.
    _lock_rows_of_roles(db, vehicle_id=vehicle_id, roles=roles, group_id=group_id)
    written: list[VehicleParty] = []
    for role in roles:
        own_rows = list(
            db.scalars(
                select(VehicleParty)
                .where(
                    VehicleParty.vehicle_id == vehicle_id,
                    VehicleParty.customer_id == customer_id,
                    VehicleParty.role == role,
                    VehicleParty.merged_into_customer_id.is_(None),
                    VehicleParty.effective_from < ended_at,
                    or_(VehicleParty.effective_to.is_(None), VehicleParty.effective_to >= ended_at),
                )
                .order_by(VehicleParty.effective_from)
                .with_for_update()
            ).all()
        )
        for row in own_rows:
            if row.effective_to is None or row.effective_to > ended_at:
                _close_vehicle_party(db, party=row, actor_id=actor_id, group_id=group_id, effective_to=ended_at)
        # A holding row closed here may start after `held_from`: the stretch
        # before it is filled below like any other (KAN-566).
        # KAN-566 (Anto, 2026-11-11, option B): the customer's own rows and
        # the history written here join without gap or overlap. Case 1: an own
        # row starting exactly at `held_from` and ended before the purchase
        # (typed by hand, say) is ended at the purchase — the same row, so
        # uq_vehicle_party_scope (vehicle, customer, role, effective_from)
        # never sees a second row with that start — or at the start of the
        # customer's next own row inside the period, never over it. Case 2:
        # every stretch of [held_from, ended_at) no own row covers gets a
        # closed history row, so a row ending (or starting) inside the period
        # is met, never overlapped and never left with a gap beside it.
        mine = (
            VehicleParty.vehicle_id == vehicle_id,
            VehicleParty.customer_id == customer_id,
            VehicleParty.role == role,
            VehicleParty.merged_into_customer_id.is_(None),
            VehicleParty.merged_into_vehicle_id.is_(None),
        )
        same_start = db.scalars(
            select(VehicleParty)
            .where(
                VehicleParty.vehicle_id == vehicle_id,
                VehicleParty.customer_id == customer_id,
                VehicleParty.role == role,
                VehicleParty.effective_from == held_from,
            )
            .with_for_update()
        ).first()
        if same_start is not None:
            # A merge's kept copy is never moved (KAN-177); it keeps standing
            # for the period, as before.
            if same_start.merged_into_customer_id is not None or same_start.merged_into_vehicle_id is not None:
                continue
            next_start = db.scalar(
                select(func.min(VehicleParty.effective_from)).where(
                    *mine, VehicleParty.effective_from > held_from, VehicleParty.effective_from < ended_at
                )
            )
            _extend_to_purchase(
                db, party=same_start, ended_at=next_start or ended_at, actor_id=actor_id, group_id=group_id
            )
        for gap_from, gap_to in _uncovered(db, mine=mine, held_from=held_from, ended_at=ended_at):
            taken = db.scalars(
                select(VehicleParty)
                .where(
                    VehicleParty.vehicle_id == vehicle_id,
                    VehicleParty.customer_id == customer_id,
                    VehicleParty.role == role,
                    VehicleParty.effective_from == gap_from,
                )
                .with_for_update()
            ).first()
            if taken is not None:
                # uq_vehicle_party_scope allows no second row with this start.
                # An own row starting there is an empty one (a plan closed at
                # its own start, KAN-664): it is extended over the stretch, as
                # case 1 extends its row. A merge's kept copy stands for it.
                if taken.merged_into_customer_id is None and taken.merged_into_vehicle_id is None:
                    _extend_to_purchase(db, party=taken, ended_at=gap_to, actor_id=actor_id, group_id=group_id)
                continue
            written.append(
                _write_history_row(
                    db, vehicle_id=vehicle_id, customer_id=customer_id, role=role, group_id=group_id,
                    effective_from=gap_from, effective_to=gap_to, actor_id=actor_id,
                )
            )
    db.flush()
    return written


def _uncovered(
    db: Session, *, mine: tuple, held_from: dt.datetime, ended_at: dt.datetime
) -> list[tuple[dt.datetime, dt.datetime]]:
    """The stretches of [held_from, ended_at) no own row of the customer
    covers (KAN-566). Rows holding at `ended_at` were closed before this is
    asked, so every own row here has an end."""

    rows = db.execute(
        select(VehicleParty.effective_from, VehicleParty.effective_to)
        .where(*mine, VehicleParty.effective_from < ended_at, VehicleParty.effective_to >= held_from)
        .order_by(VehicleParty.effective_from)
    ).all()
    gaps: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = held_from
    for row_from, row_to in rows:
        if row_to <= row_from:
            continue  # an empty row («planned, never started», KAN-664) covers nothing
        if row_from > cursor:
            gaps.append((cursor, row_from))
        cursor = max(cursor, row_to)
    if cursor < ended_at:
        gaps.append((cursor, ended_at))
    return gaps


def _write_history_row(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    customer_id: uuid.UUID,
    role: VehiclePartyRole,
    group_id: uuid.UUID,
    effective_from: dt.datetime,
    effective_to: dt.datetime,
    actor_id: uuid.UUID | None,
) -> VehicleParty:
    """A closed row of the former holder (KAN-189): history, so it closes
    nobody (KAN-370); audited `vehicle_party_add`, published `linked`."""

    party = VehicleParty(
        vehicle_id=vehicle_id, customer_id=customer_id, role=role,
        effective_from=effective_from, effective_to=effective_to,
    )
    _write_vehicle_labels(db, [party])
    db.add(party)
    db.flush()
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer_id,
        tenant_id=group_id,
        action="vehicle_party_add",
        actor_id=actor_id,
        after={
            "vehicleId": str(vehicle_id),
            "role": role.value,
            "effectiveFrom": effective_from.isoformat(),
            "effectiveTo": effective_to.isoformat(),
        },
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


def _extend_to_purchase(
    db: Session, *, party: VehicleParty, ended_at: dt.datetime, actor_id: uuid.UUID | None, group_id: uuid.UUID
) -> None:
    """KAN-566: an own history row of the trade-in's customer is extended to
    `ended_at` — case 1's row starting at the signature (to the purchase, or
    to the start of the customer's next own row inside the period, never over
    it), or an empty own row at the start of an uncovered stretch (to the
    stretch's end).
    It was history and stays history (it ended before it was moved, and ends
    in the past now), so it closes nobody and publishes nothing — as a PATCH
    moving a past end does not; audited `vehicle_party_update` with reason
    `trade_in_purchased`."""

    if party.effective_to is None or party.effective_to >= ended_at:
        return
    before = {
        "vehicleId": str(party.vehicle_id),
        "role": party.role.value,
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat(),
    }
    party.effective_to = ended_at
    db.flush()
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=group_id,
        action="vehicle_party_update",
        actor_id=actor_id,
        before=before,
        after={"effectiveTo": ended_at.isoformat()},
        reason="trade_in_purchased",
    )


def reopen_former_holder_after_storno(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    customer_id: uuid.UUID,
    group_id: uuid.UUID,
    closed_after: dt.datetime,
    closed_until: dt.datetime,
    actor_id: uuid.UUID | None,
) -> list[VehicleParty]:
    """KAN-572 (Anto, 2026-11-07, option C): Stock cancelled a trade-in's
    purchase and said the car went back to `customer_id`, who traded it in.
    That customer holds it again, so per role (owner, keeper) the row the
    purchase closed (KAN-189, `record_former_holder_on_purchase`) is reopened
    — `effectiveTo` back to open, the same row, so the history shows no
    handover that did not happen.

    The row is this customer's latest of that role closed between the
    contract's signature (`closed_after`) and the storno (`closed_until`):
    the purchase closed it there, at the purchase date's start or at the
    booking. A role the customer holds now is left alone (a redelivery, or a
    later allocation), and so is a role with no such row (a purchase this
    context never recorded). A role is left alone too when a row of it was
    recorded after the storno and, as it stands now, is open or ends after
    it was recorded (a newer fact; a history row recorded later does not
    count), or a holder of it starts after the storno. Not caught (API only,
    the accepted residual in customer.md): an older row reopened by a PATCH
    after the storno is closed by a late storno, and a row recorded after the
    storno whose end a PATCH moved back before its own recording reads as
    history, so a late storno reopens over it. Otherwise a reopen goes through the
    close rule like every other (KAN-148): every other open holder of the role
    in the group is closed, under the allocation's locks and order — advisory, then the
    customer row (`customer_merged` refused, KAN-365), then party rows. The
    copy a merge kept (`merged_into_customer_id`) is never reopened.

    Audited as `vehicle_party_update` with reason `trade_in_returned_to_customer`
    and published as `customer.vehicle_party.linked`, as a reopen by PATCH is
    (KAN-352). Flushes only: the consumer commits it with its processed_event
    row."""

    roles = sorted((VehiclePartyRole.OWNER, VehiclePartyRole.KEEPER), key=lambda role: role.value)
    for role in roles:
        _lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
    _refuse_merged_customer(db, customer_id)
    # KAN-663: both roles' rows in id order, in one statement — never this
    # customer's row and then the role's older holders.
    _lock_rows_of_roles(db, vehicle_id=vehicle_id, roles=roles, group_id=group_id)
    now = utcnow()
    reopened: list[VehicleParty] = []
    for role in roles:
        mine = (
            VehicleParty.vehicle_id == vehicle_id,
            VehicleParty.customer_id == customer_id,
            VehicleParty.role == role,
            VehicleParty.merged_into_customer_id.is_(None),
        )
        holds_now = db.scalar(
            select(VehicleParty.id).where(
                *mine,
                VehicleParty.effective_from <= now,
                or_(VehicleParty.effective_to.is_(None), VehicleParty.effective_to > now),
            )
        )
        if holds_now is not None:
            continue
        party = db.scalars(
            select(VehicleParty)
            .where(
                *mine,
                VehicleParty.effective_to >= closed_after,
                VehicleParty.effective_to <= closed_until,
            )
            .order_by(VehicleParty.effective_to.desc())
            .limit(1)
            .with_for_update()
        ).first()
        if party is None:
            continue
        # A row of this role in the group recorded after the storno that, as
        # it stands now, is open or ends after it was recorded (a customer
        # linked since, or linked and disconnected since) is a newer fact than
        # this storno: a late delivery must not overwrite it, so the role is
        # left as it is. A holder recorded before the storno but
        # starting after it (a future start) is left too: closing it would end
        # it before it began.
        newer_row = db.scalar(
            select(VehicleParty.id)
            .join(Customer, Customer.id == VehicleParty.customer_id)
            .where(
                VehicleParty.vehicle_id == vehicle_id,
                VehicleParty.role == role,
                Customer.group_id == group_id,
                VehicleParty.id != party.id,
                VehicleParty.created_at > closed_until,
                # …and, as it stands now, open or ending after it was
                # recorded: a history row (ended before it was recorded)
                # closes nobody (KAN-370), so it does not stop the reopen.
                or_(VehicleParty.effective_to.is_(None), VehicleParty.effective_to > VehicleParty.created_at),
            )
            .limit(1)
        )
        others = _open_holders_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id, excluding=party.id)
        if newer_row is not None or any(other.effective_from > closed_until for other in others):
            continue
        for other in others:
            _close_vehicle_party(db, party=other, actor_id=actor_id, group_id=group_id)
        before = {
            "vehicleId": str(party.vehicle_id),
            "role": party.role.value,
            "effectiveFrom": party.effective_from.isoformat(),
            "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        }
        party.effective_to = None
        db.flush()
        record_audit_event(
            db,
            entity_type="customer",
            entity_id=customer_id,
            tenant_id=group_id,
            action="vehicle_party_update",
            actor_id=actor_id,
            before=before,
            after={"effectiveTo": None},
            reason="trade_in_returned_to_customer",
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
        reopened.append(party)
    db.flush()
    return reopened
