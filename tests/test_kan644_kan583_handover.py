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

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.customer.models.vehicle_party import VehicleParty
from app.customer.schemas.customer import CustomerVehicleUpdate
from app.customer.services.customer import (
    delete_customer_vehicle,
    get_customer_vehicle_or_404,
    list_vehicle_parties,
    update_customer_vehicle,
)
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
