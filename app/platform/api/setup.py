"""Setup checklist, «Betrieb aktivieren» and the legal documents (KAN-214;
Dealer Administration D-A-21, FR-A-26, FR-A-34, FR-A-39, D-A-34).

Dealer side, nested under `/dealerships/{dealership_id}` (tenant from the
token, 404 for another Betrieb's id):

- `GET …/setup-checklist` — the derived checklist (manager, matrix row
  `setup_checklist`), with the legal step and whether activation may go.
- `GET/POST …/legal-acceptances` — the manager accepts the current ToS / DPA.
- `POST …/activation` — «Betrieb aktivieren», If-Match on the dealership.

The writes are the **manager's own act** for their Betrieb: platform staff
read the checklist but neither accept a document nor activate in a dealer's
name (403 for anyone but a manager of the Betrieb).

`GET /legal-documents/current` and `GET /legal-documents/{id}` serve the texts
to accept. Platform side: `GET/POST /platform/legal-documents` — staff list
and publish versions (a new version never edits an old one).
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role, require_mfa_session
from app.core.concurrency import check_version, require_if_match
from app.core.errors import ForbiddenError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.api.auth import client_ip
from app.platform.models.legal import LegalAcceptance
from app.platform.organisation.models.dealership import Dealership
from app.platform.organisation.services import number_range as number_range_service
from app.platform.organisation.services.dealership import get_dealership_for_update_or_404, get_dealership_or_404
from app.platform.schemas.setup import (
    ActivationRead,
    CurrentLegalDocuments,
    LegalAcceptanceList,
    LegalAcceptanceRead,
    LegalAcceptanceRequest,
    LegalDocumentList,
    LegalDocumentPublish,
    LegalDocumentRead,
    SetupChecklistItemRead,
    SetupChecklistRead,
    acceptance_due,
    document_read,
)
from app.platform.services import activation as activation_service
from app.platform.services import legal_documents, setup_checklist

router = APIRouter(tags=["setup-checklist"], route_class=IdempotentRoute)

_NOT_THE_MANAGER = "Only a manager of this Betrieb does this."


def _require_manager_of(dealership_id: uuid.UUID, principal: Principal) -> None:
    require_tenant_match(dealership_id, principal)
    if AccessRole.PLATFORM_ADMIN in principal.roles or not principal.is_dealer_manager:
        raise ForbiddenError(_NOT_THE_MANAGER, details={"reason": "manager_only"})


def _acceptance_read(row: LegalAcceptance) -> LegalAcceptanceRead:
    return LegalAcceptanceRead(
        id=row.id,
        legal_document_id=row.legal_document_id,
        document_type=row.document_type,
        version=row.version,
        language=row.language,
        user_id=row.user_id,
        accepted_at=row.accepted_at,
    )


def _ui_language(request: Request) -> str | None:
    header = request.headers.get("accept-language", "")
    return header.split(",", 1)[0].split("-", 1)[0].strip().lower() or None


def _checklist_read(db: Session, dealership: Dealership, *, language: str | None) -> SetupChecklistRead:
    checklist = setup_checklist.derive(db, dealership)
    versions = legal_documents.current_versions(db)
    state = legal_documents.acceptance_state(db, dealership)
    final = not legal_documents.drafts_block_activation(versions)
    confirmation = number_range_service.latest_confirmation(db, dealership.id)
    return SetupChecklistRead(
        dealership_id=dealership.id,
        status=dealership.status,
        complete=checklist.complete,
        items=[
            SetupChecklistItemRead(code=item.code, complete=item.complete, blocks=item.blocks, link=item.link)
            for item in checklist.items
        ],
        legal_acceptance=acceptance_due(state, language),
        legal_documents_final=final,
        may_activate=checklist.complete and state.complete_set and not state.is_due and final,
        number_ranges_confirmed_at=confirmation.confirmed_at if confirmation else None,
        version=dealership.version,
    )


@router.get("/dealerships/{dealership_id}/setup-checklist", response_model=SetupChecklistRead)
def get_setup_checklist(
    dealership_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_read("setup_checklist")),
    db: Session = Depends(get_db),
):
    """The Betrieb's checklist, derived on read (D-A-21): the items that apply
    to its entitlements, each complete or not, what it blocks and where to
    fix it — the manager's first screen until activation."""

    require_tenant_match(dealership_id, principal)
    dealership = get_dealership_or_404(db, dealership_id)
    return _checklist_read(db, dealership, language=_ui_language(request))


@router.get("/legal-documents/current", response_model=CurrentLegalDocuments)
def get_current_legal_documents(
    language: str | None = None,
    principal: Principal = Depends(require_read("legal_acceptance")),
    db: Session = Depends(get_db),
):
    """The current ToS and DPA, in `language` when published in it (else
    German, else the first language published)."""

    versions = legal_documents.current_versions(db)
    return CurrentLegalDocuments(
        items=[
            document_read(current.in_language(language))
            for _, current in sorted(versions.items(), key=lambda kv: kv[0].value)
        ]
    )


@router.get("/legal-documents/{document_id}", response_model=LegalDocumentRead)
def get_legal_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(require_read("legal_acceptance")),
    db: Session = Depends(get_db),
):
    return document_read(legal_documents.get_document_or_404(db, document_id))


@router.get("/dealerships/{dealership_id}/legal-acceptances", response_model=LegalAcceptanceList)
def list_legal_acceptances(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("legal_acceptance")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    get_dealership_or_404(db, dealership_id)
    return LegalAcceptanceList(
        items=[_acceptance_read(row) for row in legal_documents.list_acceptances(db, dealership_id)]
    )


@router.post("/dealerships/{dealership_id}/legal-acceptances", response_model=LegalAcceptanceRead, status_code=201)
def accept_legal_document(
    dealership_id: uuid.UUID,
    body: LegalAcceptanceRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The manager accepts the current version of the ToS or the DPA for the
    Betrieb (D-A-34): an append-only record with the user, the time, the
    address and the browser. 409 `legal_document_not_current` for any other
    version. Accepting it again returns the first record."""

    _require_manager_of(dealership_id, principal)
    dealership = get_dealership_or_404(db, dealership_id)
    document = legal_documents.get_document_or_404(db, body.legal_document_id)
    row = legal_documents.accept(
        db,
        dealership=dealership,
        document=document,
        user_id=principal.user_id,
        ip_address=client_ip(request) or None,
        user_agent=request.headers.get("user-agent") or None,
    )
    db.commit()
    return _acceptance_read(row)


@router.post("/dealerships/{dealership_id}/activation", response_model=ActivationRead)
def activate_dealership(
    dealership_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """«Betrieb aktivieren» (FR-A-34): the manager, on a complete checklist
    and the current ToS and DPA accepted. Freezes every number range, sets
    `active`, audits and publishes `platform.dealership.activated` — one
    transaction. 409 with `details.reason` when anything is missing
    (`services/activation.py`). The session's own claims are unchanged: the
    gate reads the status from the database on every request."""

    _require_manager_of(dealership_id, principal)
    dealership = get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    activation_service.activate(db, dealership=dealership, actor_id=principal.user_id)
    db.commit()
    db.refresh(dealership)
    return ActivationRead(
        dealership_id=dealership.id,
        status=dealership.status,
        activated_at=dealership.activated_at,
        version=dealership.version,
    )


# --- Platform Administration › Legal documents ------------------------------------------------


@router.get("/platform/legal-documents", response_model=LegalDocumentList)
def list_legal_documents(
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    return LegalDocumentList(items=[document_read(row) for row in legal_documents.list_documents(db)])


@router.post(
    "/platform/legal-documents",
    response_model=LegalDocumentRead,
    status_code=201,
    dependencies=[Depends(require_mfa_session())],
)
def publish_legal_document(
    body: LegalDocumentPublish,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """Publish a version of the ToS or the DPA in one language (FR-A-39).
    Staff with a second factor in the session. Versions only move forward;
    a published text is never edited."""

    row = legal_documents.publish(
        db,
        document_type=body.document_type,
        version=body.version,
        language=body.language,
        effective_date=body.effective_date,
        title=body.title,
        body_markdown=body.body_markdown,
        acceptance_grace_days=body.acceptance_grace_days,
        actor_id=principal.user_id,
    )
    db.commit()
    db.refresh(row)
    return document_read(row)
