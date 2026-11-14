"""KAN-218 — the data export job (Dealer Administration D-A-31, FR-A-44;
Anto, 2026-11-13: Q1 A seven-day download, Q4 A audit log and uploaded
files included).

The manager requests an export at any time; the worker produces one ZIP —
CSV and JSON per entity, `schema.json`, every issued document as PDF with its
number and date, the Betrieb's audit log and its uploaded files — downloadable
through the authenticated GET for seven days, then deleted by the daily job.
Group-owned customers come for the requesting Betrieb's own relationship only
(ADR-014). Every step is audited.
"""

import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.base import utcnow
from app.customer.models.customer import Customer
from app.platform.models.export_job import ExportJob, ExportJobStatus
from app.platform.services import data_export
from app.sales.models.document import DocumentOwnerType, SalesDocument
from app.sales.services.offer import create_offer
from tests.data_export_support import (
    acknowledge,
    betrieb_with_manager,
    download,
    export_store_fixture,  # noqa: F401 - registers the export_store fixture
    get_export,
    idem,
    produce,
    request_export,
    rows_in,
    staff,
)
from tests.membership_support import bearer, token


def _customer(client, headers, **fields) -> dict:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
        **fields,
    }
    response = client.post("/v1/customers", json=payload, headers=idem(headers))
    assert response.status_code == 201, response.text
    return response.json()


# --- who may export ------------------------------------------------------------------


def test_the_manager_requests_an_export_and_it_is_audited(client, export_store, db_session):
    dealership_id, manager, headers = betrieb_with_manager(client)

    job = request_export(client, dealership_id, headers)

    assert job["status"] == "requested"
    assert job["kind"] == "self_service"
    assert job["requestedBy"] == manager["id"]
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(job["id"]))).all()
    assert [(row.action, row.actor_id) for row in audit] == [("requested", uuid.UUID(manager["id"]))]


def test_a_role_without_the_data_export_row_is_refused(client, export_store):
    dealership_id, _, _ = betrieb_with_manager(client)
    sales = bearer(token(AccessRole.SALES, tenant_id=dealership_id))

    assert client.post(f"/v1/dealerships/{dealership_id}/exports", headers=idem(sales)).status_code == 403
    assert client.get(f"/v1/dealerships/{dealership_id}/exports", headers=sales).status_code == 403


def test_another_betriebs_export_is_404_never_403(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    other_id, _, other_headers = betrieb_with_manager(client, "Garage Anders AG")
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])

    assert client.post(f"/v1/dealerships/{dealership_id}/exports", headers=idem(other_headers)).status_code == 404
    assert client.get(f"/v1/dealerships/{dealership_id}/exports", headers=other_headers).status_code == 404
    assert client.get(f"/v1/dealerships/{other_id}/exports/{job['id']}", headers=other_headers).status_code == 404
    assert (
        client.get(f"/v1/dealerships/{other_id}/exports/{job['id']}/download", headers=other_headers).status_code == 404
    )


def test_staff_do_not_use_the_betriebs_own_export(client, export_store):
    dealership_id, _, _ = betrieb_with_manager(client)

    response = client.post(f"/v1/dealerships/{dealership_id}/exports", headers=idem(staff()))

    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "staff_use_archive_export"


# --- the lifecycle ---------------------------------------------------------------------


def test_requested_then_produced_downloaded_and_acknowledged(client, export_store, engine, db_session):
    dealership_id, manager, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    not_yet = client.get(f"/v1/dealerships/{dealership_id}/exports/{job['id']}/download", headers=headers)
    assert not_yet.status_code == 409
    assert not_yet.json()["error"]["details"]["reason"] == "export_requested"

    produce(engine, job["id"])

    ready = get_export(client, dealership_id, job["id"], headers)
    assert ready["status"] == "ready"
    produced_at = dt.datetime.fromisoformat(ready["producedAt"])
    expires_at = dt.datetime.fromisoformat(ready["downloadExpiresAt"])
    assert dt.timedelta(days=6, hours=23) < expires_at - produced_at <= dt.timedelta(days=7, minutes=1)
    archive = download(client, dealership_id, job["id"], headers)
    schema = json.loads(archive.read("schema.json"))
    assert schema["dealershipId"] == dealership_id
    assert {"context": "platform", "entity": "dealership"} in [
        {"context": e["context"], "entity": e["entity"]} for e in schema["entities"]
    ]
    assert [row["id"] for row in rows_in(archive, "platform", "dealership")] == [dealership_id]
    assert "data/platform/dealership.csv" in archive.namelist()

    stale = acknowledge(client, dealership_id, {**ready, "version": ready["version"] - 1}, headers)
    assert stale.status_code == 409  # If-Match
    acknowledged = acknowledge(client, dealership_id, ready, headers)
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["acknowledgedBy"] == manager["id"]
    again = acknowledge(client, dealership_id, acknowledged.json(), headers)
    assert again.json()["error"]["details"]["reason"] == "export_already_acknowledged"

    actions = [
        row.action
        for row in db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(job["id"])).order_by(AuditEvent.created_at)
        )
    ]
    assert actions == ["requested", "produced", "downloaded", "acknowledged"]


def test_a_redelivered_production_changes_nothing(client, export_store, engine, db_session):
    dealership_id, _, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])
    first = get_export(client, dealership_id, job["id"], headers)

    produce(engine, job["id"])

    assert get_export(client, dealership_id, job["id"], headers) == first


def test_a_failed_production_is_recorded_never_offered(client, export_store, engine, monkeypatch):
    dealership_id, _, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(data_export, "write_archive", broken)
    produce(engine, job["id"])

    failed = get_export(client, dealership_id, job["id"], headers)
    assert failed["status"] == "failed"
    assert failed["failureReason"] == "production_failed"
    response = client.get(f"/v1/dealerships/{dealership_id}/exports/{job['id']}/download", headers=headers)
    assert response.status_code == 409


# --- Q1 A: seven days, then the file is deleted ------------------------------------


def test_the_file_is_there_on_day_six_and_gone_on_day_eight(client, export_store, engine, db_session):
    dealership_id, _, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])
    row = db_session.get(ExportJob, uuid.UUID(job["id"]))
    key = row.object_key
    assert export_store.get(key) is not None

    assert data_export.expire_downloads(db_session, now=utcnow() + dt.timedelta(days=6)) == 0
    assert export_store.get(key) is not None

    assert data_export.expire_downloads(db_session, now=utcnow() + dt.timedelta(days=8)) == 1

    assert export_store.get(key) is None
    db_session.expire_all()
    expired = db_session.get(ExportJob, uuid.UUID(job["id"]))
    assert expired.status == ExportJobStatus.EXPIRED
    assert expired.object_key is None
    response = client.get(f"/v1/dealerships/{dealership_id}/exports/{job['id']}/download", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "export_expired"
    # A new export can be requested at any time.
    assert request_export(client, dealership_id, headers)["status"] == "requested"


def test_the_worker_registers_the_expiry_job_and_no_deletion_job():
    """Anto, 2026-11-14: nothing deletes an offboarded Betrieb automatically —
    the deletion after ten years is a manual staff step, reported nightly."""

    from app.core.daily_scheduler import registered_job_names
    from app.worker import register_daily_jobs

    register_daily_jobs()

    names = registered_job_names()
    assert "platform.data_exports.expire" in names
    assert "platform.dealership_erasure" not in names
    assert not [name for name in names if "erasure" in name]


# --- what the file holds -------------------------------------------------------------


def _issue_documents(engine, dealership_id: str, count: int) -> list[str]:
    """Issued offer documents, as the generation stores them (content frozen)."""

    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        offer = create_offer(session, tenant_id=uuid.UUID(dealership_id), actor_id=uuid.uuid4())
        for version in range(1, count + 1):
            session.add(
                SalesDocument(
                    tenant_id=offer.tenant_id,
                    owner_type=DocumentOwnerType.OFFER,
                    owner_id=offer.id,
                    version=version,
                    correspondence_language="de",
                    content_definition={"title": f"Offerte {version}"},
                    rendered_at=utcnow(),
                )
            )
        session.commit()
        return [offer.offer_number] * count
    finally:
        session.close()


def test_every_issued_document_is_a_pdf_with_its_number_and_date(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    numbers = _issue_documents(engine, dealership_id, 2)
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])

    archive = download(client, dealership_id, job["id"], headers)

    pdfs = [name for name in archive.namelist() if name.endswith(".pdf")]
    assert len(pdfs) == 2 == len(rows_in(archive, "sales", "sales_document"))
    for name in pdfs:
        assert archive.read(name).startswith(b"%PDF")
    index = json.loads(archive.read("documents/index.json"))
    assert sorted(entry["number"] for entry in index) == sorted(numbers)
    assert all(entry["issuedAt"] and entry["file"] in pdfs for entry in index)
    assert all(numbers[0].replace("/", "-") in name for name in pdfs)


def test_the_audit_log_and_the_uploaded_files_are_in_it(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    from tests.test_branding import _png  # a valid logo, as the branding tests upload it

    logo = _png(800, 300)
    upload = client.put(
        f"/v1/dealerships/{dealership_id}/branding/assets/primary",
        content=logo,
        headers={**headers, "Content-Type": "image/png", "If-Match": "1"},
    )
    assert upload.status_code == 200, upload.text
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])

    archive = download(client, dealership_id, job["id"], headers)

    audit = rows_in(archive, "core", "audit_event")
    assert {row["tenant_id"] for row in audit} == {dealership_id}
    assert any(row["entity_type"] == "dealership" and row["action"] == "create" for row in audit)
    assert archive.read("files/branding/primary") == logo
    assert json.loads(archive.read("files/index.json"))[0]["file"] == "files/branding/primary"


def test_a_location_logo_and_the_betriebs_logo_of_the_same_kind_are_both_in_it(client, export_store, engine):
    """KAN-206 gave each site its own `primary` logo beside the Betrieb's: both
    leave under their own names, with their own bytes, each listed once."""

    from tests.test_branding import _SVG, _png

    dealership_id, _, headers = betrieb_with_manager(client)
    betrieb_logo = _png(800, 300)
    upload = client.put(
        f"/v1/dealerships/{dealership_id}/branding/assets/primary",
        content=betrieb_logo,
        headers={**headers, "Content-Type": "image/png", "If-Match": "1"},
    )
    assert upload.status_code == 200, upload.text
    (location,) = client.get(f"/v1/dealerships/{dealership_id}/locations", headers=headers).json()["items"]
    site = client.put(
        f"/v1/dealerships/{dealership_id}/branding/locations/{location['id']}/logo",
        content=_SVG,
        headers={**headers, "Content-Type": "image/svg+xml", "If-Match": str(upload.json()["version"])},
    )
    assert site.status_code == 200, site.text
    # The SVG is stored as sanitised; the export carries what is stored.
    site_logo = client.get(f"/v1/dealerships/{dealership_id}/branding/locations/{location['id']}/logo", headers=headers)
    assert site_logo.status_code == 200 and site_logo.content.startswith(b"<?xml")
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])

    archive = download(client, dealership_id, job["id"], headers)

    location_path = f"files/branding/locations/{location['id']}/primary"
    assert archive.read("files/branding/primary") == betrieb_logo
    assert archive.read(location_path) == site_logo.content
    names = archive.namelist()
    assert len(names) == len(set(names))
    listed = [entry["file"] for entry in json.loads(archive.read("files/index.json"))]
    assert sorted(listed) == sorted({"files/branding/primary", location_path})


def test_the_archive_never_holds_one_name_twice(db_session):
    from app.core.export import DuplicateArchiveEntry, ExportFile, ExportScope, TableExporter, write_archive

    class _TwoLogos(TableExporter):
        context = "probe"

        def files(self, db, scope):
            yield ExportFile(path="files/branding/primary", content_type="image/png", data=b"one")
            yield ExportFile(path="files/branding/primary", content_type="image/png", data=b"two")

    scope = ExportScope(dealership_id=uuid.uuid4(), group_id=uuid.uuid4())
    with pytest.raises(DuplicateArchiveEntry, match="files/branding/primary"):
        write_archive(db_session, scope=scope, produced_at=utcnow(), header={}, registered=[_TwoLogos()])


def test_secrets_never_leave_the_database(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])

    archive = download(client, dealership_id, job["id"], headers)

    names = archive.namelist()
    for secret in ("credential", "mfa_totp", "mfa_recovery_code", "auth_token", "session", "integration_secret_ref"):
        assert not any(name.endswith(f"/{secret}.json") for name in names), secret
    excluded = {entry["entity"]: entry["reason"] for entry in json.loads(archive.read("schema.json"))["excluded"]}
    assert excluded.get("credential")


# --- ADR-014: a group-owned customer for the Betrieb's own relationship ------------------


def test_customers_come_for_the_requesting_betriebs_relationship_only(client, export_store, engine, db_session):
    home_id, _, home = betrieb_with_manager(client, "Garage Heim AG")
    _, _, sister = betrieb_with_manager(client, "Garage Schwester AG", group_of=home_id)
    own = _customer(client, home, lastName="Eigen")
    sisters = _customer(client, sister, lastName="Schwester")
    sold_by_us = _customer(client, sister, lastName="Gekauft")
    # The home Betrieb's history names a customer the sister created.
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        offer = create_offer(session, tenant_id=uuid.UUID(home_id), actor_id=uuid.uuid4())
        offer.customer_id = uuid.UUID(sold_by_us["id"])
        session.commit()
    finally:
        session.close()

    job = request_export(client, home_id, home)
    produce(engine, job["id"])
    archive = download(client, home_id, job["id"], home)

    exported = {row["id"] for row in rows_in(archive, "customer", "customer")}
    assert exported == {own["id"], sold_by_us["id"]}
    assert sisters["id"] not in exported
    emails = {row["customer_id"] for row in rows_in(archive, "customer", "customer_email")}
    assert emails == exported


def test_a_group_of_one_exports_every_customer_of_its_group(client, export_store, engine, db_session):
    dealership_id, _, headers = betrieb_with_manager(client)
    created = _customer(client, headers)
    # A customer that predates provenance (no creating Betrieb recorded).
    legacy = db_session.get(Customer, uuid.UUID(_customer(client, headers, lastName="Alt")["id"]))
    legacy.dealership_id = None
    db_session.commit()

    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])
    archive = download(client, dealership_id, job["id"], headers)

    assert {row["id"] for row in rows_in(archive, "customer", "customer")} == {created["id"], str(legacy.id)}
