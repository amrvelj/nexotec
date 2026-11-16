"""KAN-123 step 6 (ADR-162): `app.vehicle.public` hands another context the
record's id and VIN (`ResolvedVehicleMdm`), never the mapped `VehicleMdm`
row, and a missing record is still a 404.
"""

import dataclasses
import uuid

import pytest

from app.core.errors import NotFoundError
from app.vehicle import public
from app.vehicle.models.vehicle_mdm import VehicleMdm

VIN = "WVWZZZ1KZAW000738"


def test_a_vehicle_record_is_handed_out_as_a_frozen_value(db_session):
    created, was_created = public.create_or_get_vehicle_mdm(db_session, vin=VIN, actor_id=uuid.uuid4())

    found = public.get_vehicle_mdm_or_404(db_session, created.id)

    assert was_created is True
    for value in (created, found):
        assert isinstance(value, public.ResolvedVehicleMdm)
        assert not isinstance(value, VehicleMdm)
    assert found == public.ResolvedVehicleMdm(id=created.id, vin=VIN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.vin = "X"  # type: ignore[misc]


def test_found_again_it_is_the_same_record_not_created(db_session):
    first, _ = public.create_or_get_vehicle_mdm(db_session, vin=VIN, actor_id=uuid.uuid4())

    again, was_created = public.create_or_get_vehicle_mdm(db_session, vin=VIN, actor_id=uuid.uuid4())

    assert (again, was_created) == (first, False)


def test_a_missing_record_is_a_404(db_session):
    with pytest.raises(NotFoundError):
        public.get_vehicle_mdm_or_404(db_session, uuid.uuid4())


def test_vehicle_public_binds_neither_mapped_class():
    assert not hasattr(public, "VehicleMdm")
    assert not hasattr(public, "VehicleConfiguration")
