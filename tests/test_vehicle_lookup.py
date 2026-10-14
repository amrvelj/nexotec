"""WP-5 PR-6: FR-V-14 shared identity lookup."""

import datetime as dt
import uuid

from app.core.auth import create_access_token
from app.vehicle.services.lookup import resolve_shared_identity
from app.vehicle.services.plate import record_plate_assignment
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TENANT_ID = uuid.uuid4()
# KAN-279: the plate in the answer is a plate read, attributed to the caller.
CALLER = {"actor_id": uuid.uuid4(), "tenant_id": uuid.uuid4()}


def test_resolve_by_vin(db_session):
    create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    result = resolve_shared_identity(db_session, **CALLER, vin="ZAR94000007123456")
    assert result is not None and result.vin == "ZAR94000007123456"


def test_resolve_by_plate_includes_current_plate(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    record_plate_assignment(
        db_session, vehicle_id=vehicle.id, plate="ZH 284611", canton="ZH", valid_from=dt.date(2026, 1, 1),
        valid_to=None, is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )
    result = resolve_shared_identity(db_session, **CALLER, plate="ZH 284611", canton="ZH")
    assert result is not None
    assert result.current_plate == "ZH 284611"


def test_unresolvable_identifier_returns_none(db_session):
    assert resolve_shared_identity(db_session, **CALLER, vin="NOSUCHVIN00000000") is None
    assert resolve_shared_identity(db_session, **CALLER) is None


def test_resolve_by_vin_normalises_the_typed_spelling(db_session):
    """KAN-355: the lookup compares the VIN the way every write stores it
    (`NormalisedVin`, KAN-331/338) — a lower-cased or spaced spelling of a
    known car finds it (FR-V-15: one VIN, one car)."""

    create_vehicle_mdm(db_session, vin="WVWZZZ1KZAW123456", catalogue_variant_id=None)
    for typed in ("wvwzzz1kzaw123456", "WVW ZZZ 1KZ AW1 23456", " wvwZZZ1KZaw123456\t"):
        result = resolve_shared_identity(db_session, **CALLER, vin=typed)
        assert result is not None and result.vin == "WVWZZZ1KZAW123456", typed


def test_a_value_that_is_no_vin_after_normalising_finds_nothing(db_session):
    """KAN-355: the lookup is a read, so a malformed VIN is 'no such car'
    (None, the endpoint's 404), never a 422 — and it never falls through to
    another identifier."""

    create_vehicle_mdm(db_session, vin="WVWZZZ1KZAW123456", catalogue_variant_id=None)
    assert resolve_shared_identity(db_session, **CALLER, vin="WVWZZZ1KZAW12345") is None  # 16 characters
    assert resolve_shared_identity(db_session, **CALLER, vin="WVWZZZ1KZAW12345O") is None  # O is never a VIN letter
    assert resolve_shared_identity(db_session, **CALLER, vin="   ") is None


def test_lookup_endpoint_finds_a_lower_cased_vin_and_answers_404_for_a_malformed_one(client, db_session):
    create_vehicle_mdm(db_session, vin="WVWZZZ1KZAW123456", catalogue_variant_id=None)
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(), roles=frozenset(),
        is_dealer_manager=False,
    )
    headers = {"Authorization": f"Bearer {token}"}

    found = client.get("/v1/vehicle-mdm/lookup", params={"vin": "wvw zzz 1kz aw1 23456"}, headers=headers)
    assert found.status_code == 200, found.text
    assert found.json()["vin"] == "WVWZZZ1KZAW123456"

    malformed = client.get("/v1/vehicle-mdm/lookup", params={"vin": "not-a-vin"}, headers=headers)
    assert malformed.status_code == 404, malformed.text
