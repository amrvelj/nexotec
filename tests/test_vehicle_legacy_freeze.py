"""WP-5 PR-7 / PR-3: the read-only-at-cutover switch. The shipped default
is now **frozen** (ADR-021) — writes to the legacy `vehicle` table 409 and
point at /v1/vehicle-mdm; reads still work, and the table is never dropped.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    """These tests drive `legacy_vehicle_write_frozen` directly, so they
    need a settings object that is not the one conftest's autouse
    `_legacy_vehicle_writes_open` fixture has forced open."""

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=True,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_VEHICLE_PAYLOAD = {"vin": "1HGCM82633A004352", "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}


def test_shipped_default_is_frozen(monkeypatch):
    monkeypatch.delenv("DMS_LEGACY_VEHICLE_WRITE_FROZEN", raising=False)
    assert Settings().legacy_vehicle_write_frozen is True


def test_write_is_blocked_when_frozen(client, monkeypatch):
    monkeypatch.setenv("DMS_LEGACY_VEHICLE_WRITE_FROZEN", "true")
    get_settings.cache_clear()

    response = client.post("/v1/vehicles", json=_VEHICLE_PAYLOAD, headers=_bearer(_token(uuid.uuid4())))

    assert response.status_code == 409, response.text
    assert "vehicle-mdm" in response.json()["error"]["message"]


def test_reads_still_work_when_frozen(client, db_session, monkeypatch):
    """The table is read-only, not gone — a GET by id still resolves."""

    from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
    from app.vehicle.models.vehicle import VehicleCondition, VehicleStatus

    monkeypatch.setenv("DMS_LEGACY_VEHICLE_WRITE_FROZEN", "true")
    get_settings.cache_clear()

    legacy = LegacyVehicle(
        vin="WVWZZZ1JZXW000001", make="VW", model="Golf", model_year=2019,
        condition=VehicleCondition.USED, status=VehicleStatus.IN_STOCK,
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)

    response = client.get(f"/v1/vehicles/{legacy.id}", headers=_bearer(_token(uuid.uuid4())))

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == "WVWZZZ1JZXW000001"


def test_write_succeeds_when_explicitly_unfrozen(client, monkeypatch):
    monkeypatch.setenv("DMS_LEGACY_VEHICLE_WRITE_FROZEN", "false")
    get_settings.cache_clear()

    response = client.post("/v1/vehicles", json=_VEHICLE_PAYLOAD, headers=_bearer(_token(uuid.uuid4())))

    assert response.status_code == 201, response.text
