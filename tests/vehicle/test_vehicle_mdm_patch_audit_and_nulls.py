"""KAN-561 — PATCH /v1/vehicle-mdm/{id}: the VIN is the vehicle's identity.

Before the fix: `update_vehicle_mdm` said a VIN correction is "audit-logged
here" but wrote no audit row (the KAN-186 event was the only trace), and an
explicit `"vin": null` reached the NOT NULL column, whose IntegrityError the
duplicate-VIN handler answered as 409 «A vehicle with VIN 'None' already
exists». The same handler named a VIN for a duplicate Stammnummer. Now every
change is audited (actor, before/after of the changed fields), a null for a
required field is a 422, and a duplicate is named for the field it is.
Postgres lane (ADR-011).
"""

import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import create_access_token
from tests.token_support import dealer_group_of

VIN = "1HGCM82633A004352"
CORRECTED_VIN = "WVWZZZ1JZXW000001"
OTHER_VIN = "WAUZZZ8V5KA000002"


def _principal() -> tuple[uuid.UUID, dict[str, str]]:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    return user_id, {"Authorization": f"Bearer {token}"}


def _vehicle(client, headers, vin: str = VIN) -> dict:
    response = client.post("/v1/vehicle-mdm", json={"vin": vin}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    return response.json()["vehicle"]


def _patch(client, headers, vehicle: dict, body: dict):
    return client.patch(
        f"/v1/vehicle-mdm/{vehicle['id']}", json=body, headers={**headers, "If-Match": str(vehicle["version"])}
    )


def _audit_rows(db_session, vehicle_id: str) -> list[AuditEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "vehicle_mdm",
                AuditEvent.entity_id == uuid.UUID(vehicle_id),
                AuditEvent.action == "update",
            )
        )
    )


def test_a_vin_correction_writes_an_audit_row_with_the_actor(client, db_session):
    user_id, headers = _principal()
    vehicle = _vehicle(client, headers)

    corrected = _patch(client, headers, vehicle, {"vin": CORRECTED_VIN})

    assert corrected.status_code == 200, corrected.text
    [row] = _audit_rows(db_session, vehicle["id"])
    assert row.actor_id == user_id
    assert row.before == {"vin": VIN}
    assert row.after == {"vin": CORRECTED_VIN}


def test_a_patch_that_changes_nothing_writes_no_audit_row(client, db_session):
    _, headers = _principal()
    vehicle = _vehicle(client, headers)

    assert _patch(client, headers, vehicle, {"vin": VIN}).status_code == 200

    assert _audit_rows(db_session, vehicle["id"]) == []


def test_a_null_vin_is_a_422_not_a_duplicate_vin(client, db_session):
    _, headers = _principal()
    vehicle = _vehicle(client, headers)

    response = _patch(client, headers, vehicle, {"vin": None})

    assert response.status_code == 422, response.text
    assert "None" not in response.text
    assert _audit_rows(db_session, vehicle["id"]) == []


def test_a_null_vehicle_status_is_a_422(client):
    _, headers = _principal()
    vehicle = _vehicle(client, headers)

    assert _patch(client, headers, vehicle, {"vehicleStatus": None}).status_code == 422


def test_a_duplicate_vin_is_named_as_the_vin(client):
    _, headers = _principal()
    _vehicle(client, headers, OTHER_VIN)
    vehicle = _vehicle(client, headers)

    response = _patch(client, headers, vehicle, {"vin": OTHER_VIN})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"vin": OTHER_VIN}


def test_a_duplicate_stammnummer_is_named_as_the_stammnummer_not_a_vin(client):
    _, headers = _principal()
    first = _vehicle(client, headers, OTHER_VIN)
    assert _patch(client, headers, first, {"stammnummer": "123456789"}).status_code == 200
    vehicle = _vehicle(client, headers)

    response = _patch(client, headers, vehicle, {"stammnummer": "123456789"})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"stammnummer": "123456789"}
    assert "VIN" not in response.json()["error"]["message"]
