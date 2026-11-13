"""KAN-566 (Anto, 2026-11-11, option B): a bought trade-in's former-owner
history (KAN-189, `record_former_holder_on_purchase`) joins a row the
customer already has for the period, without a gap or an overlap.

Case 1: the customer's own row starts exactly at the contract's signature and
ended before the purchase (typed by hand, say) — that row is ended at the
purchase instead of standing for a shorter period.
Case 2: the customer's own row starts elsewhere and ended between the
signature and the purchase — the new row starts where it ended, so the two
periods meet instead of overlapping.
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.trade_in_holders import record_former_holder_on_purchase
from app.customer.services.vehicle_party import allocate_vehicle_party
from tests.test_customer_vehicle_party_future_holder import (  # noqa: F401 — _never_inverted is an autouse fixture
    _customer,
    _never_inverted,
    _vehicle,
)

ROLES = (VehiclePartyRole.OWNER, VehiclePartyRole.KEEPER)


def _history_row(db, vehicle_id, customer, group_id, role, *, start: dt.datetime, end: dt.datetime) -> VehicleParty:
    return allocate_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=customer.id, role=role, group_id=group_id, actor_id=uuid.uuid4(),
        effective_from=start, effective_to=end,
    )


def _rows_of(db, customer_id, role) -> list[VehicleParty]:
    db.expire_all()
    return list(
        db.scalars(
            select(VehicleParty)
            .where(VehicleParty.customer_id == customer_id, VehicleParty.role == role)
            .order_by(VehicleParty.effective_from)
        ).all()
    )


def _moments() -> tuple[dt.datetime, dt.datetime]:
    ended_at = (utcnow() - dt.timedelta(days=1)).replace(microsecond=0)
    return ended_at - dt.timedelta(days=10), ended_at


def test_a_row_starting_at_the_signature_and_ended_before_the_purchase_is_ended_at_the_purchase(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW566001")
    clara = _customer(db_session, group_id, "Clara")
    signed_at, ended_at = _moments()
    typed_end = signed_at + dt.timedelta(days=3)
    for role in ROLES:
        _history_row(db_session, vehicle.id, clara, group_id, role, start=signed_at, end=typed_end)
    db_session.commit()

    written = record_former_holder_on_purchase(
        db_session, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
        held_from=signed_at, ended_at=ended_at, actor_id=None,
    )
    db_session.commit()

    assert written == []
    for role in ROLES:
        rows = _rows_of(db_session, clara.id, role)
        assert [(row.effective_from, row.effective_to) for row in rows] == [(signed_at, ended_at)]
    extended = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == clara.id, AuditEvent.reason == "trade_in_purchased")
    ).all()
    assert len(extended) == 2
    assert all(event.before["effectiveTo"] == typed_end.isoformat() for event in extended)
    assert all(event.after == {"effectiveTo": ended_at.isoformat()} for event in extended)

    # A redelivery changes nothing more.
    record_former_holder_on_purchase(
        db_session, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
        held_from=signed_at, ended_at=ended_at, actor_id=None,
    )
    db_session.commit()
    for role in ROLES:
        assert [(r.effective_from, r.effective_to) for r in _rows_of(db_session, clara.id, role)] == [
            (signed_at, ended_at)
        ]


def test_a_row_ended_between_the_signature_and_the_purchase_is_continued_without_overlap(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW566002")
    clara = _customer(db_session, group_id, "Clara")
    signed_at, ended_at = _moments()
    typed_start = signed_at - dt.timedelta(days=200)
    typed_end = signed_at + dt.timedelta(days=4)
    for role in ROLES:
        _history_row(db_session, vehicle.id, clara, group_id, role, start=typed_start, end=typed_end)
    db_session.commit()

    written = record_former_holder_on_purchase(
        db_session, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
        held_from=signed_at, ended_at=ended_at, actor_id=None,
    )
    db_session.commit()

    assert len(written) == 2
    for role in ROLES:
        rows = _rows_of(db_session, clara.id, role)
        assert [(row.effective_from, row.effective_to) for row in rows] == [
            (typed_start, typed_end),
            (typed_end, ended_at),
        ]


def test_a_row_ended_before_the_signature_leaves_the_new_row_at_the_signature(db_session):
    """Unchanged by KAN-566: an older period that ended before the signature
    neither overlaps nor touches the new one."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW566003")
    clara = _customer(db_session, group_id, "Clara")
    signed_at, ended_at = _moments()
    old_start, old_end = signed_at - dt.timedelta(days=400), signed_at - dt.timedelta(days=100)
    _history_row(db_session, vehicle.id, clara, group_id, VehiclePartyRole.OWNER, start=old_start, end=old_end)
    db_session.commit()

    record_former_holder_on_purchase(
        db_session, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
        held_from=signed_at, ended_at=ended_at, actor_id=None,
    )
    db_session.commit()

    rows = _rows_of(db_session, clara.id, VehiclePartyRole.OWNER)
    assert [(row.effective_from, row.effective_to) for row in rows] == [(old_start, old_end), (signed_at, ended_at)]


def test_the_same_start_row_is_extended_only_up_to_the_customers_next_row(db_session):
    """Row A starts at the signature and ended early; the customer's own row B
    lies inside the period. A is extended to B's start, never over B, and the
    rest of the period continues after B — still no gap and no overlap."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW566004")
    clara = _customer(db_session, group_id, "Clara")
    signed_at, ended_at = _moments()
    b_start, b_end = signed_at + dt.timedelta(days=5), signed_at + dt.timedelta(days=7)
    _history_row(db_session, vehicle.id, clara, group_id, VehiclePartyRole.OWNER, start=signed_at, end=signed_at + dt.timedelta(days=3))
    _history_row(db_session, vehicle.id, clara, group_id, VehiclePartyRole.OWNER, start=b_start, end=b_end)
    db_session.commit()

    record_former_holder_on_purchase(
        db_session, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
        held_from=signed_at, ended_at=ended_at, actor_id=None,
    )
    db_session.commit()

    rows = _rows_of(db_session, clara.id, VehiclePartyRole.OWNER)
    assert [(row.effective_from, row.effective_to) for row in rows] == [
        (signed_at, b_start),
        (b_start, b_end),
        (b_end, ended_at),
    ]


def test_an_own_row_starting_inside_the_period_leaves_no_gap_before_it(db_session):
    """Found by the re-review: only row B, starting after the signature and
    ending before the purchase. The stretch before B and the one after it are
    both recorded; nothing overlaps B."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW566005")
    clara = _customer(db_session, group_id, "Clara")
    signed_at, ended_at = _moments()
    b_start, b_end = signed_at + dt.timedelta(days=5), signed_at + dt.timedelta(days=7)
    _history_row(db_session, vehicle.id, clara, group_id, VehiclePartyRole.KEEPER, start=b_start, end=b_end)
    db_session.commit()

    record_former_holder_on_purchase(
        db_session, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
        held_from=signed_at, ended_at=ended_at, actor_id=None,
    )
    db_session.commit()

    rows = _rows_of(db_session, clara.id, VehiclePartyRole.KEEPER)
    assert [(row.effective_from, row.effective_to) for row in rows] == [
        (signed_at, b_start),
        (b_start, b_end),
        (b_end, ended_at),
    ]
