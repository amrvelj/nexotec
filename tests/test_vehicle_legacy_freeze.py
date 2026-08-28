"""WP-5 PR-7: the read-only-at-cutover switch. Off by default; when on,
writes to the old vehicle table 409, reads still work.
"""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.core.config import get_settings


def _token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=True,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_write_is_blocked_when_frozen(client, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("DMS_LEGACY_VEHICLE_WRITE_FROZEN", "true")
    get_settings.cache_clear()
    try:
        token = _token(uuid.uuid4())
        response = client.post(
            "/v1/vehicles",
            json={"vin": "1HGCM82633A004352", "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"},
            headers=_bearer(token),
        )
        assert response.status_code == 409, response.text
        assert "vehicle-mdm" in response.json()["error"]["message"]
    finally:
        monkeypatch.delenv("DMS_LEGACY_VEHICLE_WRITE_FROZEN", raising=False)
        get_settings.cache_clear()


def test_write_succeeds_when_not_frozen(client):
    get_settings.cache_clear()
    assert get_settings().legacy_vehicle_write_frozen is False
    token = _token(uuid.uuid4())
    response = client.post(
        "/v1/vehicles",
        json={"vin": "1HGCM82633A004352", "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
