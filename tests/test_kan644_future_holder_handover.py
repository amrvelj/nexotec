"""KAN-644 (Anto, 2026-11-11, option A): a holder recorded from a date still
to come does not end the current holder today. The current owner/keeper keeps
the role until the new one starts: their row is closed at the new holder's
`effectiveFrom`, so the owner history has no gap.

Paths: the customer-side create (`POST /v1/customers/{id}/vehicles`, which
reaches `allocate_vehicle_party`), the service call itself, and a reopen by
PATCH. The vehicle-side `POST /v1/vehicle-mdm/{id}/allocate` takes no start
date (its holder always starts now), so it keeps closing at «now» — pinned
below so a later start-date field on it inherits the rule through the same
helper.
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.schemas.customer import CustomerVehicleUpdate
from app.customer.services.customer import (
    allocate_vehicle_party,
    get_customer_vehicle_or_404,
    update_customer_vehicle,
)
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

OWNER = VehiclePartyRole.OWNER


def _customer(db, group_id: uuid.UUID, first_name: str) -> Customer:
    customer = Customer(
        group_id=group_id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name=first_name, last_name="Muster",
    )
    db.add(customer)
    db.flush()
    return customer


def _vehicle(db, vin: str):
    vehicle = create_vehicle_mdm(db, vin=vin, catalogue_variant_id=None)
    db.commit()
    return vehicle


def _allocate(db, vehicle_id, customer: Customer, group_id, *, effective_from=None, effective_to=None):
    return allocate_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=customer.id, role=OWNER, group_id=group_id, actor_id=uuid.uuid4(),
        effective_from=effective_from, effective_to=effective_to,
    )


def _row(db, party_id: uuid.UUID) -> VehicleParty:
    db.expire_all()
    return db.scalars(select(VehicleParty).where(VehicleParty.id == party_id)).one()


def _headers(group_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group_id, roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def _no_inverted_rows(db, vehicle_id) -> None:
    db.expire_all()
    for row in db.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == vehicle_id)).all():
        assert row.effective_to is None or row.effective_to >= row.effective_from, row.id


def test_a_future_owner_closes_the_current_owner_at_its_start_not_now(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644001")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    handover = (utcnow() + dt.timedelta(days=19)).replace(microsecond=0)

    meier_party = _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)

    clara_row = _row(db_session, clara_party.id)
    assert clara_row.effective_to == handover  # no gap: Clara holds until Meier starts
    assert clara_row.effective_to > utcnow()  # so she is still the current owner today
    assert _row(db_session, meier_party.id).effective_from == handover
    _no_inverted_rows(db_session, vehicle.id)


def test_api_create_from_the_customer_with_a_future_start_keeps_the_current_owner_until_then(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644002")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    db_session.commit()
    handover = (utcnow() + dt.timedelta(days=19)).replace(microsecond=0)

    response = client.post(
        f"/v1/customers/{meier.id}/vehicles",
        json={"vehicleId": str(vehicle.id), "role": "owner", "effectiveFrom": handover.isoformat()},
        headers=_headers(group_id),
    )

    assert response.status_code == 201, response.text
    assert _row(db_session, clara_party.id).effective_to == handover
    current = client.get(f"/v1/customers/{clara.id}/vehicles", headers=_headers(group_id))
    assert current.status_code == 200, current.text
    assert [item["id"] for item in current.json()["items"] if item["role"] == "owner"] == [str(clara_party.id)]


def test_a_new_owner_starting_now_still_closes_the_current_owner_now(db_session):
    """KAN-148's close rule is unchanged for a holder that starts now or earlier."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644003")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    before = utcnow()

    _allocate(db_session, vehicle.id, meier, group_id, effective_from=before - dt.timedelta(days=2))

    closed = _row(db_session, clara_party.id).effective_to
    assert closed is not None and before <= closed <= utcnow()


def test_api_allocate_from_the_vehicle_starts_now_and_closes_the_current_owner_now(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644004")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Meier")
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)
    db_session.commit()
    before = utcnow()

    response = client.post(
        f"/v1/vehicle-mdm/{vehicle.id}/allocate",
        json={"customerId": str(meier.id), "role": "owner"},
        headers=_headers(group_id),
    )

    assert response.status_code == 201, response.text
    closed = _row(db_session, clara_party.id).effective_to
    assert closed is not None and before <= closed <= utcnow()
    _no_inverted_rows(db_session, vehicle.id)


def test_a_current_owner_ending_before_the_future_start_is_not_lengthened(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644005")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Meier")
    clara_until = (utcnow() + dt.timedelta(days=5)).replace(microsecond=0)
    clara_party = _allocate(db_session, vehicle.id, clara, group_id, effective_to=clara_until)
    handover = (utcnow() + dt.timedelta(days=19)).replace(microsecond=0)

    _allocate(db_session, vehicle.id, meier, group_id, effective_from=handover)

    assert _row(db_session, clara_party.id).effective_to == clara_until


def test_a_reopen_with_a_future_start_closes_the_current_owner_at_that_start(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW644006")
    clara, meier = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Meier")
    meier_party = _allocate(db_session, vehicle.id, meier, group_id)
    clara_party = _allocate(db_session, vehicle.id, clara, group_id)  # closes Meier now
    handover = (utcnow() + dt.timedelta(days=19)).replace(microsecond=0)

    loaded = get_customer_vehicle_or_404(db_session, customer_id=meier.id, party_id=meier_party.id)
    update_customer_vehicle(
        db_session, party=loaded, data=CustomerVehicleUpdate(effective_from=handover, effective_to=None),
        actor_id=uuid.uuid4(), group_id=group_id,
    )

    assert _row(db_session, clara_party.id).effective_to == handover
    _no_inverted_rows(db_session, vehicle.id)
