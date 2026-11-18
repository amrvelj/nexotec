"""WP-5 PR-3: the physical vehicle — VehicleMdm and its global number
allocator.
"""

import pytest

from app.core.errors import ConflictError
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleStatus
from app.vehicle.services.vehicle_mdm import allocate_vehicle_number, create_vehicle_mdm, get_vehicle_mdm_by_vin


def test_create_vehicle_mdm_allocates_sequential_numbers(db_session):
    v1 = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    v2 = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)

    assert v1.vehicle_number.startswith("F-")
    assert v2.vehicle_number.startswith("F-")
    assert v1.vehicle_number != v2.vehicle_number


def test_two_allocations_never_collide(db_session):
    numbers = {allocate_vehicle_number(db_session) for _ in range(20)}
    assert len(numbers) == 20


def test_duplicate_vin_raises_conflict_not_generic_error(db_session):
    create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    with pytest.raises(ConflictError):
        create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)


def test_vehicle_without_catalogue_link_is_unverified(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    assert vehicle.catalogue_match_status == CatalogueMatchStatus.UNVERIFIED


def test_get_vehicle_mdm_by_vin(db_session):
    created = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    found = get_vehicle_mdm_by_vin(db_session, "ZAR94000007123456")
    assert found is not None and found.id == created.id
    assert get_vehicle_mdm_by_vin(db_session, "NOSUCHVIN00000000") is None


def test_vehicle_status_has_exactly_four_lifecycle_only_members():
    """Regression guard for the exact mistake ADR-021/ADR-040 exist to fix
    — the shipped table's VehicleStatus mixed lifecycle with stock state
    (in_transit/in_stock/sold/...). This one must never grow a stock-shaped
    member.
    """

    members = {member.value for member in VehicleStatus}
    assert members == {"active", "exported", "scrapped", "stolen"}
    stock_shaped_words = {"available", "reserved", "sold", "in_stock", "in_transit"}
    assert not (members & stock_shaped_words)
