"""KAN-214 (FR-A-39, D-A-34; Anto, 2026-11-13, Q1 A, Q3 A): versioned legal
documents, the append-only acceptance, the 30-day grace for a new version
with a moved clock, and production's refusal of a draft."""

import datetime as dt
import uuid

import pytest

from app.core.deployment_environment import DeploymentEnvironment
from app.core.i18n import SwissLanguage
from app.platform.models.legal import LegalAcceptance, LegalAcceptanceIsAppendOnly, LegalDocumentType
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.services import legal_documents
from tests import setup_checklist_support as s
from tests.identity_support import platform_admin_headers


def test_versions_compare_number_by_number():
    assert legal_documents.version_key("0.10") > legal_documents.version_key("0.9")
    assert legal_documents.version_key("1") == legal_documents.version_key("1.0")
    assert legal_documents.is_draft("0.9") and not legal_documents.is_draft("1.0")


def test_the_current_version_is_the_highest_effective_one(db_session):
    s.publish_document(db_session, LegalDocumentType.TOS, "0.9", effective_date=dt.date(2026, 1, 1))
    s.publish_document(db_session, LegalDocumentType.TOS, "1.0", effective_date=dt.date(2026, 6, 1))
    s.publish_document(db_session, LegalDocumentType.TOS, "1.0", language=SwissLanguage.FR, effective_date=dt.date(2026, 6, 1))

    before = legal_documents.current_versions(db_session, today=dt.date(2026, 5, 31))
    assert before[LegalDocumentType.TOS].version == "0.9"
    after = legal_documents.current_versions(db_session, today=dt.date(2026, 6, 1))
    assert after[LegalDocumentType.TOS].version == "1.0"
    assert after[LegalDocumentType.TOS].in_language("fr").language == SwissLanguage.FR
    assert after[LegalDocumentType.TOS].in_language("it").language == SwissLanguage.DE  # German, the fallback
    assert LegalDocumentType.DPA not in after


def test_staff_publish_a_version_and_versions_only_move_forward(client, db_session):
    body = {
        "documentType": "dpa",
        "version": "1.0",
        "language": "de",
        "effectiveDate": "2026-12-01",
        "title": "Auftragsverarbeitungsvertrag",
        "bodyMarkdown": "## 1. Gegenstand\n\nText.",
    }
    created = client.post("/v1/platform/legal-documents", json=body, headers=platform_admin_headers())
    assert created.status_code == 201, created.text
    assert created.json()["acceptanceGraceDays"] == 30
    assert created.json()["draft"] is False

    again = client.post("/v1/platform/legal-documents", json=body, headers=platform_admin_headers())
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "legal_document_version_exists"

    older = client.post("/v1/platform/legal-documents", json={**body, "version": "0.9"}, headers=platform_admin_headers())
    assert older.status_code == 409
    assert older.json()["error"]["details"]["reason"] == "legal_document_version_not_newer"

    bad = client.post("/v1/platform/legal-documents", json={**body, "version": "v2"}, headers=platform_admin_headers())
    assert bad.status_code == 422

    listed = client.get("/v1/platform/legal-documents", headers=platform_admin_headers())
    assert [row["version"] for row in listed.json()["items"]] == ["1.0"]


def test_publishing_needs_a_staff_session_with_a_factor(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    body = {
        "documentType": "tos",
        "version": "1.0",
        "language": "de",
        "effectiveDate": "2026-12-01",
        "title": "AGB",
        "bodyMarkdown": "Text.",
    }
    assert client.post("/v1/platform/legal-documents", json=body, headers=s.headers_for(boss, dealership_id)).status_code == 403
    no_factor = client.post("/v1/platform/legal-documents", json=body, headers=platform_admin_headers(mfa=False))
    assert no_factor.status_code == 403


def test_the_manager_accepts_only_the_current_version(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    headers = {**s.headers_for(boss, dealership_id), "User-Agent": "pytest-browser", "X-Forwarded-For": "203.0.113.7"}
    old = s.publish_document(db_session, LegalDocumentType.TOS, "0.9", effective_date=dt.date(2026, 1, 1))
    current = s.publish_document(db_session, LegalDocumentType.TOS, "1.0", effective_date=dt.date(2026, 2, 1))

    refused = client.post(
        f"/v1/dealerships/{dealership_id}/legal-acceptances", json={"legalDocumentId": str(old.id)}, headers=headers
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["reason"] == "legal_document_not_current"

    accepted = client.post(
        f"/v1/dealerships/{dealership_id}/legal-acceptances", json={"legalDocumentId": str(current.id)}, headers=headers
    )
    assert accepted.status_code == 201, accepted.text
    row = db_session.get(LegalAcceptance, uuid.UUID(accepted.json()["id"]))
    assert (row.version, row.user_id, row.ip_address, row.user_agent) == (
        "1.0",
        uuid.UUID(boss["id"]),
        "203.0.113.7",
        "pytest-browser",
    )
    twice = client.post(
        f"/v1/dealerships/{dealership_id}/legal-acceptances", json={"legalDocumentId": str(current.id)}, headers=headers
    )
    assert twice.json()["id"] == accepted.json()["id"]

    listed = client.get(f"/v1/dealerships/{dealership_id}/legal-acceptances", headers=headers)
    assert [item["version"] for item in listed.json()["items"]] == ["1.0"]


def test_only_a_manager_accepts_never_a_colleague_or_staff(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    clerk = s.manager(client, db_session, dealership_id, isDealerManager=False)
    document = s.publish_document(db_session, LegalDocumentType.TOS)
    url = f"/v1/dealerships/{dealership_id}/legal-acceptances"
    body = {"legalDocumentId": str(document.id)}
    assert client.post(url, json=body, headers=s.headers_for(clerk, dealership_id, manager=False)).status_code == 403
    assert client.post(url, json=body, headers=platform_admin_headers()).status_code == 403


def test_an_acceptance_is_append_only(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    documents = s.publish_both(db_session)
    s.accept_both(client, dealership_id, documents, s.headers_for(boss, dealership_id))
    row = db_session.query(LegalAcceptance).first()

    row.version = "2.0"
    with pytest.raises(LegalAcceptanceIsAppendOnly):
        db_session.commit()
    db_session.rollback()

    db_session.delete(db_session.query(LegalAcceptance).first())
    with pytest.raises(LegalAcceptanceIsAppendOnly):
        db_session.commit()
    db_session.rollback()
    assert db_session.query(LegalAcceptance).count() == 2


def _active(db_session, dealership_id) -> Dealership:
    dealership = db_session.get(Dealership, dealership_id)
    dealership.status = DealershipStatus.ACTIVE
    db_session.commit()
    return dealership


def test_a_new_version_is_due_for_its_grace_and_then_holds_the_manager(client, db_session):
    """Q1 A: prompted at every login for 30 days, then held."""

    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    first = s.publish_both(db_session, "1.0", effective_date=dt.date(2026, 1, 1))
    s.accept_both(client, dealership_id, first, s.headers_for(boss, dealership_id))
    dealership = _active(db_session, dealership_id)
    assert not legal_documents.acceptance_state(db_session, dealership, today=dt.date(2026, 3, 1)).is_due

    s.publish_document(db_session, LegalDocumentType.DPA, "1.1", effective_date=dt.date(2026, 3, 1))
    on_day_one = legal_documents.acceptance_state(db_session, dealership, today=dt.date(2026, 3, 1))
    assert [current.version for current in on_day_one.due] == ["1.1"]
    assert on_day_one.due_by == dt.date(2026, 3, 31)
    assert not on_day_one.held
    assert not legal_documents.acceptance_state(db_session, dealership, today=dt.date(2026, 3, 31)).held
    assert legal_documents.acceptance_state(db_session, dealership, today=dt.date(2026, 4, 1)).held


def test_a_betrieb_being_set_up_is_never_held(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    s.publish_both(db_session, "1.0", effective_date=dt.date(2026, 1, 1))
    dealership = db_session.get(Dealership, dealership_id)
    state = legal_documents.acceptance_state(db_session, dealership, today=dt.date(2027, 1, 1))
    assert state.is_due and not state.held


def test_production_refuses_a_draft(monkeypatch, db_session):
    s.publish_both(db_session, "0.9")
    versions = legal_documents.current_versions(db_session)
    assert not legal_documents.drafts_block_activation(versions)  # not production
    monkeypatch.setattr(legal_documents, "current_deployment_environment", lambda: DeploymentEnvironment.PRODUCTION)
    assert legal_documents.drafts_block_activation(versions)
    s.publish_both(db_session, "1.0")
    assert not legal_documents.drafts_block_activation(legal_documents.current_versions(db_session))


def test_the_current_documents_endpoint_serves_the_language_asked(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    s.publish_both(db_session, "0.9")
    s.publish_both(db_session, "0.9", language=SwissLanguage.FR)
    response = client.get("/v1/legal-documents/current?language=fr", headers=s.headers_for(boss, dealership_id))
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(i["documentType"], i["language"], i["draft"]) for i in items] == [("dpa", "fr", True), ("tos", "fr", True)]
    assert items[0]["bodyMarkdown"]
