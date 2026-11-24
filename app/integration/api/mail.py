"""Contact mail's connection routes (KAN-215).

- The Betrieb's shared mailbox — Dealer Administration › Mail (A-RULE-0): read
  by the `sending_identities` readers, connected and disconnected by its
  writers (the manager). The tenant is the token's (`require_tenant_match`,
  404 for another Betrieb).
- The caller's own mailbox — My account › Mein Postfach: every signed-in user,
  for themselves only; the Betrieb is the token's.
- The callback the SPA's `/mail-link/callback` page posts the provider's query
  to. The state names the user and the Betrieb it was started for; anyone else
  gets the same 409 `link_expired`.
"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.errors import ConflictError, NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.impersonation import refuse_under_impersonation
from app.core.permissions import may_write, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.integration.models.mail import MailProviderCode, SendingIdentity, SendingIdentityKind, SendingIdentityStatus
from app.integration.schemas.mail import (
    BusinessLinkCreate,
    BusinessMailboxRead,
    LinkCallback,
    LinkOutcomeRead,
    LinkStartRead,
    MailboxRead,
    MailProviderRead,
    OwnMailboxRead,
    SenderRead,
    UserLinkCreate,
)
from app.integration.services import mail_link, mail_sending
from app.platform.public import is_platform_dealership

router = APIRouter(tags=["mail"], route_class=IdempotentRoute)


def _providers(db: Session) -> list[MailProviderRead]:
    configured = set(mail_link.configured_providers(db))
    return [MailProviderRead(code=code, configured=code in configured) for code in MailProviderCode]


def _read(identity: SendingIdentity, *, status: SendingIdentityStatus | None = None) -> MailboxRead:
    return MailboxRead(
        id=identity.id,
        kind=identity.kind,
        provider=identity.provider_code,
        mailbox_address=identity.mailbox_address,
        status=status or identity.status,
        error_code=identity.error_code,
        connected_at=identity.connected_at,
        version=identity.version,
    )


def _identity_for_update_or_404(
    db: Session, *, identity_id: uuid.UUID, dealership_id: uuid.UUID, kind: SendingIdentityKind,
    owner_user_id: uuid.UUID | None,
) -> SendingIdentity:
    query = (
        select(SendingIdentity)
        .where(
            SendingIdentity.id == identity_id,
            SendingIdentity.tenant_id == dealership_id,
            SendingIdentity.kind == kind,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if owner_user_id is not None:
        query = query.where(SendingIdentity.owner_user_id == owner_user_id)
    identity = db.scalar(query)
    if identity is None:
        raise NotFoundError(f"Mailbox {identity_id} was not found.")
    return identity


# -- the Betrieb's shared mailbox ---------------------------------------------------


def _refuse_platform_tenant(db: Session, dealership_id: uuid.UUID) -> None:
    """404: the platform tenant «Nexotec» is no dealer and sends no contact mail
    (KAN-221, ADR-188) — to this route it is not a Betrieb."""

    if is_platform_dealership(db, dealership_id):
        raise NotFoundError(f"Dealership {dealership_id} was not found.")


@router.get("/dealerships/{dealership_id}/mail/business-mailbox", response_model=BusinessMailboxRead)
def get_business_mailbox(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("sending_identities")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    _refuse_platform_tenant(db, dealership_id)
    identity = mail_link.live_business_identity(db, dealership_id=dealership_id)
    return BusinessMailboxRead(mailbox=_read(identity) if identity else None, providers=_providers(db))


@router.post(
    "/dealerships/{dealership_id}/mail/business-mailbox/links", dependencies=[Depends(refuse_under_impersonation)], response_model=LinkStartRead, status_code=201
)
def start_business_mailbox_link(
    dealership_id: uuid.UUID,
    body: BusinessLinkCreate,
    principal: Principal = Depends(require_write("sending_identities")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    _refuse_platform_tenant(db, dealership_id)
    url = mail_link.start_business_link(
        db,
        dealership_id=dealership_id,
        user_id=principal.user_id,
        provider_code=body.provider,
        mailbox_address=str(body.mailbox_address),
    )
    return LinkStartRead(authorize_url=url)


@router.delete("/dealerships/{dealership_id}/mail/business-mailbox/{mailbox_id}", dependencies=[Depends(refuse_under_impersonation)], status_code=204)
def disconnect_business_mailbox(
    dealership_id: uuid.UUID,
    mailbox_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("sending_identities")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    _refuse_platform_tenant(db, dealership_id)
    identity = _identity_for_update_or_404(
        db, identity_id=mailbox_id, dealership_id=dealership_id, kind=SendingIdentityKind.BUSINESS_MAILBOX,
        owner_user_id=None,
    )
    check_version(identity.version, if_match, entity_name="Mailbox")
    mail_sending.disconnect(db, identity=identity, actor_id=principal.user_id)
    return Response(status_code=204)


# -- the caller's own mailbox --------------------------------------------------------


@router.get("/integrations/mail/own-mailbox", response_model=OwnMailboxRead)
def get_own_mailbox(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    identity = mail_sending.own_identity_state(db, dealership_id=principal.tenant_id, user_id=principal.user_id)
    mailbox = None
    if identity is not None:
        in_error = mail_sending.own_identity_in_error(db, identity)
        mailbox = _read(identity, status=SendingIdentityStatus.ERROR if in_error else identity.status)
    sender = refusal = None
    try:
        resolved = mail_sending.resolve_sender(db, dealership_id=principal.tenant_id, author_user_id=principal.user_id)
        sender = SenderRead(kind=resolved.kind, address=resolved.address)
    except ConflictError as exc:
        refusal = str((exc.details or {}).get("reason"))
    return OwnMailboxRead(mailbox=mailbox, sender=sender, sender_refusal=refusal, providers=_providers(db))


@router.post("/integrations/mail/own-mailbox/links", dependencies=[Depends(refuse_under_impersonation)], response_model=LinkStartRead, status_code=201)
def start_own_mailbox_link(
    body: UserLinkCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    url = mail_link.start_user_link(
        db, dealership_id=principal.tenant_id, user_id=principal.user_id, provider_code=body.provider
    )
    return LinkStartRead(authorize_url=url)


@router.delete("/integrations/mail/own-mailbox/{mailbox_id}", dependencies=[Depends(refuse_under_impersonation)], status_code=204)
def disconnect_own_mailbox(
    mailbox_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    identity = _identity_for_update_or_404(
        db, identity_id=mailbox_id, dealership_id=principal.tenant_id, kind=SendingIdentityKind.USER_MAILBOX,
        owner_user_id=principal.user_id,
    )
    check_version(identity.version, if_match, entity_name="Mailbox")
    mail_sending.disconnect(db, identity=identity, actor_id=principal.user_id)
    return Response(status_code=204)


# -- the provider's answer -------------------------------------------------------------


@router.post("/integrations/mail/link-callback", dependencies=[Depends(refuse_under_impersonation)], response_model=LinkOutcomeRead)
def complete_mailbox_link(
    body: LinkCallback,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    outcome = mail_link.complete_link(
        db,
        dealership_id=principal.tenant_id,
        user_id=principal.user_id,
        state=body.state,
        code=body.code,
        error=body.error,
        admin_consent=body.admin_consent,
        tenant=body.tenant,
        may_connect_business_mailbox=may_write(
            "sending_identities", roles=principal.roles, is_dealer_manager=principal.is_dealer_manager
        ),
    )
    return LinkOutcomeRead(
        kind=outcome.kind, status=outcome.status, redirect_url=outcome.redirect_url, mailbox_id=outcome.identity_id
    )
