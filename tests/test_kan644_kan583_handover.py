"""KAN-644 + KAN-583 (Anto, 2026-11-11 option A; 2026-11-12: KAN-644 A, KAN-583 1A + 2A).

- KAN-644: a holder recorded from a later date closes the current holder at
  that date, not today: the owner history has no gap, and until the handover
  the current holder is the one current owner (the new one is upcoming).
- KAN-583 1A: «Eintrag beenden» on a planned holder cancels it — the row is
  kept as «planned, never started» (closed at its own start, an empty period)
  and no longer reads as current or upcoming. The current holder that the
  planned handover would have ended gets the role back.
- KAN-583's refusal (an overlap with a future holder) is in
  test_customer_vehicle_party_future_holder.py; the no-inverted-row check runs
  after every test here too.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.customer.models.vehicle_party import VehicleParty
from app.customer.schemas.customer import CustomerVehicleUpdate
from app.customer.services.vehicle_party import delete_customer_vehicle, update_customer_vehicle
from app.customer.services.vehicle_party_reads import get_customer_vehicle_or_404, list_vehicle_parties
from tests.test_customer_vehicle_party_future_holder import (  # noqa: F401 — _never_inverted is an autouse fixture
    _allocate,
    _customer,
    _never_inverted,
    _vehicle,
)


def _sales_headers(group_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group_id, roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def _handover(days: int = 19) -> dt.datetime:
    return (utcnow() + dt.timedelta(days=days)).replace(microsecond=0)


def _row(db, party_id) -> VehicleParty:
    db.expire_all()
    return db.scalars(select(VehicleParty).where(VehicleParty.id == party_id)).one()


def _current_and_upcoming(db, vehicle_id, group_id) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    now = utcnow()
    rows = list_vehicle_parties(db, vehicle_id=vehicle_id, group_id=group_id)
    return {r.customer_id for r in rows if r.effective_from <= now}, {r.customer_id for r in rows if r.effective_from > now}


def test_a_future_owner_closes_the_current_owner_at_its_start_and_is_upcoming_until_then(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644001")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    handover = _handover()

    _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)

    assert _row(db_session, clara_party.id).effective_to == handover  # no gap, still current today
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({clara.id}, {meier.id})


def test_api_create_from_the_customer_with_a_future_start_keeps_the_current_owner_until_then(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644002")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    db_session.commit()
    handover = _handover()

    response = client.post(
        f"/v1/customers/{meier.id}/vehicles",
        json={"vehicleId": str(vehicle.id), "role": "owner", "effectiveFrom": handover.isoformat()},
        headers=_sales_headers(group_id),
    )

    assert response.status_code == 201, response.text
    assert _row(db_session, clara_party.id).effective_to == handover


def test_a_reopen_with_a_future_start_closes_the_current_owner_at_that_start(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644003")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    meier_party = _allocate(db_session, vehicle.id, meier, group_id)
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)  # closes Meier now
    handover = _handover()

    loaded = get_customer_vehicle_or_404(db_session, customer_id=meier.id, party_id=meier_party.id)
    update_customer_vehicle(
        db_session, party=loaded, data=CustomerVehicleUpdate(effective_from=handover, effective_to=None),
        actor_id=uuid.uuid4(), group_id=group_id,
    )

    assert _row(db_session, clara_party.id).effective_to == handover


def test_a_current_owner_ending_before_the_future_start_is_not_lengthened(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644004")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_until = _handover(5)
    clara_party = _allocate(db_session, vehicle.id, clara, group_id, effective_to=clara_until)

    _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())

    assert _row(db_session, clara_party.id).effective_to == clara_until


def test_a_new_owner_starting_now_still_closes_the_current_owner_now(client, db_session):
    """KAN-148 unchanged; the vehicle-side /allocate takes no start date."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644005")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    db_session.commit()
    before = utcnow()

    response = client.post(
        f"/v1/vehicle-mdm/{vehicle.id}/allocate",
        json={"customerId": str(meier.id), "role": "owner"},
        headers=_sales_headers(group_id),
    )

    assert response.status_code == 201, response.text
    closed = _row(db_session, clara_party.id).effective_to
    assert closed is not None and before <= closed <= utcnow()


def test_ending_a_planned_entry_cancels_it_and_gives_the_current_owner_the_role_back(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583101")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    handover = _handover()
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)
    db_session.commit()

    response = client.delete(f"/v1/customers/{meier.id}/vehicles/{meier_party.id}", headers=_sales_headers(group_id))

    assert response.status_code == 204, response.text
    cancelled = _row(db_session, meier_party.id)
    assert cancelled.effective_to == cancelled.effective_from == handover  # planned, never started
    assert _row(db_session, clara_party.id).effective_to is None  # the sale fell through: Clara keeps the car
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({clara.id}, set())
    listed = client.get(f"/v1/customers/{meier.id}/vehicles", headers=_sales_headers(group_id)).json()["items"]
    assert listed == []
    reasons = set(db_session.scalars(select(AuditEvent.reason).where(AuditEvent.entity_type == "customer")).all())
    assert {"planned_never_started", "planned_handover_cancelled"} <= reasons


def test_ending_a_planned_entry_twice_changes_nothing_the_second_time(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583102")
    meier = _customer(db_session, group_id, "Anna", "Meier")
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())

    for _ in range(2):
        loaded = get_customer_vehicle_or_404(db_session, customer_id=meier.id, party_id=meier_party.id)
        delete_customer_vehicle(db_session, party=loaded, actor_id=uuid.uuid4(), group_id=group_id)

    removals = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == meier.id, AuditEvent.action == "vehicle_party_remove")
    ).all()
    assert len(removals) == 1


def test_a_cancelled_planned_entry_is_not_in_the_way_of_a_new_owner(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583103")
    meier, huber = _customer(db_session, group_id, "Anna", "Meier"), _customer(db_session, group_id, "Hans", "Huber")
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    loaded = get_customer_vehicle_or_404(db_session, customer_id=meier.id, party_id=meier_party.id)
    delete_customer_vehicle(db_session, party=loaded, actor_id=uuid.uuid4(), group_id=group_id)

    huber_party = _allocate(db_session, vehicle.id, huber, group_id)  # no future-holder refusal

    assert _row(db_session, huber_party.id).effective_to is None
    assert _row(db_session, meier_party.id).effective_to == _row(db_session, meier_party.id).effective_from


def test_ending_a_current_entry_still_ends_it_now(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583104")
    clara = _customer(db_session, group_id, "Clara")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    before = utcnow()

    loaded = get_customer_vehicle_or_404(db_session, customer_id=clara.id, party_id=clara_party.id)
    delete_customer_vehicle(db_session, party=loaded, actor_id=uuid.uuid4(), group_id=group_id)

    ended = _row(db_session, clara_party.id).effective_to
    assert ended is not None and before <= ended <= utcnow()


def test_the_vehicles_tab_names_another_party_from_a_later_date_with_its_start(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644006")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    _allocate(db_session, vehicle.id, clara, group_id)
    handover = _handover()
    _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)
    db_session.commit()

    items = client.get(f"/v1/customers/{clara.id}/vehicles", headers=_sales_headers(group_id)).json()["items"]

    [others] = [item["otherParties"] for item in items]
    assert [(o["customerId"], dt.datetime.fromisoformat(o["effectiveFrom"])) for o in others] == [(str(meier.id), handover)]


# --- the independent review's findings (2026-11-12) ----------------------------------------


def _events(db, event_type: str, aggregate_id) -> list:
    from app.core.outbox_model import OutboxMessage

    db.expire_all()
    return list(
        db.scalars(
            select(OutboxMessage).where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
        ).all()
    )


def _patch(db, party, group_id, **fields):
    loaded = get_customer_vehicle_or_404(db, customer_id=party.customer_id, party_id=party.id)
    return update_customer_vehicle(
        db, party=loaded, data=CustomerVehicleUpdate(**fields), actor_id=uuid.uuid4(), group_id=group_id,
    )


def _end(db, party, group_id):
    loaded = get_customer_vehicle_or_404(db, customer_id=party.customer_id, party_id=party.id)
    delete_customer_vehicle(db, party=loaded, actor_id=uuid.uuid4(), group_id=group_id)


def test_cancelling_a_planned_entry_leaves_an_end_the_user_typed_alone(db_session):
    """Clara «bis 1.12.» typed by hand, Meier «ab 1.12.»: the dates meet, but
    Meier's handover closed nobody, so cancelling it gives nobody a role back."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583201")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    handover = _handover()
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    _patch(db_session, clara_party, group_id, effective_to=handover)
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)

    _end(db_session, meier_party, group_id)

    assert _row(db_session, clara_party.id).effective_to == handover


def test_disconnect_decides_on_the_locked_row_when_its_start_moved_meanwhile(engine, db_session):
    """The row was read as current; another session moved its start into the
    future before the Disconnect locked it. It is cancelled at its new start,
    never closed at now (an end before its start)."""

    from sqlalchemy.orm import sessionmaker

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583202")
    meier = _customer(db_session, group_id, "Anna", "Meier")
    party = _allocate(db_session, vehicle.id, meier, group_id)
    stale = get_customer_vehicle_or_404(db_session, customer_id=meier.id, party_id=party.id)
    later = _handover()
    with sessionmaker(bind=engine)() as other:
        other.execute(
            VehicleParty.__table__.update().where(VehicleParty.id == party.id).values(effective_from=later)
        )
        other.commit()

    delete_customer_vehicle(db_session, party=stale, actor_id=uuid.uuid4(), group_id=group_id)

    row = _row(db_session, party.id)
    assert row.effective_from == row.effective_to == later


def test_a_close_at_a_future_start_publishes_nothing_and_the_cancel_publishes_only_the_planned_rows_unlinked(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583203")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())

    assert _events(db_session, "customer.vehicle_party.unlinked", clara_party.id) == []  # Clara still holds (KAN-352)

    _end(db_session, meier_party, group_id)

    assert _events(db_session, "customer.vehicle_party.unlinked", clara_party.id) == []
    assert len(_events(db_session, "customer.vehicle_party.unlinked", meier_party.id)) == 1


def test_reopening_a_cancelled_planned_entry_publishes_linked_and_closes_the_current_owner_at_its_start(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583204")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    handover = _handover()
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)
    _end(db_session, meier_party, group_id)

    _patch(db_session, meier_party, group_id, effective_to=None)

    assert len(_events(db_session, "customer.vehicle_party.linked", meier_party.id)) == 2
    assert _row(db_session, clara_party.id).effective_to == handover
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({clara.id}, {meier.id})


def test_moving_a_planned_start_later_moves_the_current_owners_end_with_it(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583205")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    later = _handover(40)

    _patch(db_session, meier_party, group_id, effective_from=later)

    assert _row(db_session, clara_party.id).effective_to == later  # no gap


def test_moving_a_planned_start_into_the_past_ends_the_current_owner_there(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583206")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id, effective_from=utcnow() - dt.timedelta(days=30))
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    earlier = (utcnow() - dt.timedelta(hours=1)).replace(microsecond=0)

    _patch(db_session, meier_party, group_id, effective_from=earlier)

    assert _row(db_session, clara_party.id).effective_to == earlier
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({meier.id}, set())  # one current owner
    assert len(_events(db_session, "customer.vehicle_party.unlinked", clara_party.id)) == 1


def test_a_patch_moving_the_planned_start_later_with_an_end_moves_the_current_owners_end_too(db_session):
    """The review's probe: {effectiveFrom: later, effectiveTo: …} in one PATCH
    used to spare the linked owner at its old end — a gap of weeks."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583207")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    later = _handover(40)

    for effective_to in (None, _handover(80)):
        _patch(db_session, meier_party, group_id, effective_from=later, effective_to=effective_to)
        assert _row(db_session, clara_party.id).effective_to == later
        assert _row(db_session, clara_party.id).closed_for_party_id == meier_party.id


# --- the third review's findings (2026-11-12) ----------------------------------------------


def test_a_start_only_patch_on_a_cancelled_entry_is_refused_and_changes_nothing(db_session):
    from app.core.errors import BadRequestError

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583301")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    handover = _handover()
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)
    _end(db_session, meier_party, group_id)

    try:
        _patch(db_session, meier_party, group_id, effective_from=utcnow() - dt.timedelta(hours=1))
    except BadRequestError:
        db_session.rollback()
    else:
        raise AssertionError("a start-only PATCH revived a cancelled entry")

    assert _row(db_session, meier_party.id).effective_to == _row(db_session, meier_party.id).effective_from == handover
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({clara.id}, set())
    assert _row(db_session, clara_party.id).effective_to is None


def test_moving_the_start_of_a_holder_that_already_began_leaves_the_former_holder_alone(db_session):
    """Its handover happened: the holder it replaced is history, never given
    its role back by a later change of the new holder's start."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583302")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id, effective_from=utcnow() - dt.timedelta(days=30))
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    started = (utcnow() - dt.timedelta(days=1)).replace(microsecond=0)
    _patch(db_session, meier_party, group_id, effective_from=started)  # the handover happened yesterday
    clara_end = _row(db_session, clara_party.id).effective_to

    _patch(db_session, meier_party, group_id, effective_from=_handover(10))

    assert _row(db_session, clara_party.id).effective_to == clara_end == started


@pytest.mark.parametrize("action", ["cancel", "move_start"])
def test_a_cancel_or_a_moved_start_never_deadlocks_against_the_label_refresh(engine, db_session, action):
    """The review's reproduction, made deterministic: the label refresh
    (KAN-186) locks a vehicle's party rows in id order — here it holds the
    older row (Clara's) and then reaches for the newer one (Meier's). A cancel
    or a moved planned start used to lock Meier's row first and then wait for
    Clara's: a deadlock. It now locks the role's rows in id order, so it waits
    for Clara's row and the refresh goes through."""

    import threading

    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from tests.test_customer_vehicle_party_close_rule import _wait_until_a_backend_waits_on_a_lock

    if engine.dialect.name != "postgresql":
        pytest.skip("row locks are Postgres-only; the SQLite lane serialises writers itself")
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, f"WVWZZZ1KZAW58330{3 if action == 'cancel' else 4}")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    db_session.commit()
    assert clara_party.id < meier_party.id
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _write():
        with factory() as s:
            try:
                if action == "cancel":
                    _end(s, meier_party, group_id)
                else:
                    _patch(s, meier_party, group_id, effective_from=_handover(40))
            except Exception as exc:  # noqa: BLE001 — collected and asserted empty
                errors.append(exc)

    lock_row = "SELECT 1 FROM vehicle_party WHERE id = :id FOR NO KEY UPDATE"
    with factory() as refresh:
        refresh.execute(text("SET LOCAL lock_timeout = '10s'"))
        refresh.execute(text(lock_row), {"id": clara_party.id})  # the refresh holds the older row
        writer = threading.Thread(target=_write)
        writer.start()
        try:
            _wait_until_a_backend_waits_on_a_lock(engine)  # the writer waits on Clara's row
            refresh.execute(text(lock_row), {"id": meier_party.id})  # …and the refresh reaches for Meier's
            refresh.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            refresh.rollback()
        writer.join(timeout=30)

    assert not writer.is_alive()
    assert errors == []


# --- the fourth review's findings (2026-11-12) ---------------------------------------------


def _let_the_handover_pass(engine, clara_party, meier_party) -> dt.datetime:
    """Real time passing: Clara's end and Meier's start move to yesterday,
    the state the clock leaves behind (the link stays on Clara's row)."""

    from sqlalchemy.orm import sessionmaker

    yesterday = (utcnow() - dt.timedelta(days=1)).replace(microsecond=0)
    with sessionmaker(bind=engine)() as s:
        table = VehicleParty.__table__
        s.execute(table.update().where(table.c.id == clara_party.id).values(effective_to=yesterday))
        s.execute(table.update().where(table.c.id == meier_party.id).values(effective_from=yesterday))
        s.commit()
    return yesterday


def test_after_the_handover_a_re_planned_and_cancelled_entry_gives_no_role_back_to_the_former_owner(engine, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583401")
    clara, meier, weber = (
        _customer(db_session, group_id, "Clara"),
        _customer(db_session, group_id, "Anna", "Meier"),
        _customer(db_session, group_id, "Hans", "Weber"),
    )
    clara_party = _allocate(db_session, vehicle.id, clara, group_id, effective_from=utcnow() - dt.timedelta(days=60))
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    db_session.commit()
    handed_over = _let_the_handover_pass(engine, clara_party, meier_party)
    db_session.expire_all()
    weber_party = _allocate(db_session, vehicle.id, weber, group_id)  # closes Meier now
    _patch(db_session, meier_party, group_id, effective_from=_handover(30), effective_to=None)  # Weber linked

    _end(db_session, meier_party, group_id)

    assert _row(db_session, clara_party.id).effective_to == handed_over  # history stays history
    assert _row(db_session, weber_party.id).effective_to is None  # Weber gets his role back
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({weber.id}, set())


def test_after_the_handover_moving_the_start_twice_never_moves_the_former_owners_end(engine, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583402")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Anna", "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id, effective_from=utcnow() - dt.timedelta(days=60))
    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=_handover())
    db_session.commit()
    handed_over = _let_the_handover_pass(engine, clara_party, meier_party)
    db_session.expire_all()

    _patch(db_session, meier_party, group_id, effective_from=_handover(10))
    _patch(db_session, meier_party, group_id, effective_from=_handover(20))

    assert _row(db_session, clara_party.id).effective_to == handed_over


def test_the_nightly_label_refresh_takes_a_batchs_rows_in_id_order_before_it_writes(engine, db_session):
    """It waits on the older row before it touches the newer one, as every
    multi-row writer of a vehicle's rows does (so none can deadlock it)."""

    import threading

    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from app.customer.services.vehicle_party_labels import refresh_vehicle_party_labels
    from tests.test_customer_vehicle_party_close_rule import _wait_until_a_backend_waits_on_a_lock

    if engine.dialect.name != "postgresql":
        pytest.skip("row locks are Postgres-only")
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583403")
    older = _allocate(db_session, vehicle.id, _customer(db_session, group_id, "Clara"), group_id)
    newer = _allocate(db_session, vehicle.id, _customer(db_session, group_id, "Anna", "Meier"), group_id)
    db_session.commit()
    assert older.id < newer.id
    # KAN-664: the newer row's label is stale, so the refresh writes it through
    # the ORM before it stamps the current older one — the order in which the
    # scan-order refresh used to hold the newer row while it waited for the
    # older. Without a stale row the old refresh also happened to pass.
    db_session.execute(
        VehicleParty.__table__.update().where(VehicleParty.id == newer.id).values(vehicle_vin="STALE000000000000")
    )
    db_session.commit()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _nightly():
        with factory() as s:
            try:
                refresh_vehicle_party_labels(s)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    with factory() as holder:
        holder.execute(text("SELECT 1 FROM vehicle_party WHERE id = :id FOR UPDATE"), {"id": older.id})
        nightly = threading.Thread(target=_nightly)
        nightly.start()
        _wait_until_a_backend_waits_on_a_lock(engine)
        with factory() as probe:  # the newer row is still free: the refresh waits before it
            probe.execute(text("SET LOCAL lock_timeout = '1s'"))
            probe.execute(text("SELECT 1 FROM vehicle_party WHERE id = :id FOR UPDATE NOWAIT"), {"id": newer.id})
            probe.rollback()
        holder.commit()
        nightly.join(timeout=30)
    assert not nightly.is_alive()
    assert errors == []



# --- KAN-664: a restored owner holds only until the next holder to come ----------------------


def test_cancelling_a_planned_entry_restores_the_owner_only_until_a_later_planned_holder(db_session):
    """Route 1: A owns; P is planned for [T+10, T+15) and closes A at T+10;
    Q is planned from T+20 (A is spared: it already ends first). Cancelling
    P gives A the role back until T+20, linked to Q — never open beside Q."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW664001")
    a, p, q = (
        _customer(db_session, group_id, "Anna"),
        _customer(db_session, group_id, "Paul"),
        _customer(db_session, group_id, "Quirin"),
    )
    a_party = _allocate(db_session, vehicle.id, a, group_id)
    p_party = _allocate(db_session, vehicle.id, p, group_id, effective_from=_handover(10), effective_to=_handover(15))
    q_party = _allocate(db_session, vehicle.id, q, group_id, effective_from=_handover(20))

    _end(db_session, p_party, group_id)

    restored = _row(db_session, a_party.id)
    assert restored.effective_to == _row(db_session, q_party.id).effective_from
    assert restored.closed_for_party_id == q_party.id
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({a.id}, {q.id})


def test_cancelling_an_interim_entry_restores_the_owner_until_the_planned_holder_after_it(db_session):
    """Route 2: P is planned from T+10 and closes A there; B is recorded for
    [T+3, T+5) and moves A's end (and link) to T+3. Cancelling B gives A the
    role back until P's start, linked to P again."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW664002")
    a, p, b = (
        _customer(db_session, group_id, "Anna"),
        _customer(db_session, group_id, "Paul"),
        _customer(db_session, group_id, "Berta"),
    )
    a_party = _allocate(db_session, vehicle.id, a, group_id)
    p_party = _allocate(db_session, vehicle.id, p, group_id, effective_from=_handover(10))
    b_party = _allocate(db_session, vehicle.id, b, group_id, effective_from=_handover(3), effective_to=_handover(5))

    _end(db_session, b_party, group_id)

    restored = _row(db_session, a_party.id)
    assert restored.effective_to == _row(db_session, p_party.id).effective_from
    assert restored.closed_for_party_id == p_party.id
    assert _current_and_upcoming(db_session, vehicle.id, group_id) == ({a.id}, {p.id})

    _end(db_session, p_party, group_id)  # and cancelling P too leaves A the owner, open-ended

    assert _row(db_session, a_party.id).effective_to is None
