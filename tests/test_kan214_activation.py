"""KAN-214 (FR-A-34, D-A-21, A-RULE-3; Anto, 2026-11-13, Q3 A, Q4 A):
«Betrieb aktivieren» — refused for each missing item and for missing or
draft legal documents; on success one transaction freezes every number
range, sets `active`, audits and publishes `platform.dealership.activated`."""

import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.deployment_environment import DeploymentEnvironment
from app.core.outbox_model import OutboxMessage
from app.platform.models.legal import LegalDocumentType
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.models.number_range import NumberRange
from app.platform.services import legal_documents
from tests import setup_checklist_support as s
from tests.identity_support import platform_admin_headers


@pytest.fixture()
def ready(client, db_session):
    """A Betrieb with every item complete and both documents accepted."""

    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    s.complete_checklist(db_session, dealership_id, boss)
    documents = s.publish_both(db_session)
    s.accept_both(client, dealership_id, documents, s.headers_for(boss, dealership_id))
    return dealership_id, boss


def _activate(client, dealership_id, headers, version=None):
    if version is None:
        version = client.get(f"/v1/dealerships/{dealership_id}/setup-checklist", headers=headers).json()["version"]
    return client.post(
        f"/v1/dealerships/{dealership_id}/activation",
        headers={**headers, "If-Match": str(version), "Idempotency-Key": str(uuid.uuid4())},
    )


def test_activation_freezes_the_ranges_sets_active_audits_and_publishes(client, db_session, ready):
    dealership_id, boss = ready
    headers = s.headers_for(boss, dealership_id)
    checklist = client.get(f"/v1/dealerships/{dealership_id}/setup-checklist", headers=headers).json()
    assert checklist["mayActivate"] is True
    assert checklist["legalAcceptance"]["documents"] == []

    response = _activate(client, dealership_id, headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"

    db_session.expire_all()
    dealership = db_session.get(Dealership, dealership_id)
    assert dealership.status == DealershipStatus.ACTIVE
    assert dealership.activated_by == uuid.UUID(boss["id"])
    ranges = db_session.scalars(select(NumberRange).where(NumberRange.tenant_id == dealership_id)).all()
    assert ranges and all(row.frozen_at is not None for row in ranges)
    events = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "platform.dealership.activated")
    ).all()
    assert len(events) == 1
    assert events[0].payload["dealershipId"] == str(dealership_id)
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == dealership_id, AuditEvent.action == "activate")
    ).all()
    assert len(audit) == 1 and audit[0].actor_id == uuid.UUID(boss["id"])
    frozen_audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "number_range_frozen", AuditEvent.reason == "activation")
    ).all()
    assert len(frozen_audit) == len(ranges)


def test_activation_needs_if_match(client, db_session, ready):
    dealership_id, boss = ready
    headers = s.headers_for(boss, dealership_id)
    stale = _activate(client, dealership_id, headers, version=999)
    assert stale.status_code == 409
    missing = client.post(f"/v1/dealerships/{dealership_id}/activation", headers=headers)
    assert missing.status_code == 400  # If-Match is required (require_if_match)


@pytest.mark.parametrize(
    "left_open",
    ["branding", "vat_and_rounding", "bank_account", "number_ranges", "location_opening_hours"],
)
def test_activation_is_refused_for_each_missing_item(client, db_session, left_open):
    """Q4 A: no exception — every applicable item, whatever the go-live date."""

    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    steps = {
        "branding": lambda: s.complete_branding(db_session, dealership_id),
        "vat_and_rounding": lambda: s.complete_vat(db_session, dealership_id),
        "bank_account": lambda: s.complete_bank_account(db_session, dealership_id),
        "number_ranges": lambda: s.complete_number_ranges(db_session, dealership_id, uuid.UUID(boss["id"])),
        "location_opening_hours": lambda: s.complete_opening_hours(db_session, dealership_id),
    }
    for code, step in steps.items():
        if code != left_open:
            step()
    documents = s.publish_both(db_session)
    s.accept_both(client, dealership_id, documents, s.headers_for(boss, dealership_id))

    response = _activate(client, dealership_id, s.headers_for(boss, dealership_id))
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert details == {"reason": "setup_checklist_incomplete", "missing": [left_open]}
    db_session.expire_all()
    assert db_session.get(Dealership, dealership_id).status == DealershipStatus.PENDING_ONBOARDING


def test_activation_is_refused_without_the_current_acceptances(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    s.complete_checklist(db_session, dealership_id, boss)
    headers = s.headers_for(boss, dealership_id)

    none_published = _activate(client, dealership_id, headers)
    assert none_published.json()["error"]["details"]["reason"] == "legal_documents_unavailable"

    documents = s.publish_both(db_session)
    s.accept_both(client, dealership_id, {LegalDocumentType.TOS: documents[LegalDocumentType.TOS]}, headers)
    response = _activate(client, dealership_id, headers)
    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"reason": "legal_acceptance_missing", "documents": ["dpa"]}


def test_production_refuses_activation_on_the_draft_texts(client, db_session, ready, monkeypatch):
    """Q3 A: 0.9 «Entwurf» activates on staging only."""

    dealership_id, boss = ready
    monkeypatch.setattr(legal_documents, "current_deployment_environment", lambda: DeploymentEnvironment.PRODUCTION)
    headers = s.headers_for(boss, dealership_id)
    assert client.get(f"/v1/dealerships/{dealership_id}/setup-checklist", headers=headers).json()["mayActivate"] is False
    response = _activate(client, dealership_id, headers)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "legal_documents_not_final"


def test_only_the_manager_activates(client, db_session, ready):
    dealership_id, _ = ready
    clerk = s.manager(client, db_session, dealership_id, isDealerManager=False)
    version = db_session.get(Dealership, dealership_id).version
    clerk_headers = s.headers_for(clerk, dealership_id, manager=False)
    assert _activate(client, dealership_id, clerk_headers, version=version).status_code == 403
    staff = platform_admin_headers()
    assert _activate(client, dealership_id, staff, version=version).status_code == 403
    other = s.pending_dealership(client, db_session)
    stranger = s.manager(client, db_session, other)
    assert _activate(client, dealership_id, s.headers_for(stranger, other), version=version).status_code == 404


def test_an_active_betrieb_is_not_activated_again(client, db_session, ready):
    dealership_id, boss = ready
    headers = s.headers_for(boss, dealership_id)
    assert _activate(client, dealership_id, headers).status_code == 200
    again = _activate(client, dealership_id, headers)
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "dealership_not_pending"


def test_the_patch_still_refuses_to_activate(client, db_session, ready):
    """KAN-712: activation is the procedure, never a PATCH of `status`."""

    dealership_id, _ = ready
    version = db_session.get(Dealership, dealership_id).version
    response = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"status": "active"},
        headers={**platform_admin_headers(), "If-Match": str(version)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "use_activation_procedure"
