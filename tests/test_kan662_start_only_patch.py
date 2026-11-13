"""KAN-662: a PATCH that changes only a party row's `effectiveFrom` is a
holder's period changing, as a reopen is. It takes the same (vehicle, role,
group) lock, KAN-583's refusal to overlap a future holder and KAN-148/KAN-644's
close rule — so the group never shows two holders at once.

The linked case (a planned holder's start moving the end of the holder it
replaces, `closed_for_party_id`) is KAN-644's, in test_kan644_kan583_handover.py.
These are the rows without a link: an end the user typed.
"""

import datetime as dt
import uuid

import pytest

from app.core.base import utcnow
from app.core.errors import ConflictError
from tests.test_customer_vehicle_party_future_holder import (  # noqa: F401 — _never_inverted is an autouse fixture
    _allocate,
    _customer,
    _never_inverted,
    _vehicle,
)
from tests.test_kan644_kan583_handover import _current_and_upcoming, _handover, _patch, _row


def _typed_handover(db, group_id, vehicle, *, days: int = 20):
    """Clara owns «bis T+days», typed by hand; Meier is recorded «ab T+days».
    The dates meet, so Meier's allocation closed nobody and linked nobody."""

    clara, meier = _customer(db, group_id, "Clara"), _customer(db, group_id, "Anna", "Meier")
    handover = _handover(days)
    clara_party = _allocate(db, vehicle.id, clara, group_id)
    _patch(db, clara_party, group_id, effective_to=handover)
    meier_party = _allocate(db, vehicle.id, meier, group_id, effective_from=handover)
    assert _row(db, clara_party.id).closed_for_party_id is None
    return clara, clara_party, meier, meier_party, handover


def test_moving_a_start_earlier_closes_the_current_holder_at_the_new_start(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW662001")
    clara, clara_party, meier, meier_party, _ = _typed_handover(db_session, group_id, vehicle)
    earlier = _handover(10)

    _patch(db_session, meier_party, group_id, effective_from=earlier)

    clara_row = _row(db_session, clara_party.id)
    assert clara_row.effective_to == earlier  # was T+20: ten days of two owners
    assert clara_row.closed_for_party_id == meier_party.id
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({clara.id}, {meier.id})


def test_moving_a_start_into_the_past_leaves_one_current_holder(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW662002")
    _clara, clara_party, meier, meier_party, _ = _typed_handover(db_session, group_id, vehicle)
    yesterday = (utcnow() - dt.timedelta(days=1)).replace(microsecond=0)

    _patch(db_session, meier_party, group_id, effective_from=yesterday)

    clara_row = _row(db_session, clara_party.id)
    assert clara_row.effective_to is not None and clara_row.effective_to <= utcnow()
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({meier.id}, set())


def test_moving_a_start_later_closes_nobody_and_lengthens_nobody(db_session):
    """The other direction: a later start shrinks the period, so it can
    overlap no one. Clara's typed end stays where she typed it (the close rule
    never lengthens a row, KAN-644), and nothing is refused."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW662003")
    clara, clara_party, meier, meier_party, handover = _typed_handover(db_session, group_id, vehicle)

    later = _handover(25)

    _patch(db_session, meier_party, group_id, effective_from=later)

    assert _row(db_session, clara_party.id).effective_to == handover
    assert _row(db_session, meier_party.id).effective_from == later
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({clara.id}, {meier.id})


def test_moving_a_start_earlier_into_a_future_holders_period_is_refused_and_changes_nothing(db_session):
    """The review's reproduction (PR #313): P is recorded for [T+10, T+15),
    Q from T+20. Moving Q's start to T+12 made two upcoming holders overlap
    over [T+12, T+15); it is now refused like a create."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW662004")
    p, q = _customer(db_session, group_id, "Paul"), _customer(db_session, group_id, "Quirin")
    p_ends, q_starts = _handover(15), _handover(20)
    p_party = _allocate(db_session, vehicle.id, p, group_id, effective_from=_handover(10), effective_to=p_ends)
    q_party = _allocate(db_session, vehicle.id, q, group_id, effective_from=q_starts)

    with pytest.raises(ConflictError) as raised:
        _patch(db_session, q_party, group_id, effective_from=_handover(12))
    db_session.rollback()

    assert raised.value.details["reason"] == "vehicle_party_future_holder"
    assert raised.value.details["vehiclePartyId"] == str(p_party.id)
    assert _row(db_session, q_party.id).effective_from == q_starts
    assert _row(db_session, p_party.id).effective_to == p_ends


def test_moving_the_start_of_a_history_row_closes_nobody(db_session):
    """A row that no longer holds its role is history: a start-only PATCH of
    it runs no close rule (as before)."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW662005")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id, effective_from=utcnow() - dt.timedelta(days=60))
    meier_party = _allocate(db_session, vehicle.id, meier, group_id)  # closes Clara now

    _patch(db_session, clara_party, group_id, effective_from=utcnow() - dt.timedelta(days=90))

    assert _row(db_session, meier_party.id).effective_to is None
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({meier.id}, set())
