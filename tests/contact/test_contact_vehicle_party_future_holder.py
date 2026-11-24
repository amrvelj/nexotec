"""KAN-583 (Anto, 2026-11-10, option A): a future holder is never closed
before it begins.

`allocate_vehicle_party` and a reopen by PATCH close every open holder of the
(vehicle, role) in the group at «now» (KAN-148). `_open_holders_in_group`
counts a row whose `effective_from` is still to come as open, so a car whose
sale was agreed for later (Mrs. Meier, owner from the 1st of next month) lost
that future owner when someone recorded another owner from now: her row was
closed at now, `effective_to < effective_from` — a period that never existed.

Ruling A: an allocation or a reopen whose period overlaps a future holder is
refused, 409 `vehicle_party_future_holder` naming that holder and its start;
one that ends before the future holder starts is accepted and leaves it
alone. Current holders keep KAN-148's close rule.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.contact.models.contact import Contact, ContactRole, ContactRoleAssignment, ContactType, Language
from app.contact.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.contact.schemas.contact import ContactVehicleCreate, ContactVehicleUpdate
from app.contact.services.vehicle_party import (
    allocate_vehicle_party,
    create_contact_vehicle,
    delete_contact_vehicle,
    update_contact_vehicle,
)
from app.contact.services.vehicle_party_reads import get_contact_vehicle_or_404
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.errors import ConflictError
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

OWNER = VehiclePartyRole.OWNER


def _contact(db_session, group_id: uuid.UUID, first_name: str, last_name: str = "Muster") -> Contact:
    contact = Contact(
        group_id=group_id, contact_number=f"K-{uuid.uuid4().hex[:6]}", contact_type=ContactType.INDIVIDUAL,
        language=Language.DE, first_name=first_name, last_name=last_name,
    )
    db_session.add(contact)
    db_session.flush()
    # KAN-464: every contact holds the customer role, as the create path and the migration leave it.
    db_session.add(ContactRoleAssignment(group_id=group_id, contact_id=contact.id, role=ContactRole.CUSTOMER))
    db_session.flush()
    return contact


def _vehicle(db_session, vin: str):
    vehicle = create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)
    db_session.commit()
    return vehicle


def _allocate(db, vehicle_id, contact: Contact, group_id, **period) -> VehicleParty:
    return allocate_vehicle_party(
        db, vehicle_id=vehicle_id, contact_id=contact.id, role=OWNER, group_id=group_id, actor_id=uuid.uuid4(), acting_dealership_id=None,
        **period,
    )


def _rows(db_session, vehicle_id) -> list[VehicleParty]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(VehicleParty).where(VehicleParty.vehicle_id == vehicle_id).order_by(VehicleParty.effective_from)
        ).all()
    )


@pytest.fixture(autouse=True)
def _never_inverted(db_session):
    """The exit criterion, after every test: no party row ends before it begins."""

    yield
    db_session.rollback()
    db_session.expire_all()
    inverted = db_session.scalars(
        select(VehicleParty).where(
            VehicleParty.effective_to.is_not(None), VehicleParty.effective_to < VehicleParty.effective_from
        )
    ).all()
    assert inverted == []


def _future_owner(db_session, group_id, vehicle, *, days: int = 20) -> tuple[Contact, VehicleParty]:
    meier = _contact(db_session, group_id, "Anna", "Meier")
    starts = (utcnow() + dt.timedelta(days=days)).replace(microsecond=0)
    return meier, _allocate(db_session, vehicle.id, meier, group_id, effective_from=starts)


def test_an_allocation_from_now_over_a_future_owner_is_refused_and_changes_nothing(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583001")
    meier, future = _future_owner(db_session, group_id, vehicle)
    huber = _contact(db_session, group_id, "Hans", "Huber")
    before = [(r.id, r.effective_from, r.effective_to) for r in _rows(db_session, vehicle.id)]

    with pytest.raises(ConflictError) as raised:
        _allocate(db_session, vehicle.id, huber, group_id)

    assert raised.value.details == {
        "reason": "vehicle_party_future_holder",
        "vehicleId": str(vehicle.id),
        "role": "owner",
        "vehiclePartyId": str(future.id),
        "contactId": str(meier.id),
        "contactName": "Anna Meier",
        "effectiveFrom": future.effective_from.isoformat(),
        "effectiveTo": None,
    }
    db_session.rollback()
    assert [(r.id, r.effective_from, r.effective_to) for r in _rows(db_session, vehicle.id)] == before


def test_an_allocation_over_a_future_owner_closes_no_current_owner_either(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583002")
    _, future = _future_owner(db_session, group_id, vehicle)
    # Clara holds the car until the day before Mrs. Meier takes it over.
    clara_until = future.effective_from - dt.timedelta(days=1)
    current = _allocate(db_session, vehicle.id, _contact(db_session, group_id, "Clara"), group_id, effective_to=clara_until)

    with pytest.raises(ConflictError):
        _allocate(db_session, vehicle.id, _contact(db_session, group_id, "Hans", "Huber"), group_id)

    db_session.rollback()
    db_session.refresh(current)
    assert current.effective_to == clara_until


def test_an_allocation_ending_before_the_future_owner_starts_is_accepted_and_leaves_it_alone(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583003")
    current = _allocate(db_session, vehicle.id, _contact(db_session, group_id, "Clara"), group_id)
    _, future = _future_owner(db_session, group_id, vehicle, days=20)
    huber = _contact(db_session, group_id, "Hans", "Huber")
    until = future.effective_from - dt.timedelta(days=1)

    interim = _allocate(db_session, vehicle.id, huber, group_id, effective_to=until)

    assert interim.effective_to == until
    db_session.refresh(future)
    assert future.effective_to is None  # the agreed handover stands
    db_session.refresh(current)
    assert current.effective_to is not None and current.effective_to <= utcnow()  # KAN-148, as before


def test_a_create_from_the_contact_over_a_future_owner_is_refused(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583004")
    _future_owner(db_session, group_id, vehicle)
    huber = _contact(db_session, group_id, "Hans", "Huber")

    with pytest.raises(ConflictError) as raised:
        create_contact_vehicle(
            db_session, contact=huber, data=ContactVehicleCreate(vehicle_id=vehicle.id, role=OWNER),
            actor_id=uuid.uuid4(), acting_dealership_id=None,
        )

    assert raised.value.details["reason"] == "vehicle_party_future_holder"


def test_a_reopen_over_a_future_owner_is_refused_and_changes_nothing(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583005")
    huber = _contact(db_session, group_id, "Hans", "Huber")
    huber_party = _allocate(db_session, vehicle.id, huber, group_id)
    delete_contact_vehicle(db_session, party=huber_party, actor_id=uuid.uuid4(), acting_dealership_id=None, group_id=group_id)
    _, future = _future_owner(db_session, group_id, vehicle)
    closed_at = _rows(db_session, vehicle.id)[0].effective_to

    loaded = get_contact_vehicle_or_404(db_session, contact_id=huber.id, party_id=huber_party.id)
    with pytest.raises(ConflictError) as raised:
        update_contact_vehicle(
            db_session, party=loaded, data=ContactVehicleUpdate(effective_to=None), actor_id=uuid.uuid4(), acting_dealership_id=None,
            group_id=group_id,
        )

    assert raised.value.details["reason"] == "vehicle_party_future_holder"
    assert raised.value.details["vehiclePartyId"] == str(future.id)
    db_session.rollback()
    db_session.refresh(huber_party)
    assert huber_party.effective_to == closed_at
    db_session.refresh(future)
    assert future.effective_to is None


def test_a_reopen_ending_before_the_future_owner_starts_is_accepted(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583006")
    huber = _contact(db_session, group_id, "Hans", "Huber")
    huber_party = _allocate(db_session, vehicle.id, huber, group_id)
    delete_contact_vehicle(db_session, party=huber_party, actor_id=uuid.uuid4(), acting_dealership_id=None, group_id=group_id)
    _, future = _future_owner(db_session, group_id, vehicle)
    until = future.effective_from - dt.timedelta(hours=1)

    loaded = get_contact_vehicle_or_404(db_session, contact_id=huber.id, party_id=huber_party.id)
    reopened = update_contact_vehicle(
        db_session, party=loaded, data=ContactVehicleUpdate(effective_to=until), actor_id=uuid.uuid4(), acting_dealership_id=None,
        group_id=group_id,
    )

    assert reopened.effective_to == until
    db_session.refresh(future)
    assert future.effective_to is None


def test_a_row_closed_at_its_own_start_is_neither_closed_again_nor_in_the_way(db_session):
    """The copy a merge kept (KAN-177) holds nothing: an allocation passes
    over it, even when its start is still to come."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583007")
    starts = (utcnow() + dt.timedelta(days=10)).replace(microsecond=0)
    empty = VehicleParty(
        vehicle_id=vehicle.id, contact_id=_contact(db_session, group_id, "Kopie").id, role=OWNER,
        effective_from=starts, effective_to=starts,
    )
    db_session.add(empty)
    db_session.commit()

    _allocate(db_session, vehicle.id, _contact(db_session, group_id, "Hans", "Huber"), group_id)

    db_session.refresh(empty)
    assert empty.effective_to == starts


def test_api_allocation_over_a_future_owner_answers_409_with_the_reason(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW583008")
    _future_owner(db_session, group_id, vehicle)
    huber = _contact(db_session, group_id, "Hans", "Huber")
    db_session.commit()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group_id, roles=frozenset({AccessRole.SALES}),
    )

    response = client.post(
        f"/v1/contacts/{huber.id}/vehicles",
        json={"vehicleId": str(vehicle.id), "role": "owner"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "vehicle_party_future_holder"
    assert details["contactName"] == "Anna Meier"
