"""KAN-427 — ending a recorded accessory (Anto, 2026-11-03, option B).

The car is shared by every Betrieb, so any Betrieb with vehicle_mdm write may
end an accessory another one recorded (no 404) — but before the fix the end
wrote no audit row, so who ended Garage A's towbar was untraceable. Now every
end is audited with the actor, the ending Betrieb and the row as it was; a
repeated DELETE changes nothing; the end date is the Swiss calendar day.
Postgres lane (ADR-011).
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import create_access_token
from app.vehicle.models.vehicle_history import VehicleAccessory
from app.vehicle.services import plate as plate_service
from tests.token_support import dealer_group_of

VALID_VIN = "1HGCM82633A004352"


def _principal() -> tuple[uuid.UUID, uuid.UUID, dict[str, str]]:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    return tenant_id, user_id, {"Authorization": f"Bearer {token}"}


def _towbar_recorded_by_garage_a(client) -> tuple[dict, dict, uuid.UUID]:
    garage_a, _, headers_a = _principal()
    vehicle = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=headers_a).json()["vehicle"]
    accessory = client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/accessories",
        json={"accessoryType": "towbar", "description": "Westfalia", "validFrom": "2024-01-01"},
        headers=headers_a,
    )
    assert accessory.status_code == 201, accessory.text
    return vehicle, accessory.json(), garage_a


def _ended_audit_rows(db_session, accessory_id: str) -> list[AuditEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "vehicle_accessory",
                AuditEvent.entity_id == uuid.UUID(accessory_id),
                AuditEvent.action == "accessory_ended",
            )
        )
    )


def test_another_betrieb_may_end_the_accessory_and_the_end_is_audited(client, db_session):
    vehicle, accessory, garage_a = _towbar_recorded_by_garage_a(client)
    garage_b, user_b, headers_b = _principal()

    ended = client.delete(f"/v1/vehicle-mdm/{vehicle['id']}/accessories/{accessory['id']}", headers=headers_b)

    assert ended.status_code == 204, ended.text
    rows = _ended_audit_rows(db_session, accessory["id"])
    # Both Betriebe see it (Anto, 2026-11-04, option A): the one that ended it
    # and the one that recorded it, the same entry in each one's audit log.
    assert sorted(str(row.tenant_id) for row in rows) == sorted([str(garage_a), str(garage_b)])
    for row in rows:
        assert row.actor_id == user_b
        assert row.before["validTo"] is None
        assert row.before["recordingTenantId"] == str(garage_a)
        assert row.before["accessoryType"] == "towbar"
        assert row.after["validTo"] == plate_service.swiss_today().isoformat()
    stored = db_session.get(VehicleAccessory, uuid.UUID(accessory["id"]))
    assert stored is not None  # FR-V-13: closed, never deleted


def test_the_recording_betrieb_ending_its_own_accessory_writes_one_row(client, db_session):
    vehicle, accessory, garage_a = _towbar_recorded_by_garage_a(client)
    token_a = create_access_token(
        user_id=uuid.uuid4(), tenant_id=garage_a, group_id=dealer_group_of(str(garage_a)),
        roles=frozenset(), is_dealer_manager=True,
    )

    ended = client.delete(
        f"/v1/vehicle-mdm/{vehicle['id']}/accessories/{accessory['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert ended.status_code == 204, ended.text
    [row] = _ended_audit_rows(db_session, accessory["id"])
    assert row.tenant_id == garage_a


def test_ending_an_ended_accessory_changes_nothing(client, db_session, monkeypatch):
    vehicle, accessory, _ = _towbar_recorded_by_garage_a(client)
    _, _, headers_b = _principal()
    path = f"/v1/vehicle-mdm/{vehicle['id']}/accessories/{accessory['id']}"
    assert client.delete(path, headers=headers_b).status_code == 204
    first_end = db_session.get(VehicleAccessory, uuid.UUID(accessory["id"])).valid_to

    # A day later, someone else repeats the DELETE.
    monkeypatch.setattr(plate_service, "_clock", lambda: dt.datetime.now(dt.UTC) + dt.timedelta(days=1))
    _, _, headers_c = _principal()
    assert client.delete(path, headers=headers_c).status_code == 204

    db_session.expire_all()
    assert db_session.get(VehicleAccessory, uuid.UUID(accessory["id"])).valid_to == first_end
    assert len(_ended_audit_rows(db_session, accessory["id"])) == 2  # the first end's two rows, no more


def test_the_end_date_is_the_swiss_day_not_the_utc_day(client, db_session, monkeypatch):
    """23:30 UTC on 10 January is 00:30 on 11 January in Zurich (UTC+1)."""

    vehicle, accessory, _ = _towbar_recorded_by_garage_a(client)
    _, _, headers_b = _principal()
    monkeypatch.setattr(plate_service, "_clock", lambda: dt.datetime(2026, 1, 10, 23, 30, tzinfo=dt.UTC))

    assert client.delete(
        f"/v1/vehicle-mdm/{vehicle['id']}/accessories/{accessory['id']}", headers=headers_b
    ).status_code == 204

    db_session.expire_all()
    assert db_session.get(VehicleAccessory, uuid.UUID(accessory["id"])).valid_to == dt.date(2026, 1, 11)
