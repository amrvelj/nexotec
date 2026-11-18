"""WP-5 PR-6: the matching waterfall stops at the first decisive/probable
rung rather than falling through.
"""

import datetime as dt
import uuid

from app.vehicle.services.matching import match_vehicle
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.plate_support import seed_plate

TENANT_ID = uuid.uuid4()


def test_vin_exact_is_decisive_and_wins_over_everything_else(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None, stammnummer="123456789")
    result = match_vehicle(db_session, vin="ZAR94000007123456", stammnummer="000000000")
    assert result.rung == "vin"
    assert result.decisive is True
    assert result.requires_confirmation is False
    assert result.vehicle is not None and result.vehicle.id == vehicle.id


def test_stammnummer_exact_is_decisive(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None, stammnummer="123456789")
    result = match_vehicle(db_session, stammnummer="123456789")
    assert result.rung == "stammnummer"
    assert result.decisive is True
    assert result.vehicle is not None and result.vehicle.id == vehicle.id


def test_plate_match_is_probable_and_requires_confirmation(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    group = uuid.uuid4()
    seed_plate(db_session, vehicle_id=vehicle.id, group_id=group, plate="ZH 284611", valid_from=dt.date(2026, 1, 1))
    result = match_vehicle(db_session, plate="ZH 284611", canton="ZH", group_id=group)
    assert result.rung == "plate"
    assert result.decisive is False
    assert result.requires_confirmation is True
    assert result.vehicle is not None and result.vehicle.id == vehicle.id


def test_no_identifiers_match_falls_through_to_no_match(db_session):
    result = match_vehicle(db_session, vin="NOSUCHVIN00000000")
    assert result.rung == "no_match"
    assert result.vehicle is None
    assert result.decisive is False


def test_type_approval_match_code_2_always_requires_confirmation(db_session):
    create_vehicle_mdm(
        db_session, vin="ZAR94000007123456", catalogue_variant_id=None, type_approval_number="1AB234"
    )
    create_vehicle_mdm(
        db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None, type_approval_number="1AB234"
    )
    result = match_vehicle(db_session, type_approval_number="1AB234")
    assert result.rung == "type_approval"
    assert result.match_code == 2
    assert result.requires_confirmation is True


def test_the_public_match_hands_out_a_frozen_value_never_the_row(db_session):
    """KAN-754 (ADR-162): another context's match answers the record's id and
    VIN as plain data — no `VehicleMdm` row it could change and flush."""

    import dataclasses

    import pytest

    from app.vehicle.models.vehicle_mdm import VehicleMdm
    from app.vehicle.public import ResolvedVehicleMdm, VehicleMatch
    from app.vehicle.public import match_vehicle as public_match_vehicle

    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)

    found = public_match_vehicle(db_session, vin="ZAR94000007123456", group_id=uuid.uuid4())

    assert found == VehicleMatch(
        rung="vin",
        vehicle=ResolvedVehicleMdm(id=vehicle.id, vin="ZAR94000007123456"),
        decisive=True,
        requires_confirmation=False,
    )
    assert not isinstance(found.vehicle, VehicleMdm)
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.vehicle.vin = "WVWZZZ1JZXW000001"  # type: ignore[misc,union-attr]

    nothing = public_match_vehicle(db_session, vin="NOSUCHVIN00000000", group_id=uuid.uuid4())
    assert (nothing.rung, nothing.vehicle) == ("no_match", None)
