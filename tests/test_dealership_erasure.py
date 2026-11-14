"""KAN-218 — the deletion after the retention period (Dealer Administration
FR-A-45; Anto, 2026-11-13, Q3 A: ten full years from the offboarding date,
for everything).

The daily job erases an offboarded Betrieb the day after its archive's last
day — never a day early — context by context on sessions of their own,
platform last, and writes one platform-level audit row. Users are never
deleted (A-RULE-5): minimised, their id stays; the Betrieb's row stays as the
tombstone; the audit log's rows stay with their values redacted (ADR-121).
A run that stops resumes where it stopped. Group-owned customers stay while a
sister Betrieb still holds data.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core import export as core_export
from app.core.audit import REDACTED
from app.core.audit_model import AuditEvent
from app.customer.models.customer import Customer, CustomerEmail
from app.platform.models.dealership import Dealership, Location
from app.platform.models.export_job import ExportJob
from app.platform.models.user import User
from app.platform.services import offboarding
from tests.data_export_support import (
    betrieb_with_manager,
    export_store_fixture,  # noqa: F401 - registers the export_store fixture
    exported_and_acknowledged,
    idem,
    staff,
)


def _customer(client, headers) -> dict:
    response = client.post(
        "/v1/customers",
        json={
            "firstName": "Anna",
            "lastName": "Muster",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
        },
        headers=idem(headers),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _offboarded(client, engine, *, legal_name="Garage Muster AG", group_of=None):
    dealership_id, manager, headers = betrieb_with_manager(client, legal_name, group_of=group_of)
    customer = _customer(client, headers)
    exported_and_acknowledged(client, engine, dealership_id, headers)
    version = client.get(f"/v1/dealerships/{dealership_id}", headers=staff()).json()["version"]
    response = client.post(
        f"/v1/platform/dealerships/{dealership_id}/offboard",
        json={"confirmation": True},
        headers=idem(staff(), **{"If-Match": str(version)}),
    )
    assert response.status_code == 200, response.text
    return dealership_id, manager, customer, dt.date.fromisoformat(response.json()["retentionUntil"])


def _run(engine, db_session, today: dt.date) -> list[uuid.UUID]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    result = offboarding.run_erasure(db_session, factory, today=today)
    db_session.expire_all()
    return result


def _count(db_session, model, *where) -> int:
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def test_nothing_is_deleted_on_the_archives_last_day(client, export_store, engine, db_session):
    dealership_id, _, customer, last_day = _offboarded(client, engine)

    assert _run(engine, db_session, last_day) == []

    row = db_session.get(Dealership, uuid.UUID(dealership_id))
    assert row.erased_at is None
    assert db_session.get(Customer, uuid.UUID(customer["id"])) is not None
    assert _count(db_session, Location, Location.tenant_id == row.id) == 1


def test_the_day_after_everything_goes_but_the_tombstone_and_the_users(client, export_store, engine, db_session):
    dealership_id, manager, customer, last_day = _offboarded(client, engine)
    tenant = uuid.UUID(dealership_id)
    export_keys = [job.object_key for job in db_session.scalars(select(ExportJob)) if job.object_key]
    assert export_keys and all(export_store.get(key) is not None for key in export_keys)

    assert _run(engine, db_session, last_day + dt.timedelta(days=1)) == []

    row = db_session.get(Dealership, tenant)
    assert row.erased_at is not None
    assert [entry["context"] for entry in row.erasure_progress][-1] == "platform"
    # A group of one: its customers go.
    assert db_session.get(Customer, uuid.UUID(customer["id"])) is None
    assert _count(db_session, CustomerEmail, CustomerEmail.customer_id == uuid.UUID(customer["id"])) == 0
    assert _count(db_session, Location, Location.tenant_id == tenant) == 0
    assert _count(db_session, ExportJob, ExportJob.dealership_id == tenant) == 0
    assert all(export_store.get(key) is None for key in export_keys)
    # A-RULE-5: the user stays, minimised.
    user = db_session.get(User, uuid.UUID(manager["id"]))
    assert user is not None and user.first_name == "" and user.last_name == ""
    assert user.email == f"{user.id}@erased.invalid"
    # ADR-121: the audit rows stay, their values redacted.
    create = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == tenant, AuditEvent.entity_id == tenant, AuditEvent.action == "create"
        )
    )
    assert create is not None and set(create.after.values()) <= {REDACTED, None}
    # The deletion's own row, at platform level.
    erased = db_session.scalar(select(AuditEvent).where(AuditEvent.entity_id == tenant, AuditEvent.action == "erased"))
    assert erased.tenant_id is None
    assert erased.after["contexts"]["customer"] >= 2
    # Run again: nothing left to do, no second row.
    assert _run(engine, db_session, last_day + dt.timedelta(days=2)) == []
    assert _count(db_session, AuditEvent, AuditEvent.entity_id == tenant, AuditEvent.action == "erased") == 1


def test_a_failed_run_resumes_where_it_stopped(client, export_store, engine, db_session, monkeypatch):
    dealership_id, _, customer, last_day = _offboarded(client, engine)
    tenant = uuid.UUID(dealership_id)
    exporters = {exporter.context: exporter for exporter in core_export.exporters()}
    calls: list[str] = []
    original = type(exporters["customer"]).erase

    def failing_once(self, session_factory, scope):
        calls.append("customer")
        if len(calls) == 1:
            raise RuntimeError("database went away")
        return original(self, session_factory, scope)

    monkeypatch.setattr(type(exporters["customer"]), "erase", failing_once)

    assert _run(engine, db_session, last_day + dt.timedelta(days=1)) == [tenant]
    row = db_session.get(Dealership, tenant)
    done_first = [entry["context"] for entry in row.erasure_progress]
    assert row.erased_at is None
    assert "sales" in done_first and "customer" not in done_first
    assert db_session.get(Customer, uuid.UUID(customer["id"])) is not None

    assert _run(engine, db_session, last_day + dt.timedelta(days=1)) == []
    row = db_session.get(Dealership, tenant)
    done = [entry["context"] for entry in row.erasure_progress]
    assert row.erased_at is not None
    assert done[: len(done_first)] == done_first  # nothing done twice
    assert len(done) == len(set(done))
    assert calls == ["customer", "customer"]


def test_the_daily_job_raises_while_a_betrieb_is_unfinished(client, export_store, engine, db_session, monkeypatch):
    from app.platform import daily_jobs

    dealership_id, _, _, _ = _offboarded(client, engine)
    monkeypatch.setattr(offboarding, "run_erasure", lambda db, factory, today: [uuid.UUID(dealership_id)])
    with pytest.raises(RuntimeError):
        daily_jobs.run_daily_dealership_erasure(db_session, session_factory=sessionmaker(bind=engine))


def test_group_owned_customers_stay_while_a_sister_holds_data(client, export_store, engine, db_session):
    sister_id, _, sister_headers = betrieb_with_manager(client, "Garage Schwester AG")
    dealership_id, _, customer, last_day = _offboarded(client, engine, legal_name="Garage Heim AG", group_of=sister_id)
    sisters_customer = _customer(client, sister_headers)

    assert _run(engine, db_session, last_day + dt.timedelta(days=1)) == []

    assert db_session.get(Dealership, uuid.UUID(dealership_id)).erased_at is not None
    assert db_session.get(Customer, uuid.UUID(customer["id"])) is not None
    assert db_session.get(Customer, uuid.UUID(sisters_customer["id"])) is not None
    assert _count(db_session, Location, Location.tenant_id == uuid.UUID(dealership_id)) == 0
