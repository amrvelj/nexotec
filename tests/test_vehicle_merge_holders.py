"""KAN-672 (Anto's ruling 2026-11-12, option C) — what a vehicle merge does to
the holders of a role when both cars bring one: the later start stays, the
others are closed at its start; and the row the survivor already carries for
the same customer, role and start is kept on the duplicate, marked, instead
of colliding with `uq_vehicle_party_scope`. Anto's rulings of 2026-11-13: at
equal starts the holder on the surviving record stays, and a customer whose
row is still current on the duplicate but ended on the survivor remains the
holder — the survivor's row takes the later end.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.core.reconciliation import find_rows_in_state
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.reconciliation import CHECKS as CUSTOMER_CHECKS
from app.customer.schemas.customer import CustomerVehicleUpdate
from app.customer.services.vehicle_merge import repoint_vehicle_parties_on_vehicle_merge
from app.customer.services.vehicle_party import (
    allocate_vehicle_party,
    delete_customer_vehicle,
    update_customer_vehicle,
)
from app.customer.services.vehicle_party_locks import _lock_vehicle_role_in_group
from app.vehicle.services.merge import merge_vehicles
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION
from tests.test_sales_lifecycle_reservation import _customer
from tests.test_vehicle_merge import _deliver_merged, _merged_message

OWNER = VehiclePartyRole.OWNER
Y2020 = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
Y2022 = dt.datetime(2022, 1, 1, tzinfo=dt.UTC)
Y2023 = dt.datetime(2023, 1, 1, tzinfo=dt.UTC)
VINS = ("ZAR94000007123456", "WVWZZZ1JZXW000001", "1HGCM82633A004352")


def _cars(db, count=2):
    return [create_vehicle_mdm(db, vin=vin, catalogue_variant_id=None) for vin in VINS[:count]]


def _holder(db, vehicle, group_id, *, customer=None, role=OWNER, **dates) -> VehicleParty:
    customer = customer or _customer(db, group_id)
    return allocate_vehicle_party(
        db, vehicle_id=vehicle.id, customer_id=customer.id, role=role, group_id=group_id, actor_id=uuid.uuid4(),
        **dates,
    )


def _raw(db, vehicle, group_id, **columns) -> VehicleParty:
    """A row written past the close rule — the second current holder only
    legacy data has."""

    row = VehicleParty(vehicle_id=vehicle.id, customer_id=_customer(db, group_id).id, role=OWNER, **columns)
    db.add(row)
    db.commit()
    return row


def _merge(db, engine, survivor, duplicate) -> None:
    merge_vehicles(db, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    _deliver_merged(engine, _merged_message(db, duplicate.id))
    db.expire_all()


def _row(db, party) -> VehicleParty:
    return db.get(VehicleParty, party.id)


def _unlinked(db, party) -> list[OutboxMessage]:
    return list(
        db.scalars(
            select(OutboxMessage).where(
                OutboxMessage.event_type == "customer.vehicle_party.unlinked", OutboxMessage.aggregate_id == party.id
            )
        )
    )


def _linked(db, party) -> list[OutboxMessage]:
    return list(
        db.scalars(
            select(OutboxMessage).where(
                OutboxMessage.event_type == "customer.vehicle_party.linked", OutboxMessage.aggregate_id == party.id
            )
        )
    )


def _merge_audits(db, party) -> list[AuditEvent]:
    rows = db.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == party.customer_id, AuditEvent.reason == "vehicle_merged")
    )
    return list(rows)


def _current(db, vehicle, role=OWNER) -> set[uuid.UUID]:
    now = dt.datetime.now(dt.UTC)
    rows = db.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == vehicle.id, VehicleParty.role == role))
    return {
        row.id
        for row in rows
        if row.effective_from <= now and (row.effective_to is None or row.effective_to > now)
    }


def test_the_holder_with_the_later_start_stays_and_the_other_is_closed_at_that_start(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id, other_group = uuid.uuid4(), uuid.uuid4()
    older = _holder(db_session, survivor, group_id, effective_from=Y2020)
    later = _holder(db_session, duplicate, group_id, effective_from=Y2022)
    survivor_keeper = _holder(db_session, survivor, group_id, role=VehiclePartyRole.KEEPER, effective_from=Y2020)
    # KAN-99: another dealer group's holder is another timeline — never closed.
    elsewhere = _holder(db_session, duplicate, other_group, effective_from=Y2023)

    _merge(db_session, engine, survivor, duplicate)

    assert _current(db_session, survivor) == {later.id, elsewhere.id}
    closed = _row(db_session, older)
    assert (closed.vehicle_id, closed.effective_to, closed.closed_for_party_id) == (survivor.id, Y2022, None)
    assert _row(db_session, later).effective_to is None
    assert _row(db_session, elsewhere).effective_to is None
    assert _row(db_session, survivor_keeper).effective_to is None  # a role only one car held
    [audit] = _merge_audits(db_session, older)
    assert (audit.action, audit.actor_id, audit.tenant_id) == ("vehicle_party_remove", None, group_id)
    assert audit.after == {"effectiveTo": Y2022.isoformat()}
    [event] = _unlinked(db_session, older)
    assert event.tenant_id == group_id
    assert event.payload["effectiveTo"] == Y2022.isoformat()
    assert event.payload["vehicleId"] == str(survivor.id)
    for untouched in (later, elsewhere, survivor_keeper):
        assert _unlinked(db_session, untouched) == []
        assert _merge_audits(db_session, untouched) == []


def test_the_survivors_own_holder_is_closed_when_the_duplicates_started_later(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    later = _holder(db_session, survivor, group_id, effective_from=Y2022)
    older = _holder(db_session, duplicate, group_id, effective_from=Y2020)

    _merge(db_session, engine, survivor, duplicate)

    assert _current(db_session, survivor) == {later.id}
    assert _row(db_session, older).effective_to == Y2022
    assert len(_unlinked(db_session, older)) == 1


def test_equal_starts_keep_the_survivors_holder_and_end_the_other_where_it_began(db_session, engine):
    """Anto, 2026-11-13 (ruling 1): two different customers hold the role from
    the same start — the holder on the surviving record stays."""

    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    brought = _holder(db_session, duplicate, group_id, effective_from=Y2022)
    kept = _holder(db_session, survivor, group_id, effective_from=Y2022)

    _merge(db_session, engine, survivor, duplicate)

    assert _current(db_session, survivor) == {kept.id}
    ended = _row(db_session, brought)
    assert (ended.vehicle_id, ended.effective_to) == (survivor.id, Y2022)
    assert len(_unlinked(db_session, brought)) == 1
    assert _row(db_session, kept).effective_to is None
    assert _unlinked(db_session, kept) == []
    assert _merge_audits(db_session, kept) == []


def test_every_other_current_holder_is_closed_at_the_latest_start(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    first = _holder(db_session, survivor, group_id, effective_from=Y2020)
    second = _raw(db_session, survivor, group_id, effective_from=Y2022)
    latest = _holder(db_session, duplicate, group_id, effective_from=Y2023)

    _merge(db_session, engine, survivor, duplicate)

    assert _current(db_session, survivor) == {latest.id}
    assert [_row(db_session, row).effective_to for row in (first, second)] == [Y2023, Y2023]


def test_rows_that_already_ended_move_and_close_nobody(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    current = _holder(db_session, survivor, group_id, effective_from=Y2020)
    former = _holder(db_session, duplicate, group_id, effective_from=Y2022, effective_to=Y2023)
    cancelled = _raw(db_session, duplicate, group_id, effective_from=Y2023, effective_to=Y2023)

    _merge(db_session, engine, survivor, duplicate)

    assert _row(db_session, current).effective_to is None
    assert (_row(db_session, former).vehicle_id, _row(db_session, former).effective_to) == (survivor.id, Y2023)
    assert _row(db_session, cancelled).vehicle_id == survivor.id
    assert db_session.scalars(select(AuditEvent).where(AuditEvent.reason == "vehicle_merged")).all() == []


def test_a_planned_holder_ends_the_current_one_at_its_start_and_is_linked(db_session, engine):
    """KAN-644: «ab <date>» on the duplicate is what it would be had it been
    recorded on the survivor — the current holder keeps the role until then."""

    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    starts = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=30)
    current = _holder(db_session, survivor, group_id, effective_from=Y2020)
    planned = _holder(db_session, duplicate, group_id, effective_from=starts)

    _merge(db_session, engine, survivor, duplicate)

    held = _row(db_session, current)
    assert (held.effective_to, held.closed_for_party_id) == (starts, planned.id)
    assert _current(db_session, survivor) == {current.id}
    assert _row(db_session, planned).effective_to is None
    assert _unlinked(db_session, current) == []  # still holding: nothing to announce (KAN-352)
    [audit] = _merge_audits(db_session, current)
    assert audit.action == "vehicle_party_remove"


def test_the_current_holder_of_each_car_and_a_planned_one_leave_one_current_holder(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    starts = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=30)
    on_survivor = _holder(db_session, survivor, group_id, effective_from=Y2022)
    on_duplicate = _holder(db_session, duplicate, group_id, effective_from=Y2020)
    planned = _holder(db_session, duplicate, group_id, effective_from=starts)
    assert _row(db_session, on_duplicate).closed_for_party_id == planned.id

    _merge(db_session, engine, survivor, duplicate)

    replaced = _row(db_session, on_duplicate)
    assert (replaced.effective_to, replaced.closed_for_party_id) == (Y2022, None)
    held = _row(db_session, on_survivor)
    assert (held.effective_to, held.closed_for_party_id) == (starts, planned.id)
    assert _current(db_session, survivor) == {on_survivor.id}


def test_two_planned_holders_from_the_same_instant_leave_the_survivors(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    starts = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=30)
    current = _holder(db_session, duplicate, group_id, effective_from=Y2020)
    brought = _holder(db_session, duplicate, group_id, effective_from=starts)
    kept = _holder(db_session, survivor, group_id, effective_from=starts)

    _merge(db_session, engine, survivor, duplicate)

    assert _row(db_session, brought).effective_to == starts  # planned, never started
    assert _row(db_session, kept).effective_to is None
    # The current holder waits for the planned holder that does start.
    held = _row(db_session, current)
    assert (held.effective_to, held.closed_for_party_id) == (starts, kept.id)


def test_the_same_customer_from_the_same_instant_on_both_cars_is_kept_as_a_marked_copy(db_session, engine):
    """The ticket's second defect: this move broke `uq_vehicle_party_scope`,
    and the delivery raised, was retried and dead-lettered."""

    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    on_survivor = _holder(db_session, survivor, group_id, customer=customer, effective_from=Y2022)
    copy = _holder(db_session, duplicate, group_id, customer=customer, effective_from=Y2022)
    keeper = _holder(db_session, duplicate, group_id, customer=customer, role=VehiclePartyRole.KEEPER)

    _merge(db_session, engine, survivor, duplicate)

    kept = _row(db_session, copy)
    assert (kept.vehicle_id, kept.merged_into_vehicle_id, kept.effective_to) == (duplicate.id, survivor.id, Y2022)
    assert kept.vehicle_vin == duplicate.vin  # it names the merged record, so it keeps that label
    assert _row(db_session, on_survivor).effective_to is None
    assert _row(db_session, keeper).vehicle_id == survivor.id
    assert _current(db_session, survivor) == {on_survivor.id}
    [audit] = _merge_audits(db_session, copy)
    assert audit.action == "vehicle_party_remove"
    assert audit.after == {"effectiveTo": Y2022.isoformat(), "mergedIntoVehicleId": str(survivor.id)}
    [event] = _unlinked(db_session, copy)
    assert event.payload["mergedIntoVehicleId"] == str(survivor.id)
    assert event.payload["vehicleId"] == str(duplicate.id)
    # The copy is there on purpose: not the «delivery never happened» finding.
    check = next(c for c in CUSTOMER_CHECKS if c.label == "vehicle_party.vehicle_id names a merged vehicle")
    assert find_rows_in_state(db_session, check) == []

    # It never holds its role again (as KAN-177's copy).
    with pytest.raises(ConflictError) as refused:
        update_customer_vehicle(
            db_session, party=kept, data=CustomerVehicleUpdate(effective_to=None), actor_id=uuid.uuid4(),
            group_id=group_id,
        )
    # KAN-680 (Anto, 2026-11-13, option A): its own reason, naming the survivor's VIN.
    assert refused.value.details == {
        "reason": "vehicle_merged_party_row",
        "mergedIntoVehicleId": str(survivor.id),
        "mergedIntoVehicleVin": survivor.vin,
    }
    db_session.rollback()

    # Past the processed-events guard the move finds nothing left to do.
    assert (
        repoint_vehicle_parties_on_vehicle_merge(
            db_session, duplicate_vehicle_id=duplicate.id, survivor_vehicle_id=survivor.id
        )
        == 0
    )
    db_session.commit()
    assert len(_unlinked(db_session, copy)) == 1


def test_a_copy_that_already_ended_keeps_its_end_and_takes_the_mark(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    _holder(db_session, survivor, group_id, customer=customer, effective_from=Y2020)
    former = _holder(db_session, duplicate, group_id, customer=customer, effective_from=Y2020, effective_to=Y2022)

    _merge(db_session, engine, survivor, duplicate)

    kept = _row(db_session, former)
    assert (kept.vehicle_id, kept.merged_into_vehicle_id, kept.effective_to) == (duplicate.id, survivor.id, Y2022)
    assert _unlinked(db_session, former) == []  # its end was published when it happened
    [audit] = _merge_audits(db_session, former)
    assert audit.action == "vehicle_party_update"
    assert audit.after["mergedIntoVehicleId"] == str(survivor.id)


def _same_customer_on_both(db, group_id, *, survivor_ends, duplicate_ends):
    """One customer, owner of both cars since 2020: the survivor's row ended,
    the duplicate's ends at `duplicate_ends` (None: open)."""

    survivor, duplicate = _cars(db)
    customer = _customer(db, group_id)
    ended = _holder(db, survivor, group_id, customer=customer, effective_from=Y2020, effective_to=survivor_ends)
    copy = _holder(db, duplicate, group_id, customer=customer, effective_from=Y2020, effective_to=duplicate_ends)
    return survivor, duplicate, ended, copy


def _period_audits(db, party) -> list[AuditEvent]:
    """The merge's `vehicle_party_update` rows that changed a period (the
    mark an ended copy takes is one too, and changes none)."""

    return [
        audit
        for audit in _merge_audits(db, party)
        if audit.action == "vehicle_party_update" and "mergedIntoVehicleId" not in audit.after
    ]


def test_a_customer_still_current_on_the_duplicate_remains_the_holder_on_the_survivor(db_session, engine):
    """Anto, 2026-11-13 (ruling 2): the same customer, role and start on both
    cars — ended on the surviving record, open on the duplicate. The
    survivor's row is open again; the duplicate's is still the marked copy."""

    group_id = uuid.uuid4()
    survivor, duplicate, ended, copy = _same_customer_on_both(
        db_session, group_id, survivor_ends=Y2022, duplicate_ends=None
    )
    linked_before = len(_linked(db_session, ended))

    _merge(db_session, engine, survivor, duplicate)

    reopened = _row(db_session, ended)
    assert (reopened.vehicle_id, reopened.effective_to, reopened.closed_for_party_id) == (survivor.id, None, None)
    assert _current(db_session, survivor) == {ended.id}
    [audit] = _period_audits(db_session, ended)
    assert (audit.actor_id, audit.tenant_id) == (None, group_id)
    assert audit.before == {"effectiveFrom": Y2020.isoformat(), "effectiveTo": Y2022.isoformat()}
    assert audit.after == {"effectiveFrom": Y2020.isoformat(), "effectiveTo": None}
    events = _linked(db_session, ended)
    assert len(events) == linked_before + 1
    published = max(events, key=lambda event: event.id)
    assert published.tenant_id == group_id
    assert published.payload["vehicleId"] == str(survivor.id)
    assert published.payload["effectiveTo"] is None
    assert _unlinked(db_session, ended) == []
    kept = _row(db_session, copy)
    assert (kept.vehicle_id, kept.merged_into_vehicle_id, kept.effective_to) == (duplicate.id, survivor.id, Y2020)
    assert len(_unlinked(db_session, copy)) == 1


def test_a_copy_that_ended_in_the_past_leaves_the_survivors_history_as_it_is(db_session, engine):
    """Anto's ruling (2026-11-13) is for a customer still holding on the
    duplicate. A copy that ended later, but in the past, rewrites nothing on
    the surviving record."""

    group_id = uuid.uuid4()
    survivor, duplicate, ended, copy = _same_customer_on_both(
        db_session, group_id, survivor_ends=Y2022, duplicate_ends=Y2023
    )
    linked_before = len(_linked(db_session, ended))

    _merge(db_session, engine, survivor, duplicate)

    assert _row(db_session, ended).effective_to == Y2022
    assert _current(db_session, survivor) == set()
    assert _period_audits(db_session, ended) == []
    assert len(_linked(db_session, ended)) == linked_before
    kept = _row(db_session, copy)
    assert (kept.merged_into_vehicle_id, kept.effective_to) == (survivor.id, Y2023)


def test_a_planned_successor_on_the_duplicate_keeps_its_link_to_the_row_that_took_the_later_end(db_session, engine):
    """The duplicate's row ends where its planned holder starts and is linked
    to it (KAN-644). The survivor's row takes that end *and* that link, so
    cancelling the planned holder or moving its start still reaches it —
    without the link, moving the start earlier left two current holders."""

    group_id = uuid.uuid4()
    starts = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=30)
    survivor, duplicate = _cars(db_session)
    customer = _customer(db_session, group_id)
    ended = _holder(db_session, survivor, group_id, customer=customer, effective_from=Y2020, effective_to=Y2022)
    copy = _holder(db_session, duplicate, group_id, customer=customer, effective_from=Y2020)
    planned = _holder(db_session, duplicate, group_id, effective_from=starts)
    assert (_row(db_session, copy).effective_to, _row(db_session, copy).closed_for_party_id) == (starts, planned.id)

    _merge(db_session, engine, survivor, duplicate)

    row = _row(db_session, ended)
    assert (row.effective_to, row.closed_for_party_id) == (starts, planned.id)
    assert _row(db_session, planned).vehicle_id == survivor.id
    assert _row(db_session, planned).effective_to is None
    assert _current(db_session, survivor) == {ended.id}
    assert _row(db_session, copy).merged_into_vehicle_id == survivor.id


@pytest.mark.parametrize("on_survivor_too", [True, False])
def test_a_row_waiting_for_a_planned_holder_kept_as_a_copy_waits_for_the_survivors_row_of_it(
    db_session, engine, on_survivor_too
):
    """The same customer is the planned holder on both cars from the same
    start, so the duplicate's planned row stays there as a kept copy. A row
    linked to that copy — the survivor's reopened row, or a row that simply
    moved — is linked to the survivor's planned row instead: cancelling the
    planned holder gives the role back, and bringing its start forward to now
    leaves one current holder (KAN-644, KAN-664)."""

    group_id = uuid.uuid4()
    starts = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=30)
    survivor, duplicate = _cars(db_session)
    holder, successor = _customer(db_session, group_id), _customer(db_session, group_id)
    if on_survivor_too:
        mine = _holder(db_session, survivor, group_id, customer=holder, effective_from=Y2020, effective_to=Y2022)
    planned = _holder(db_session, survivor, group_id, customer=successor, effective_from=starts)
    theirs = _holder(db_session, duplicate, group_id, customer=holder, effective_from=Y2020)
    planned_copy = _holder(db_session, duplicate, group_id, customer=successor, effective_from=starts)
    assert _row(db_session, theirs).closed_for_party_id == planned_copy.id

    _merge(db_session, engine, survivor, duplicate)

    waiting = _row(db_session, mine if on_survivor_too else theirs)
    assert (waiting.vehicle_id, waiting.effective_to, waiting.closed_for_party_id) == (survivor.id, starts, planned.id)
    assert _row(db_session, planned_copy).merged_into_vehicle_id == survivor.id
    assert _current(db_session, survivor) == {waiting.id}

    # Cancelling the planned holder («planned, never started») gives the role back.
    delete_customer_vehicle(db_session, party=_row(db_session, planned), actor_id=uuid.uuid4(), group_id=group_id)
    db_session.commit()
    assert _row(db_session, waiting).effective_to is None
    assert _current(db_session, survivor) == {waiting.id}


def test_the_row_that_took_the_later_end_does_not_hold_beside_another_holder_the_duplicate_brought(db_session, engine):
    """The duplicate carried two open holders of the role (written past the
    close rule). The survivor's reopened row is both cars' at once, so the
    role is settled: the later start stays, one current holder."""

    group_id = uuid.uuid4()
    survivor, duplicate = _cars(db_session)
    customer = _customer(db_session, group_id)
    ended = _holder(db_session, survivor, group_id, customer=customer, effective_from=Y2020, effective_to=Y2022)
    _holder(db_session, duplicate, group_id, customer=customer, effective_from=Y2020)
    other = _raw(db_session, duplicate, group_id, effective_from=Y2023)

    _merge(db_session, engine, survivor, duplicate)

    assert _current(db_session, survivor) == {other.id}
    assert _row(db_session, ended).effective_to == Y2023


def test_a_copy_ending_at_a_date_to_come_gives_the_survivors_row_that_end(db_session, engine):
    group_id = uuid.uuid4()
    ends = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=30)
    survivor, duplicate, ended, copy = _same_customer_on_both(
        db_session, group_id, survivor_ends=Y2022, duplicate_ends=ends
    )
    linked_before = len(_linked(db_session, ended))

    _merge(db_session, engine, survivor, duplicate)

    assert _row(db_session, ended).effective_to == ends
    assert _current(db_session, survivor) == {ended.id}
    assert len(_linked(db_session, ended)) == linked_before + 1
    assert _row(db_session, copy).effective_to == Y2020  # the copy holds nothing


@pytest.mark.parametrize("duplicate_ends", [Y2022, Y2023])
def test_a_copy_that_ended_no_later_than_the_survivors_row_changes_nothing_on_it(db_session, engine, duplicate_ends):
    group_id = uuid.uuid4()
    survivor, duplicate, ended, copy = _same_customer_on_both(
        db_session, group_id, survivor_ends=Y2023, duplicate_ends=duplicate_ends
    )
    linked_before = len(_linked(db_session, ended))

    _merge(db_session, engine, survivor, duplicate)

    assert _row(db_session, ended).effective_to == Y2023
    assert _period_audits(db_session, ended) == []
    assert len(_linked(db_session, ended)) == linked_before
    assert _row(db_session, copy).merged_into_vehicle_id == survivor.id


def test_a_later_holder_on_the_survivor_still_ends_the_row_that_took_the_later_end(db_session, engine):
    """The survivor's row ended because a successor took the role there. The
    duplicate says the customer is still the holder; option C says the later
    start stays — so the row is opened and closed again at the successor's
    start, and the role has one current holder: the successor."""

    group_id = uuid.uuid4()
    survivor, duplicate, ended, copy = _same_customer_on_both(
        db_session, group_id, survivor_ends=Y2022, duplicate_ends=None
    )
    successor = _holder(db_session, survivor, group_id, effective_from=Y2023)
    assert _row(db_session, ended).effective_to == Y2022
    linked_before = len(_linked(db_session, ended))

    _merge(db_session, engine, survivor, duplicate)

    assert _current(db_session, survivor) == {successor.id}
    assert _row(db_session, successor).effective_to is None
    assert _merge_audits(db_session, successor) == []
    row = _row(db_session, ended)
    assert (row.effective_to, row.closed_for_party_id) == (Y2023, None)
    [opened] = _period_audits(db_session, ended)
    assert opened.after == {"effectiveFrom": Y2020.isoformat(), "effectiveTo": None}
    closes = [
        audit.after
        for audit in _merge_audits(db_session, ended)
        if audit.action == "vehicle_party_remove" and "mergedIntoVehicleId" not in audit.after
    ]
    assert closes == [{"effectiveTo": Y2023.isoformat()}]
    assert len(_linked(db_session, ended)) == linked_before + 1
    [unlinked] = _unlinked(db_session, ended)
    assert unlinked.payload["effectiveTo"] == Y2023.isoformat()
    assert _row(db_session, copy).merged_into_vehicle_id == survivor.id


def test_a_planned_successor_on_the_survivor_keeps_its_link_to_the_row_that_took_the_later_end(db_session, engine):
    group_id = uuid.uuid4()
    starts = dt.datetime.now(dt.UTC).replace(microsecond=0) + dt.timedelta(days=30)
    survivor, duplicate = _cars(db_session)
    customer = _customer(db_session, group_id)
    held = _holder(db_session, survivor, group_id, customer=customer, effective_from=Y2020)
    planned = _holder(db_session, survivor, group_id, effective_from=starts)
    copy = _holder(db_session, duplicate, group_id, customer=customer, effective_from=Y2020)
    assert (_row(db_session, held).effective_to, _row(db_session, held).closed_for_party_id) == (starts, planned.id)
    linked_before = len(_linked(db_session, held))

    _merge(db_session, engine, survivor, duplicate)

    row = _row(db_session, held)
    assert (row.effective_to, row.closed_for_party_id) == (starts, planned.id)
    assert _current(db_session, survivor) == {held.id}
    assert _row(db_session, planned).effective_to is None
    # It held its role throughout: nothing to announce.
    assert len(_linked(db_session, held)) == linked_before
    assert _unlinked(db_session, held) == []
    assert _row(db_session, copy).merged_into_vehicle_id == survivor.id


def test_a_redelivered_merge_gives_the_later_end_once(db_session, engine):
    group_id = uuid.uuid4()
    survivor, duplicate, ended, copy = _same_customer_on_both(
        db_session, group_id, survivor_ends=Y2022, duplicate_ends=None
    )
    linked_before = len(_linked(db_session, ended))
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    message = _merged_message(db_session, duplicate.id)

    _deliver_merged(engine, message)
    _deliver_merged(engine, message)
    # And with the processed-events row gone, the move itself repeats nothing.
    db_session.query(ProcessedEvent).filter(ProcessedEvent.event_id == message.id).delete()
    db_session.commit()
    _deliver_merged(engine, message)

    db_session.expire_all()
    assert _row(db_session, ended).effective_to is None
    assert len(_period_audits(db_session, ended)) == 1
    assert len(_linked(db_session, ended)) == linked_before + 1
    assert len(_unlinked(db_session, copy)) == 1
    assert len(_merge_audits(db_session, ended)) == 2  # the later end, and the copy's close


def test_a_kept_copy_follows_its_survivor_into_a_later_merge(db_session, engine):
    a, b, c = _cars(db_session, 3)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    _holder(db_session, b, group_id, customer=customer, effective_from=Y2020)
    copy = _holder(db_session, a, group_id, customer=customer, effective_from=Y2020)

    _merge(db_session, engine, b, a)
    _merge(db_session, engine, c, b)

    kept = _row(db_session, copy)
    assert (kept.vehicle_id, kept.merged_into_vehicle_id) == (a.id, c.id)


def test_a_redelivered_merge_closes_and_announces_nothing_twice(db_session, engine):
    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    older = _holder(db_session, survivor, group_id, effective_from=Y2020)
    _holder(db_session, duplicate, group_id, effective_from=Y2022)
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    message = _merged_message(db_session, duplicate.id)

    _deliver_merged(engine, message)
    _deliver_merged(engine, message)
    # And with the processed-events row gone, the move itself repeats nothing.
    db_session.query(ProcessedEvent).filter(ProcessedEvent.event_id == message.id).delete()
    db_session.commit()
    _deliver_merged(engine, message)

    db_session.expire_all()
    assert _row(db_session, older).effective_to == Y2022
    assert len(_unlinked(db_session, older)) == 1
    assert len(_merge_audits(db_session, older)) == 1


@NEEDS_ITS_OWN_CONNECTION
@pytest.mark.parametrize("locked", ["survivor", "duplicate"])
def test_the_move_waits_for_the_role_lock_an_allocation_holds(db_session, engine, locked):
    """The exit criterion's lock: the settle runs under the (vehicle, role,
    group) advisory lock every allocation, reopen and customer merge takes —
    of both cars, so a writer of either is never interleaved with."""

    survivor, duplicate = _cars(db_session)
    group_id = uuid.uuid4()
    older = _holder(db_session, survivor, group_id, effective_from=Y2020)
    _holder(db_session, duplicate, group_id, effective_from=Y2022)
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    db_session.commit()
    vehicle_id = survivor.id if locked == "survivor" else duplicate.id
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with factory() as allocation, factory() as consumer:
        _lock_vehicle_role_in_group(allocation, vehicle_id=vehicle_id, role=OWNER, group_id=group_id)
        consumer.execute(text("SET LOCAL lock_timeout = '300ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            repoint_vehicle_parties_on_vehicle_merge(
                consumer, duplicate_vehicle_id=duplicate.id, survivor_vehicle_id=survivor.id
            )
        consumer.rollback()
        allocation.rollback()
        # Released, the same move goes through.
        assert (
            repoint_vehicle_parties_on_vehicle_merge(
                consumer, duplicate_vehicle_id=duplicate.id, survivor_vehicle_id=survivor.id
            )
            == 1
        )
        consumer.commit()
    db_session.expire_all()
    assert _row(db_session, older).effective_to == Y2022
