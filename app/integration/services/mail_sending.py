"""Whose mailbox a customer mail leaves from, and the send (KAN-215; D-A-28 as
re-cut by Anto on 2026-11-14; A-RULE-9 — no module composes a sender).

**`resolve_sender`** (Anto's D1 A, 2026-11-15): the author's own connected
mailbox in this Betrieb, else the Betrieb's shared mailbox, else a refusal
that links to both connection screens — never a silent fallback, never a
Nexotec sender. An author whose own mailbox is in error is told exactly that
(`own_mailbox_in_error`), not «no mailbox connected», and is not sent from
the shared mailbox instead: they chose their own, and a customer must not get
mail from another address without the author knowing. A grant ended in
platform (the user revoked it in My account, or was deactivated) counts as no
own mailbox.

**`send_customer_mail`** writes exactly one `integration_message_delivery` row
per send on a session of integration's own (F2 of the review; ADR-047): the
caller's session is never written to or committed. Any adapter failure is a
`failed` row with a closed code, and a mailbox's own refusal (consent
withdrawn, token dead) puts its identity in error, so the next resolution says
so. The provider request goes through `call_capability` on Nexotec's
platform-scope app connection: a call-log row per request, and the breaker.

**Disconnecting.** The shared mailbox ends in Nexotec only: the consent stays
with the dealer's tenant until its IT removes Nexotec's app (the screen says
so). An own mailbox ends its grant in platform (`revoke_connected_account`)
unless another live identity of the same user still uses that grant (the same
mailbox connected in a sister Betrieb). Ending it at the provider is the
revoker `end_mail_grant`, which platform's consumer runs after the revoke
committed (ADR-118): it skips Google's revoke while another live identity
holds the same Google account (F3 — Google's revoke ends the client's whole
grant for the account), and always drops the grant's refresh token from the
secrets manager.
"""

import dataclasses
import logging
import uuid
from collections.abc import Callable, Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ConflictError
from app.integration.adapters.mail_base import MAIL_SEND_CAPABILITY, MailAttachment, MailGateway, OutgoingMail
from app.integration.errors import (
    ProviderAccountRefusedError,
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderRequestError,
)
from app.integration.models.mail import (
    DeliveryStatus,
    MailProviderCode,
    MessageDelivery,
    SendingIdentity,
    SendingIdentityKind,
    SendingIdentityStatus,
)
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import mail_link, secrets_backend
from app.integration.services.gateway import call_capability
from app.integration.services.resilience import CircuitOpenError
from app.platform.public import (
    ConnectedAccountProvider,
    ConnectedAccountStatus,
    RevokedGrant,
    connected_account_states,
    revoke_connected_account,
)

logger = logging.getLogger(__name__)

OWN_MAILBOX_SCREEN = "/account/mailbox"
BUSINESS_MAILBOX_SCREEN = "/administration/mail"


@dataclasses.dataclass(frozen=True)
class ResolvedSender:
    identity_id: uuid.UUID
    kind: SendingIdentityKind
    address: str


@dataclasses.dataclass(frozen=True)
class DeliveryRecord:
    """What a send answers: the delivery row's id, its status, the address the
    customer sees and, when it failed, a closed code."""

    delivery_id: uuid.UUID
    status: DeliveryStatus
    sender_address: str
    failure_code: str | None


def _sender_refusal(reason: str, message: str) -> ConflictError:
    return ConflictError(
        message,
        details={
            "reason": reason,
            "ownMailboxScreen": OWN_MAILBOX_SCREEN,
            "businessMailboxScreen": BUSINESS_MAILBOX_SCREEN,
        },
    )


def _grant_live(db: Session, identity: SendingIdentity) -> ConnectedAccountStatus | None:
    """The grant's status in platform, or None when it is revoked or gone."""

    if identity.connected_account_id is None:
        return None
    state = connected_account_states(db, account_ids=[identity.connected_account_id]).get(
        identity.connected_account_id
    )
    if state is None or state.status == ConnectedAccountStatus.REVOKED:
        return None
    return state.status


def own_identity_state(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> SendingIdentity | None:
    """The user's live own mailbox in this Betrieb; a grant revoked in platform
    reads as none."""

    identity = mail_link.live_user_identity(db, dealership_id=dealership_id, user_id=user_id)
    if identity is None or _grant_live(db, identity) is None:
        return None
    return identity


def own_identity_in_error(db: Session, identity: SendingIdentity) -> bool:
    return identity.status == SendingIdentityStatus.ERROR or _grant_live(db, identity) == ConnectedAccountStatus.ERROR


def resolve_sender(db: Session, *, dealership_id: uuid.UUID, author_user_id: uuid.UUID) -> ResolvedSender:
    """D1 A. Reads only (the caller's session); refusals are 409 with
    `details.reason` and the two screens' paths."""

    own = own_identity_state(db, dealership_id=dealership_id, user_id=author_user_id)
    if own is not None:
        if own_identity_in_error(db, own):
            raise _sender_refusal(
                "own_mailbox_in_error",
                "Your own mailbox cannot send right now; reconnect it in My account.",
            )
        return ResolvedSender(identity_id=own.id, kind=own.kind, address=own.mailbox_address)
    business = mail_link.live_business_identity(db, dealership_id=dealership_id)
    if business is not None:
        if business.status == SendingIdentityStatus.ERROR:
            raise _sender_refusal(
                "business_mailbox_in_error",
                "The Betrieb's shared mailbox cannot send right now; a manager reconnects it.",
            )
        return ResolvedSender(identity_id=business.id, kind=business.kind, address=business.mailbox_address)
    raise _sender_refusal(
        "no_mailbox_connected",
        "No mailbox is connected: connect your own in My account, or a manager connects the Betrieb's.",
    )


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, ProviderAccountRefusedError):
        return "mailbox_refused"
    if isinstance(exc, ProviderConfigurationError):
        return "provider_not_configured"
    if isinstance(exc, ProviderRequestError):
        return "provider_refused_message"
    if isinstance(exc, (CircuitOpenError, ProviderGatewayError)):
        return "provider_unavailable"
    return "send_failed"


def send_customer_mail(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    author_user_id: uuid.UUID,
    document_type: str,
    document_id: uuid.UUID | None,
    to: Sequence[str],
    cc: Sequence[str] = (),
    subject: str,
    body_text: str,
    attachments: Sequence[MailAttachment] = (),
) -> DeliveryRecord:
    """Sends one customer mail from the resolved sender. Raises the
    resolution's 409 before anything is written; afterwards it answers, never
    raises: the delivery row says what happened. Commits on its own session
    only."""

    if not to:
        raise ConflictError("A customer mail needs a recipient.", details={"reason": "no_recipient"})
    sender = resolve_sender(db, dealership_id=dealership_id, author_user_id=author_user_id)
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        identity = own.get(SendingIdentity, sender.identity_id)
        assert identity is not None
        message = OutgoingMail(
            sender=sender.address,
            to=tuple(to),
            cc=tuple(cc),
            subject=subject,
            body_text=body_text,
            attachments=tuple(attachments),
        )
        provider_message_id: str | None = None
        failure: BaseException | None = None
        connection = mail_link.app_connection(own, identity.provider_code)
        try:
            if connection is None:
                raise ProviderConfigurationError("Nexotec's app registration is not set up.")
            with call_capability(
                own, connection=connection, capability=MAIL_SEND_CAPABILITY, actor_id=author_user_id,
                purpose=document_type,
            ) as raw:
                adapter = cast(MailGateway, raw)
                if identity.kind == SendingIdentityKind.USER_MAILBOX:
                    assert identity.connected_account_id is not None
                    sent = adapter.send_as_user(grant_id=identity.connected_account_id, message=message)
                else:
                    sent = adapter.send_as_business(provider_tenant=identity.provider_tenant, message=message)
                provider_message_id = sent.provider_message_id
        except Exception as exc:  # noqa: BLE001 — every failure is a failed row (F2), never the caller's 500
            failure = exc
            logger.warning(
                "integration.mail: send from identity %s failed (%s)", identity.id, type(exc).__name__,
                extra={"sendingIdentityId": str(identity.id)},
            )
        own.rollback()  # whatever the gateway left open
        identity = own.get(SendingIdentity, sender.identity_id, populate_existing=True)
        assert identity is not None
        if isinstance(failure, ProviderAccountRefusedError) and identity.status == SendingIdentityStatus.ACTIVE:
            identity.status = SendingIdentityStatus.ERROR
            identity.error_code = "mailbox_refused"
            identity.version += 1
            record_audit_event(
                own, entity_type="integration_sending_identity", entity_id=identity.id, tenant_id=identity.tenant_id,
                action="mailbox_error", actor_id=None, after={"status": identity.status.value, "errorCode": "mailbox_refused"},
            )
        delivery = MessageDelivery(
            tenant_id=dealership_id,
            sending_identity_id=identity.id,
            identity_kind=identity.kind,
            sender_address=sender.address,
            recipients=[*to, *cc],
            subject=subject,
            document_type=document_type,
            document_id=document_id,
            author_user_id=author_user_id,
            status=DeliveryStatus.FAILED if failure is not None else DeliveryStatus.SENT,
            failure_code=_failure_code(failure) if failure is not None else None,
            provider_message_id=provider_message_id,
            sent_at=utcnow(),
            created_by=author_user_id,
            updated_by=author_user_id,
        )
        own.add(delivery)
        own.commit()
        return DeliveryRecord(
            delivery_id=delivery.id,
            status=delivery.status,
            sender_address=delivery.sender_address,
            failure_code=delivery.failure_code,
        )


# -- disconnecting --------------------------------------------------------------


def disconnect(db: Session, *, identity: SendingIdentity, actor_id: uuid.UUID) -> SendingIdentity:
    """Ends one identity in Nexotec (the row stays, `disconnected`). An own
    mailbox then ends its grant in platform unless another live identity of the
    same grant remains. Commits."""

    if identity.status == SendingIdentityStatus.DISCONNECTED:
        raise ConflictError("This mailbox is already disconnected.", details={"reason": "already_disconnected"})
    identity.status = SendingIdentityStatus.DISCONNECTED
    identity.disconnected_at = utcnow()
    identity.updated_by = actor_id
    identity.version += 1
    mail_link.audit_identity(db, identity, action="mailbox_disconnected", actor_id=actor_id)
    db.commit()
    if identity.kind == SendingIdentityKind.USER_MAILBOX and identity.connected_account_id is not None:
        still_used = db.scalar(
            select(SendingIdentity.id).where(
                SendingIdentity.connected_account_id == identity.connected_account_id,
                SendingIdentity.status != SendingIdentityStatus.DISCONNECTED,
            )
        )
        if still_used is None and identity.owner_user_id is not None:
            revoke_connected_account(db, connected_account_id=identity.connected_account_id, user_id=identity.owner_user_id)
    db.refresh(identity)
    return identity


# -- ending a grant at the provider (platform's consumer runs it) ---------------

_PLATFORM_TO_MAIL: dict[ConnectedAccountProvider, MailProviderCode] = {
    grant_provider: mail_provider for mail_provider, grant_provider in mail_link.GRANT_PROVIDER.items()
}


def _another_live_holder(db: Session, grant: RevokedGrant, provider: MailProviderCode) -> bool:
    """F3: another live own-mailbox identity — another grant, of this or another
    user — holds the same account at the provider."""

    others = list(
        db.scalars(
            select(SendingIdentity.connected_account_id).where(
                SendingIdentity.kind == SendingIdentityKind.USER_MAILBOX,
                SendingIdentity.provider_code == provider,
                SendingIdentity.external_account_id == grant.external_account_id,
                SendingIdentity.status != SendingIdentityStatus.DISCONNECTED,
                SendingIdentity.connected_account_id.is_not(None),
                SendingIdentity.connected_account_id != grant.connected_account_id,
            )
        )
    )
    states = connected_account_states(db, account_ids=[gid for gid in others if gid is not None])
    return any(state.status != ConnectedAccountStatus.REVOKED for state in states.values())


def end_mail_grant(grant: RevokedGrant, *, session_factory: Callable[[], Session]) -> None:
    """The revoker registered with `platform.public.register_grant_revoker`
    for both mailbox providers (app/worker.py). Idempotent: an already-ended
    grant at the provider and an already-deleted secret both succeed. Raises on
    the provider's refusal (the outbox retries, then dead-letters); the message
    never carries token material."""

    provider = _PLATFORM_TO_MAIL.get(grant.provider)
    if provider is None:
        return
    with session_factory() as db:
        for identity in db.scalars(
            select(SendingIdentity).where(
                SendingIdentity.connected_account_id == grant.connected_account_id,
                SendingIdentity.status != SendingIdentityStatus.DISCONNECTED,
            )
        ):
            # The grant is gone in platform: every identity on it ends too.
            identity.status = SendingIdentityStatus.DISCONNECTED
            identity.disconnected_at = utcnow()
            identity.version += 1
            mail_link.audit_identity(db, identity, action="mailbox_disconnected", actor_id=None)
        db.commit()
        if provider == MailProviderCode.GOOGLE_WORKSPACE and not _another_live_holder(db, grant, provider):
            connection = mail_link.app_connection(db, provider)
            if connection is None:
                raise ProviderConfigurationError("Nexotec's Google Workspace app registration is not set up.")
            with call_capability(
                db, connection=connection, capability="mail_grant_end", purpose="mail_grant_end"
            ) as raw:
                cast(MailGateway, raw).end_user_grant(grant_id=grant.connected_account_id)
        db.rollback()
    secrets_backend.delete_secret(connection_id=grant.connected_account_id, slot=SecretSlot.REFRESH_TOKEN.value)


def register_revokers(session_factory: Callable[[], Session]) -> None:
    """Called by the worker's composition root (the consumer runs there) with
    its session factory; a test registers with its own."""

    from app.platform.public import register_grant_revoker

    def _revoker(grant: RevokedGrant) -> None:
        end_mail_grant(grant, session_factory=session_factory)

    for grant_provider in _PLATFORM_TO_MAIL:
        register_grant_revoker(grant_provider, _revoker)
