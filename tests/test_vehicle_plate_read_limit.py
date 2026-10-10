"""KAN-231: the plate table could be exported by paging the vehicle list and
reading /plates per vehicle (ADR-039, PRD Vehicles Halterauskunftssperre).
Anto's ruling, "log and limit": every per-vehicle plate read is audited,
and a user past the limit of distinct vehicles per window is refused — on
the Plates tab and on the plate of a resolved search hit alike.
"""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import create_access_token
from app.core.base import utcnow
from app.core.config import get_settings
from app.vehicle.services import plate_read_guard
from app.vehicle.services.plate import record_plate_assignment
from app.vehicle.services.plate_read_guard import (
    ACTION_PLATE_READ,
    ACTION_PLATE_READ_REFUSED,
    PLATE_READ_ENTITY_TYPE,
)
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

LIMIT = 3
VINS = ["ZAR94000007123456", "WVWZZZ1JZXW000001", "1HGCM82633A004352", "WBA3A5C51CF256985", "VF1RFB00X57123456"]


@pytest.fixture(autouse=True)
def _small_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "plate_read_limit", LIMIT)
    monkeypatch.setattr(get_settings(), "plate_read_window_seconds", 3600)


def _bearer(user_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _seed(db_session) -> list:
    vehicles = []
    for index, vin in enumerate(VINS):
        vehicle = create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)
        record_plate_assignment(
            db_session, vehicle_id=vehicle.id, plate=f"ZH {10000 + index}", canton="ZH",
            valid_from=dt.date(2020, 1, 1), valid_to=None, is_interchangeable=False, plate_group_id=None,
            recording_tenant_id=uuid.uuid4(),
        )
        vehicles.append(vehicle)
    db_session.commit()
    return vehicles


def _plate_audit(db_session, actor_id: uuid.UUID) -> list[AuditEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == PLATE_READ_ENTITY_TYPE, AuditEvent.actor_id == actor_id)
            .order_by(AuditEvent.created_at)
        ).all()
    )


def _page_all_vehicle_ids(client, headers) -> list[str]:
    """Walk the whole vehicle list by cursor, no identifier — the first half
    of the N+1 export the ticket describes."""

    ids, cursor = [], None
    while True:
        url = "/v1/vehicle-mdm/search?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=headers).json()["filtered"]
        ids += [item["id"] for item in page["items"]]
        cursor = page["nextCursor"]
        if not cursor:
            return ids


def test_paging_the_vehicle_list_and_reading_plates_is_refused_past_the_limit(client, db_session):
    _seed(db_session)
    user, tenant = uuid.uuid4(), uuid.uuid4()
    headers = _bearer(user, tenant)

    vehicle_ids = _page_all_vehicle_ids(client, headers)
    assert len(vehicle_ids) == len(VINS)

    responses = [client.get(f"/v1/vehicle-mdm/{vid}/plates", headers=headers) for vid in vehicle_ids]

    assert [r.status_code for r in responses] == [200] * LIMIT + [403] * (len(VINS) - LIMIT)
    refused = responses[LIMIT].json()["error"]
    assert refused["code"] == "forbidden"
    assert refused["details"] == {"reason": "plate_read_limit_reached"}
    assert all(len(r.json()) == 1 for r in responses[:LIMIT])

    audit = _plate_audit(db_session, user)
    assert [e.action for e in audit] == [ACTION_PLATE_READ] * LIMIT + [ACTION_PLATE_READ_REFUSED] * (
        len(VINS) - LIMIT
    )
    assert [str(e.entity_id) for e in audit] == vehicle_ids
    assert all(e.tenant_id == tenant for e in audit)


def test_a_single_read_is_audited_without_the_plate_value(client, db_session):
    vehicles = _seed(db_session)
    user, tenant = uuid.uuid4(), uuid.uuid4()

    response = client.get(f"/v1/vehicle-mdm/{vehicles[0].id}/plates", headers=_bearer(user, tenant))

    assert response.status_code == 200
    assert response.json()[0]["plate"] == "ZH 10000"
    [event] = _plate_audit(db_session, user)
    assert (event.action, event.entity_id, event.tenant_id) == (ACTION_PLATE_READ, vehicles[0].id, tenant)
    assert event.created_at is not None
    assert event.before is None and event.after is None
    assert "ZH" not in (event.reason or "")


def test_rereading_a_vehicle_inside_the_window_does_not_count_again(client, db_session):
    vehicles = _seed(db_session)
    user, tenant = uuid.uuid4(), uuid.uuid4()
    headers = _bearer(user, tenant)

    for _ in range(LIMIT + 2):
        assert client.get(f"/v1/vehicle-mdm/{vehicles[0].id}/plates", headers=headers).status_code == 200
    for vehicle in vehicles[1:LIMIT]:
        assert client.get(f"/v1/vehicle-mdm/{vehicle.id}/plates", headers=headers).status_code == 200
    # At the limit: a new vehicle is refused, one already read is not.
    assert client.get(f"/v1/vehicle-mdm/{vehicles[LIMIT].id}/plates", headers=headers).status_code == 403
    assert client.get(f"/v1/vehicle-mdm/{vehicles[0].id}/plates", headers=headers).status_code == 200


def test_reads_older_than_the_window_no_longer_count(client, db_session):
    vehicles = _seed(db_session)
    user, tenant = uuid.uuid4(), uuid.uuid4()
    long_ago = utcnow() - dt.timedelta(seconds=3601)
    for vehicle in vehicles[:LIMIT]:
        db_session.add(
            AuditEvent(
                entity_type=PLATE_READ_ENTITY_TYPE, entity_id=vehicle.id, tenant_id=tenant,
                action=ACTION_PLATE_READ, actor_id=user, created_at=long_ago,
            )
        )
    db_session.commit()

    response = client.get(f"/v1/vehicle-mdm/{vehicles[LIMIT].id}/plates", headers=_bearer(user, tenant))

    assert response.status_code == 200


def test_the_limit_is_per_user(client, db_session):
    vehicles = _seed(db_session)
    tenant = uuid.uuid4()
    first, second = _bearer(uuid.uuid4(), tenant), _bearer(uuid.uuid4(), tenant)
    for vehicle in vehicles[:LIMIT]:
        client.get(f"/v1/vehicle-mdm/{vehicle.id}/plates", headers=first)

    assert client.get(f"/v1/vehicle-mdm/{vehicles[LIMIT].id}/plates", headers=first).status_code == 403
    assert client.get(f"/v1/vehicle-mdm/{vehicles[LIMIT].id}/plates", headers=second).status_code == 200


def test_a_resolved_search_hit_is_audited_and_withholds_its_plate_past_the_limit(client, db_session):
    vehicles = _seed(db_session)
    user, tenant = uuid.uuid4(), uuid.uuid4()
    headers = _bearer(user, tenant)

    hit = client.get(f"/v1/vehicle-mdm/search?q={VINS[0]}", headers=headers).json()["resolved"]
    assert (hit["currentPlate"], hit["currentPlateWithheld"]) == ("ZH 10000", False)
    [event] = _plate_audit(db_session, user)
    assert (event.action, event.entity_id, event.reason) == (ACTION_PLATE_READ, vehicles[0].id, "search_hit")

    for vehicle in vehicles[1:LIMIT]:
        client.get(f"/v1/vehicle-mdm/{vehicle.id}/plates", headers=headers)
    hit = client.get(f"/v1/vehicle-mdm/search?q={VINS[LIMIT]}", headers=headers).json()["resolved"]

    assert hit["id"] == str(vehicles[LIMIT].id)
    assert (hit["currentPlate"], hit["currentPlateWithheld"]) == (None, True)
    assert _plate_audit(db_session, user)[-1].action == ACTION_PLATE_READ_REFUSED


def test_concurrent_reads_by_one_user_cannot_both_slip_under_the_limit(engine, db_session, monkeypatch):
    """Limit 1, two different vehicles read at once by one user: the second
    read waits for the first's commit, then counts it and is refused.
    Without the per-actor lock both would count zero prior reads and pass."""

    if engine.dialect.name != "postgresql":
        pytest.skip("advisory locks are Postgres-only; the SQLite lane serialises writers itself")

    monkeypatch.setattr(get_settings(), "plate_read_limit", 1)
    vehicles = _seed(db_session)
    user, tenant = uuid.uuid4(), uuid.uuid4()

    first_counted, release = threading.Event(), threading.Event()
    real_record = plate_read_guard.record_audit_event

    def _pause_first(db, **kwargs):
        if threading.current_thread().name == "first":
            first_counted.set()
            release.wait(timeout=10)
        return real_record(db, **kwargs)

    monkeypatch.setattr(plate_read_guard, "record_audit_event", _pause_first)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    results: dict[str, bool] = {}
    errors: list[Exception] = []

    def _read(vehicle_id):
        with factory() as session:
            try:
                plates = plate_read_guard.read_plate_history(
                    session, actor_id=user, tenant_id=tenant, vehicle_id=vehicle_id, purpose="plates_tab"
                )
                results[threading.current_thread().name] = plates is not None
            except Exception as exc:  # noqa: BLE001 — collected and asserted empty below, never swallowed
                errors.append(exc)

    first = threading.Thread(target=_read, args=(vehicles[0].id,), name="first")
    second = threading.Thread(target=_read, args=(vehicles[1].id,), name="second")
    first.start()
    try:
        assert first_counted.wait(timeout=10)
        second.start()
        second.join(timeout=1)
        assert second.is_alive()  # blocked behind the first read's lock
    finally:
        release.set()
        first.join(timeout=10)
        if second.ident is not None:
            second.join(timeout=10)
        monkeypatch.undo()

    assert errors == []
    assert results == {"first": True, "second": False}


def test_the_plate_read_entity_type_is_the_one_the_index_covers():
    """The guard's entity type is spelled in three places — the guard, the
    core model's partial index and its migration. Renaming one would leave
    the per-read count without its index, silently."""

    import pathlib

    from app.core.audit_model import AuditEvent

    predicate = f"entity_type = '{PLATE_READ_ENTITY_TYPE}'"
    [index] = [i for i in AuditEvent.__table__.indexes if i.name == "ix_audit_event_plate_read_actor_created"]
    assert str(index.dialect_options["postgresql"]["where"]) == predicate
    migration = pathlib.Path(__file__).resolve().parents[1] / (
        "alembic/versions/core/a3d8f6c2e917_audit_event_plate_read_index_kan_231.py"
    )
    assert predicate in migration.read_text(encoding="utf-8")
