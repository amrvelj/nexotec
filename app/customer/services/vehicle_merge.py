"""KAN-634 (FR-V-12, ADR-131) — a vehicle merge reaches customer's party rows
as a fact, never as a call.

Vehicle merges two vehicle-mdm records and publishes
`vehicle.vehicle_mdm.merged` in the merge's own transaction
(`app/vehicle/services/merge.py`); customer, which sits above vehicle and
owns `VehicleParty`, consumes it here (`customer.vehicle_parties_on_vehicle_merged`,
registered in `app.worker.register_handlers`) and re-points every party row
naming the duplicate at the survivor, in its own transaction with the
processed_event row (rules 4, 6 and 12; ADR-047).

The event's contract: the duplicate is the aggregate (`aggregate_id`),
`tenant_id` is null (vehicle-mdm is global, ADR-022), and the payload carries
`vehicleId` (the duplicate) and `mergedIntoVehicleId` (the survivor the merge
named).

Every row moves — every dealer group's, open, closed or planned — and takes
the survivor's label (KAN-84); a row that only moves is not audited or
published. Two things the move would otherwise break are settled in the same
transaction (KAN-672; Anto's ruling 2026-11-12, option C):

- **One holder per role and dealer group.** Where both cars bring a holder of
  a role in one group, the holder with the later start stays and the others
  are closed at its start, through the close rule's own `_close_vehicle_party`
  (audited `vehicle_party_remove`, reason `vehicle_merged`, published
  `unlinked`) — see `_settle_role`.
- **The same customer, role and start on both cars** cannot move
  (`uq_vehicle_party_scope`). Like the copy a customer merge keeps (KAN-177),
  that row stays on the duplicate, closed at its own start and marked
  `merged_into_vehicle_id` — see `_keep_copy`. Where the duplicate's row ends
  later than the survivor's (or not at all), the survivor's row takes that
  end first, so the customer remains the holder (Anto, 2026-11-13) — see
  `_take_later_end`.
"""

import datetime as dt
import itertools
import logging
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.core.outbox_model import OutboxMessage
from app.customer.models.customer import Customer
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.customer_common import _EVENT_PRODUCER
from app.customer.services.vehicle_party import _close_vehicle_party, _vehicle_party_payload
from app.customer.services.vehicle_party_labels import relabel_vehicle_parties_for_vehicle
from app.customer.services.vehicle_party_locks import _lock_vehicle_role_in_group, _try_lock_vehicle_role_in_group
from app.vehicle.public import resolve_merged_vehicle_id

logger = logging.getLogger(__name__)

# The audit reason of every close and mark this consumer writes.
VEHICLE_MERGED_REASON = "vehicle_merged"

_RoleLock = tuple[uuid.UUID, VehiclePartyRole, uuid.UUID]


class VehiclePartyRoleBusyError(RuntimeError):
    """Another writer holds a (vehicle, role, group) lock this move found it
    needs only after it had taken the others. Waiting for it out of order
    could deadlock, so the delivery fails and the transport retries it."""


def repoint_vehicle_parties_on_vehicle_merge(
    db: Session, *, duplicate_vehicle_id: uuid.UUID, survivor_vehicle_id: uuid.UUID
) -> int:
    """Moves every party row naming the duplicate onto the survivor, settles
    the roles both cars held (KAN-672) and relabels the survivor's rows from
    `app.vehicle.public` (never from the event). Flushes only — the consumer
    harness commits it with the processed_event row. Returns the number of
    rows moved.

    The survivor is resolved to the live record first: delivery is
    unordered, so A→B consumed after B→C must land A's rows on C, not on the
    merged B. A duplicate no row names moves nothing — a kept copy still names
    it and is skipped by its mark — so a late or repeated event is harmless
    even past the processed-events guard.

    Locks, in the order every writer of party rows takes them (KAN-148,
    KAN-356): first the (vehicle, role, group) advisory lock of both cars for
    every role and group the duplicate has a row in, sorted; then the rows of
    both vehicles FOR NO KEY UPDATE in one statement, in id order — so this
    never interleaves with an allocation, a reopen or a customer merge on a
    role it settles, and never holds one vehicle's rows while waiting for the
    other's."""

    target = resolve_merged_vehicle_id(db, survivor_vehicle_id)
    if target == duplicate_vehicle_id:
        # A cycle the vehicle side never writes; moving rows onto the record
        # they are on would only relabel them.
        logger.warning("Vehicle merge of %s resolves back to itself; no party row moved.", duplicate_vehicle_id)
        return 0
    held = _lock_roles(db, duplicate_vehicle_id=duplicate_vehicle_id, target=target)
    rows = list(
        db.scalars(
            select(VehicleParty)
            .where(
                or_(
                    VehicleParty.vehicle_id.in_((duplicate_vehicle_id, target)),
                    VehicleParty.merged_into_vehicle_id == duplicate_vehicle_id,
                )
            )
            .order_by(VehicleParty.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    # A copy an earlier merge kept for this duplicate (it was that merge's
    # survivor) follows it, so the mark always names the live record.
    for party in rows:
        if party.merged_into_vehicle_id == duplicate_vehicle_id:
            party.merged_into_vehicle_id = target
    leaving = [
        party for party in rows if party.vehicle_id == duplicate_vehicle_id and party.merged_into_vehicle_id is None
    ]
    if not leaving:
        db.flush()
        return 0

    groups = _group_ids(db, rows)
    # A row committed between the advisory locks and the row lock can name a
    # role not locked yet: tried, never waited for (out of the sorted order).
    late = {
        (vehicle_id, party.role, groups[party.customer_id])
        for party in leaving
        if party.customer_id in groups
        for vehicle_id in (duplicate_vehicle_id, target)
    } - held
    _take_role_locks(db, late, wait=False)

    now = utcnow()
    on_target = {
        (party.customer_id, party.role, party.effective_from): party for party in rows if party.vehicle_id == target
    }
    moved: list[VehicleParty] = []
    # The survivor's rows that took their copy's later end: what the duplicate
    # brought, as a moved row is, when the role is settled.
    extended: list[VehicleParty] = []
    # A kept copy's id -> the survivor's row that stands for it.
    stands_for: dict[uuid.UUID, uuid.UUID] = {}
    for party in leaving:
        key = (party.customer_id, party.role, party.effective_from)
        if key in on_target:
            stands_for[party.id] = on_target[key].id
            group_id = groups.get(party.customer_id)
            # Anto's ruling is for a customer *still* holding on the duplicate:
            # a copy that ended in the past leaves the survivor's history as it is.
            if (
                group_id is not None
                and _holds(party, now)
                and _take_later_end(
                    db,
                    on_target[key],
                    until=party.effective_to,
                    link=party.closed_for_party_id,
                    group_id=group_id,
                    now=now,
                )
            ):
                extended.append(on_target[key])
            _keep_copy(db, party, target=target, group_id=group_id, now=now)
            continue
        party.vehicle_id = target
        on_target[key] = party
        moved.append(party)
    # A row waiting for a planned holder (KAN-644) that is itself a kept copy
    # — the same customer planned on both cars — waits for the survivor's row
    # of that holder instead: the copy stays on the duplicate and never
    # changes again, so cancelling or moving the planned holder would not
    # reach a row still linked to it.
    for party in rows:
        if party.vehicle_id == target and party.closed_for_party_id in stands_for:
            party.closed_for_party_id = stands_for[party.closed_for_party_id]
    db.flush()

    moved_ids = {party.id for party in moved + extended}
    for role, group_id in sorted(
        {(party.role, groups[party.customer_id]) for party in moved + extended if party.customer_id in groups},
        key=lambda pair: (pair[0].value, str(pair[1])),
    ):
        _settle_role(
            db,
            [
                party
                for party in rows
                if party.vehicle_id == target and party.role == role and groups.get(party.customer_id) == group_id
            ],
            moved_ids=moved_ids,
            extended_ids={party.id for party in extended},
            group_id=group_id,
            now=now,
        )
    db.flush()
    relabel_vehicle_parties_for_vehicle(db, vehicle_id=target)
    return len(moved)


def _lock_roles(db: Session, *, duplicate_vehicle_id: uuid.UUID, target: uuid.UUID) -> set[_RoleLock]:
    """The (vehicle, role, group) advisory lock of the duplicate and of the
    survivor, for every role and dealer group the duplicate has a row in —
    the group through `Customer.group_id`, as the close rule finds it
    (KAN-99). Sorted by vehicle, then role, the order a customer merge takes
    its own in (KAN-356), so the two never deadlock. The pairs are read again
    until every one is held; one that appeared while this waited is out of the
    sorted order and only tried."""

    held: set[_RoleLock] = set()
    while True:
        pairs = db.execute(
            select(VehicleParty.role, Customer.group_id)
            .join(Customer, Customer.id == VehicleParty.customer_id)
            .where(VehicleParty.vehicle_id == duplicate_vehicle_id, VehicleParty.merged_into_vehicle_id.is_(None))
            .distinct()
        ).all()
        missing = {
            (vehicle_id, role, group_id) for role, group_id in pairs for vehicle_id in (duplicate_vehicle_id, target)
        } - held
        if not missing:
            return held
        _take_role_locks(db, missing, wait=not held)
        held |= missing


def _take_role_locks(db: Session, locks: set[_RoleLock], *, wait: bool) -> None:
    for vehicle_id, role, group_id in sorted(locks, key=lambda lock: (str(lock[0]), lock[1].value, str(lock[2]))):
        if wait:
            _lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
        elif not _try_lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id):
            raise VehiclePartyRoleBusyError(
                f"The {role.value} of vehicle {vehicle_id} is being changed right now; the merge's move is retried."
            )


def _group_ids(db: Session, rows: list[VehicleParty]) -> dict[uuid.UUID, uuid.UUID]:
    """Each row's dealer group, through its customer (the row has no group
    column, KAN-99). A row whose customer no longer exists is in no group: it
    moves, and no role is settled for it."""

    customer_ids = {party.customer_id for party in rows}
    if not customer_ids:
        return {}
    found = db.execute(select(Customer.id, Customer.group_id).where(Customer.id.in_(customer_ids))).all()
    return {customer_id: group_id for customer_id, group_id in found}


def _holds(party: VehicleParty, now: dt.datetime) -> bool:
    """`_holds_now_or_later` on a loaded row: open, or ending later, and never
    an empty period (a cancelled planned row, a merge's kept copy)."""

    return party.effective_to is None or (party.effective_to > now and party.effective_to > party.effective_from)


def _take_later_end(
    db: Session,
    kept: VehicleParty,
    *,
    until: dt.datetime | None,
    link: uuid.UUID | None,
    group_id: uuid.UUID,
    now: dt.datetime,
) -> bool:
    """KAN-672 (Anto, 2026-11-13) — the same customer holds the role on both
    cars from the same start, and the duplicate's row ends later than the
    survivor's (`kept`), or not at all: the customer remains the holder. The
    survivor's row takes the duplicate's end (`until`; open when that is) and,
    with it, the duplicate row's link to the planned holder that end waits
    for (`link`, KAN-644: that holder moves to the survivor under its own id
    — or, when it is itself a kept copy, the caller re-points the link to the
    survivor's row of it — so cancelling it or moving its start still reaches
    this row) — audited
    `vehicle_party_update` (reason `vehicle_merged`,
    no actor) and, when the row did not hold its role and now does, published
    `linked`. An end that is not later changes nothing, and a copy an earlier
    customer merge kept (KAN-177) is never given a period. Returns whether
    the row changed; the caller then settles its role, counting it among the
    rows the duplicate brought — so a holder with a later start on the
    survivor still ends it at that start (`_settle_role`). The caller holds
    the role's lock and the row's."""

    if kept.effective_to is None or (until is not None and until <= kept.effective_to):
        return False
    if kept.merged_into_customer_id is not None or kept.merged_into_vehicle_id is not None:
        return False
    held_before = _holds(kept, now)
    before = {
        "effectiveFrom": kept.effective_from.isoformat(),
        "effectiveTo": kept.effective_to.isoformat(),
    }
    kept.effective_to = until
    kept.closed_for_party_id = link
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=kept.customer_id,
        tenant_id=group_id,
        action="vehicle_party_update",
        actor_id=None,
        before=before,
        after={
            "effectiveFrom": kept.effective_from.isoformat(),
            "effectiveTo": until.isoformat() if until else None,
        },
        reason=VEHICLE_MERGED_REASON,
    )
    if _holds(kept, now) and not held_before:
        publish_event(
            db,
            OutboxEvent(
                event_type="customer.vehicle_party.linked",
                tenant_id=group_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="vehicle_party",
                aggregate_id=kept.id,
                payload=_vehicle_party_payload(kept),
            ),
        )
    return True


def _keep_copy(
    db: Session, party: VehicleParty, *, target: uuid.UUID, group_id: uuid.UUID | None, now: dt.datetime
) -> None:
    """The survivor already carries this customer's row of this role from
    this very start, so the duplicate's cannot move (`uq_vehicle_party_scope`).
    It is kept as the customer merge keeps its copy (KAN-177): on the
    duplicate, marked with the vehicle it was merged into, never deleted. One
    that holds its role — open, or ending later — is closed at its own start
    (`vehicle_party_remove`, `unlinked`, both carrying `mergedIntoVehicleId`),
    so it never reads as a current party again; one that already ended keeps
    its end, takes the mark and is audited as a `vehicle_party_update`. The
    survivor's row has taken the copy's end before, where that is later
    (`_take_later_end`)."""

    if group_id is not None and _holds(party, now):
        _close_vehicle_party(
            db,
            party=party,
            actor_id=None,
            group_id=group_id,
            effective_to=party.effective_from,
            merged_into_vehicle_id=target,
            reason=VEHICLE_MERGED_REASON,
        )
        return
    party.merged_into_vehicle_id = target
    if group_id is None:
        # No customer, so no group to stamp an audit row or an event with.
        logger.warning("Vehicle party %s names no customer; kept on its merged vehicle unaudited.", party.id)
        if _holds(party, now):
            party.effective_to = party.effective_from
        return
    period = {
        "vehicleId": str(party.vehicle_id),
        "role": party.role.value,
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
    }
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=group_id,
        action="vehicle_party_update",
        actor_id=None,
        before=period,
        after={**period, "mergedIntoVehicleId": str(target)},
        reason=VEHICLE_MERGED_REASON,
    )


def _settle_role(
    db: Session,
    parties: list[VehicleParty],
    *,
    moved_ids: set[uuid.UUID],
    extended_ids: set[uuid.UUID],
    group_id: uuid.UUID,
    now: dt.datetime,
) -> None:
    """KAN-672 (Anto, 2026-11-12, option C) — one role of the survivor in one
    dealer group, after the move. Nothing is done unless both cars brought a
    row that holds the role now or later: a role only one of them held is as
    it was, and what was already wrong on one car is not this consumer's to
    repair — except in a role with a survivor's row that took its copy's
    later end (`extended_ids`): that row is both cars' at once, so its role
    is always settled, which also leaves a double holder the duplicate alone
    brought with one current holder. Never another group's row
    (KAN-99): `parties` is one group's.

    Of the holders current now, the one with the later start stays; every
    other is closed **at that start**, the instant it was replaced — through
    `_close_vehicle_party`, as a new holder closes the previous one: audited
    `vehicle_party_remove` (reason `vehicle_merged`, no actor) and published
    `unlinked`, losing any link to a planned handover. Equal starts (Anto,
    2026-11-13): the holder on the surviving record stays (then the row
    recorded later), and the other is closed at its own start — an empty period, it never held the
    role on the merged car.

    A holder whose start is still to come («ab …», KAN-583/KAN-644) is what
    it would be had it been allocated on the survivor: the current holder
    keeps the role until that start, closed there and linked
    (`closed_for_party_id`), with no `unlinked`; one already ending first is
    spared. Two planned holders follow the same rule between them — the
    earlier ends where the later starts.
    """

    holding = [party for party in parties if _holds(party, now)]
    brought = [party for party in holding if party.id in moved_ids]
    if not brought:
        return
    if len(brought) == len(holding) and not any(party.id in extended_ids for party in holding):
        return

    def rank(party: VehicleParty) -> tuple[dt.datetime, bool, uuid.UUID]:
        return (party.effective_from, party.id not in moved_ids, party.id)

    current = sorted((party for party in holding if party.effective_from <= now), key=rank)
    planned = sorted((party for party in holding if party.effective_from > now), key=rank)
    for replaced in current[:-1]:
        _close_vehicle_party(
            db,
            party=replaced,
            actor_id=None,
            group_id=group_id,
            effective_to=current[-1].effective_from,
            reason=VEHICLE_MERGED_REASON,
        )
    chain = current[-1:] + planned
    for earlier, later in itertools.pairwise(chain):
        if earlier.effective_to is not None and earlier.effective_to <= later.effective_from:
            continue
        empty = later.effective_from == earlier.effective_from
        _close_vehicle_party(
            db,
            party=earlier,
            actor_id=None,
            group_id=group_id,
            effective_to=later.effective_from,
            closed_for_party_id=None if empty else later.id,
            reason=VEHICLE_MERGED_REASON,
        )
        if empty:
            # Two planned holders from the same instant: the rows waiting for
            # the one that never starts now wait for the one that does.
            for party in parties:
                if party.closed_for_party_id == earlier.id:
                    party.closed_for_party_id = later.id


def handle_vehicle_merged_message(db: Session, message: OutboxMessage) -> None:
    """The consumer of `vehicle.vehicle_mdm.merged`
    (`customer.vehicle_parties_on_vehicle_merged`). Flushes only;
    consume_once commits it together with the processed_event row."""

    payload = message.payload or {}
    survivor = payload.get("mergedIntoVehicleId")
    if not survivor:
        raise ValueError(f"vehicle.vehicle_mdm.merged message {message.id} names no mergedIntoVehicleId.")
    repoint_vehicle_parties_on_vehicle_merge(
        db, duplicate_vehicle_id=message.aggregate_id, survivor_vehicle_id=uuid.UUID(survivor)
    )
