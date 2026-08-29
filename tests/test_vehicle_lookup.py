"""WP-5 PR-6: FR-V-14 shared identity lookup."""

import datetime as dt
import uuid

from app.vehicle.services.lookup import resolve_shared_identity
from app.vehicle.services.plate import record_plate_assignment
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TENANT_ID = uuid.uuid4()


def test_resolve_by_vin(db_session):
    create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    result = resolve_shared_identity(db_session, vin="ZAR94000007123456")
    assert result is not None and result.vin == "ZAR94000007123456"


def test_resolve_by_plate_includes_current_plate(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    record_plate_assignment(
        db_session, vehicle_id=vehicle.id, plate="ZH 284611", canton="ZH", valid_from=dt.date(2026, 1, 1),
        valid_to=None, is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )
    result = resolve_shared_identity(db_session, plate="ZH 284611", canton="ZH")
    assert result is not None
    assert result.current_plate == "ZH 284611"


def test_unresolvable_identifier_returns_none(db_session):
    assert resolve_shared_identity(db_session, vin="NOSUCHVIN00000000") is None
    assert resolve_shared_identity(db_session) is None
