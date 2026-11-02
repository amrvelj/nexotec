"""KAN-121 (rule 2): the two plain-data reads that replaced another
context's SQL — platform's group Betriebe for Stock's group listing, and
customer's party count for vehicle's legacy-reference check."""

import uuid

from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.public import count_vehicle_parties_for_vehicles
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.public import DealershipRef, list_group_read_dealerships
from tests.dealership_support import valid_uid


def _group(db_session, *, read_enabled: bool) -> DealerGroup:
    group = DealerGroup(name=f"Group {uuid.uuid4().hex[:6]}", group_read_enabled=read_enabled)
    db_session.add(group)
    db_session.commit()
    return group


def _dealership(db_session, group: DealerGroup, legal_name: str) -> Dealership:
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name=legal_name,
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        uid=valid_uid(),
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def test_group_read_dealerships_are_the_groups_own_sorted_by_label(db_session):
    group = _group(db_session, read_enabled=True)
    second = _dealership(db_session, group, "Garage Zürich")
    first = _dealership(db_session, group, "Autohaus Bern")
    other = _group(db_session, read_enabled=True)
    _dealership(db_session, other, "Garage of another group")

    refs = list_group_read_dealerships(db_session, group_id=group.id)

    assert refs == [DealershipRef(id=first.id, label="Autohaus Bern"), DealershipRef(id=second.id, label="Garage Zürich")]


def test_group_read_dealerships_are_none_while_the_groups_read_is_off(db_session):
    group = _group(db_session, read_enabled=False)
    _dealership(db_session, group, "Garage Zürich")

    assert list_group_read_dealerships(db_session, group_id=group.id) is None


def test_group_read_dealerships_are_none_for_a_group_that_does_not_exist(db_session):
    assert list_group_read_dealerships(db_session, group_id=uuid.uuid4()) is None


def test_an_enabled_group_without_a_betrieb_is_an_empty_list_not_none(db_session):
    group = _group(db_session, read_enabled=True)

    assert list_group_read_dealerships(db_session, group_id=group.id) == []


def test_vehicle_party_count_covers_every_row_for_the_given_vehicles(db_session):
    named, also_named, unrelated = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            VehicleParty(vehicle_id=named, customer_id=uuid.uuid4(), role=VehiclePartyRole.OWNER),
            VehicleParty(vehicle_id=named, customer_id=uuid.uuid4(), role=VehiclePartyRole.KEEPER),
            VehicleParty(vehicle_id=also_named, customer_id=uuid.uuid4(), role=VehiclePartyRole.DRIVER),
            VehicleParty(vehicle_id=unrelated, customer_id=uuid.uuid4(), role=VehiclePartyRole.OWNER),
        ]
    )
    db_session.commit()

    assert count_vehicle_parties_for_vehicles(db_session, vehicle_ids=[named, also_named]) == 3
    assert count_vehicle_parties_for_vehicles(db_session, vehicle_ids=[uuid.uuid4()]) == 0
    assert count_vehicle_parties_for_vehicles(db_session, vehicle_ids=[]) == 0
