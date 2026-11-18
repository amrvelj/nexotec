"""Connecting a mailbox (KAN-215): the OAuth round trip for a user's own
mailbox and for the Betrieb's shared one.

**The state.** `start_*` mints a random `state`, stores only its SHA-256 on a
`MailLinkState` row bound to the user, the Betrieb, the kind and the provider,
valid ten minutes, and hands the browser the provider's authorisation URL. The
PKCE verifier and the OIDC nonce are derived from the state with HKDF over the
deployment's signing key, so neither is stored anywhere. The callback consumes
the row in one conditional UPDATE: a second use, an expired state or a state of
another user or Betrieb is the same 409 `link_expired`, which confirms nothing.

**A user mailbox** (`user_mailbox`): one leg. The code is redeemed with the
verifier, the id_token verified (signature, issuer, audience, expiry, nonce;
Microsoft: a work account, Google: a Workspace domain), the grant recorded as
platform's connected-account row (`platform.public.link_connected_account`,
its own commit — ADR-047), the refresh token written to the secrets manager
under the grant's id, and the identity row written in integration's own
commit. A failure after the grant was created revokes it (the compensation).

**A shared mailbox** (`business_mailbox`, Anto's Q2 B: admin consent with
tenant-wide send). F1 of the 2026-11-15 review: the provider tenant is never
the browser's word.
- Microsoft: leg 1 is the administrator's OIDC sign-in; the verified id_token
  gives `tid` and must carry a directory role that can consent (`wids`). The
  tenant is written on a new state row for leg 2, the admin-consent screen;
  its callback is accepted only with `admin_consent=True` and a `tenant` equal
  to the stored one. Then an application token for that tenant (granted only
  after the consent) must find the mailbox in it through Graph.
- Google: one leg. The verified id_token gives `hd`; the Directory API must
  say the person signing in is an administrator and must know the mailbox
  (it answers for the administrator's own organisation only); a token minted
  for the mailbox through domain-wide delegation proves the delegation.
- Either way, a provider tenant already bound to a Betrieb outside this
  Betrieb's dealer group is refused (`tenant_bound_elsewhere`).

One live mailbox per kind: a second connect while one is live is 409
`mailbox_already_connected` — at the start, and again at the end, where the
partial unique index decides a race between two callbacks.
"""

import dataclasses
import datetime as dt
import hashlib
import secrets
import uuid
from typing import cast

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from app.integration.adapters.mail_base import MAIL_LINK_CAPABILITY, CodeGrant, MailGateway, MailLinkRefused
from app.integration.adapters.mail_providers import _b64url
from app.integration.errors import ProviderConfigurationError, ProviderGatewayError
from app.integration.models.connection import ConnectionScope, IntegrationConnection
from app.integration.models.mail import (
    MailLinkLeg,
    MailLinkState,
    MailProviderCode,
    SendingIdentity,
    SendingIdentityKind,
    SendingIdentityStatus,
)
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.gateway import call_capability, resolve_adapter
from app.integration.services.resilience import CircuitOpenError
from app.platform.public import (
    ConnectedAccountProvider,
    get_dealership_or_404,
    link_connected_account,
    revoke_connected_account,
)

LINK_TTL = dt.timedelta(minutes=10)
CALLBACK_PATH = "/mail-link/callback"
_HKDF_SALT = b"nexotec/integration/mail-link/v1"
_ENTITY_TYPE = "integration_sending_identity"

# integration's provider code -> platform's connected-account provider.
GRANT_PROVIDER: dict[MailProviderCode, ConnectedAccountProvider] = {
    MailProviderCode.MICROSOFT_365: ConnectedAccountProvider.MICROSOFT_365,
    MailProviderCode.GOOGLE_WORKSPACE: ConnectedAccountProvider.GOOGLE_WORKSPACE,
}


@dataclasses.dataclass(frozen=True)
class LinkOutcome:
    kind: SendingIdentityKind
    # `connected` — done; `consent_required` — go on to `redirect_url` (leg 2).
    status: str
    redirect_url: str | None
    identity_id: uuid.UUID | None


def _refusal(reason: str, message: str, **details: object) -> ConflictError:
    return ConflictError(message, details={"reason": reason, **details})


def redirect_uri() -> str:
    return get_settings().app_base_url.rstrip("/") + CALLBACK_PATH


# -- the state, the verifier, the nonce ---------------------------------------


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _derive(state: str, purpose: bytes) -> str:
    """32 bytes from the deployment's own signing key, bound to the state and
    the purpose; never stored. The key never leaves this function."""

    ikm = get_settings().jwt_private_key.encode("utf-8")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=purpose + b":" + state.encode("utf-8"))
    return _b64url(hkdf.derive(ikm))


def code_verifier(state: str) -> str:
    return _derive(state, b"pkce-verifier")  # 43 characters (RFC 7636)


def code_challenge(state: str) -> str:
    return _b64url(hashlib.sha256(code_verifier(state).encode("ascii")).digest())


def oidc_nonce(state: str) -> str:
    return _derive(state, b"oidc-nonce")


# -- Nexotec's app registration -------------------------------------------------


def app_connection(db: Session, provider_code: MailProviderCode) -> IntegrationConnection | None:
    """Nexotec's own app registration at the provider: the enabled
    platform-scope connection (secret refs only). None until platform staff
    set it up."""

    return db.scalar(
        select(IntegrationConnection)
        .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        .where(
            IntegrationProvider.provider_code == provider_code.value,
            IntegrationConnection.scope == ConnectionScope.PLATFORM,
            IntegrationConnection.enabled.is_(True),
        )
        .order_by(IntegrationConnection.created_at)
    )


def configured_providers(db: Session) -> list[MailProviderCode]:
    return [code for code in MailProviderCode if app_connection(db, code) is not None]


def _require_app_connection(db: Session, provider_code: MailProviderCode) -> IntegrationConnection:
    connection = app_connection(db, provider_code)
    if connection is None:
        raise _refusal(
            "mail_provider_not_configured",
            "Nexotec has not set up this mailbox provider yet.",
            provider=provider_code.value,
        )
    return connection


def _gateway_error(exc: Exception) -> ConflictError | UnprocessableEntityError:
    if isinstance(exc, MailLinkRefused):
        return _refusal(exc.code, "The provider's answer does not allow this mailbox to be connected.")
    if isinstance(exc, ProviderConfigurationError):
        return _refusal("mail_provider_not_configured", "Nexotec's app registration at this provider is incomplete.")
    return UnprocessableEntityError(
        "The mailbox provider could not be reached; try again in a moment.",
        details={"reason": "provider_unavailable"},
    )


# -- live identities ----------------------------------------------------------


def live_user_identity(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> SendingIdentity | None:
    return db.scalar(
        select(SendingIdentity).where(
            SendingIdentity.tenant_id == dealership_id,
            SendingIdentity.kind == SendingIdentityKind.USER_MAILBOX,
            SendingIdentity.owner_user_id == user_id,
            SendingIdentity.status != SendingIdentityStatus.DISCONNECTED,
        )
    )


def live_business_identity(db: Session, *, dealership_id: uuid.UUID) -> SendingIdentity | None:
    return db.scalar(
        select(SendingIdentity).where(
            SendingIdentity.tenant_id == dealership_id,
            SendingIdentity.kind == SendingIdentityKind.BUSINESS_MAILBOX,
            SendingIdentity.status != SendingIdentityStatus.DISCONNECTED,
        )
    )


def _already_connected() -> ConflictError:
    return _refusal(
        "mailbox_already_connected", "A mailbox is already connected; disconnect it first to connect another."
    )


# -- start ----------------------------------------------------------------------


def _new_state(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: SendingIdentityKind,
    provider_code: MailProviderCode,
    leg: MailLinkLeg,
    mailbox_address: str | None = None,
    provider_tenant: str | None = None,
) -> str:
    state = secrets.token_urlsafe(32)
    db.add(
        MailLinkState(
            state_hash=_state_hash(state),
            tenant_id=dealership_id,
            user_id=user_id,
            kind=kind,
            provider_code=provider_code,
            leg=leg,
            mailbox_address=mailbox_address,
            provider_tenant=provider_tenant,
            expires_at=utcnow() + LINK_TTL,
        )
    )
    return state


def _authorize_url(
    db: Session, connection: IntegrationConnection, *, state: str, admin: bool, user_id: uuid.UUID,
    login_hint: str | None,
) -> str:
    # Building the URL sends nothing: resolved without a call-log row.
    adapter = cast(MailGateway, resolve_adapter(db, connection, actor_id=user_id, purpose=MAIL_LINK_CAPABILITY))
    try:
        return adapter.authorize_url(
            admin=admin,
            state=state,
            nonce=oidc_nonce(state),
            code_challenge=code_challenge(state),
            redirect_uri=redirect_uri(),
            login_hint=login_hint,
        )
    except ProviderConfigurationError as exc:
        raise _gateway_error(exc) from exc


def start_user_link(
    db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID, provider_code: MailProviderCode
) -> str:
    """The user connects their own mailbox: the provider's authorisation URL."""

    if live_user_identity(db, dealership_id=dealership_id, user_id=user_id) is not None:
        raise _already_connected()
    connection = _require_app_connection(db, provider_code)
    state = _new_state(
        db, dealership_id=dealership_id, user_id=user_id, kind=SendingIdentityKind.USER_MAILBOX,
        provider_code=provider_code, leg=MailLinkLeg.USER_SIGNIN,
    )
    url = _authorize_url(db, connection, state=state, admin=False, user_id=user_id, login_hint=None)
    db.commit()
    return url


def start_business_link(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    provider_code: MailProviderCode,
    mailbox_address: str,
) -> str:
    """A manager connects the Betrieb's shared mailbox: the administrator's
    sign-in comes first (whoever signs in at the provider must administer
    the tenant the mailbox lives in)."""

    if live_business_identity(db, dealership_id=dealership_id) is not None:
        raise _already_connected()
    connection = _require_app_connection(db, provider_code)
    state = _new_state(
        db, dealership_id=dealership_id, user_id=user_id, kind=SendingIdentityKind.BUSINESS_MAILBOX,
        provider_code=provider_code, leg=MailLinkLeg.ADMIN_SIGNIN, mailbox_address=mailbox_address.strip().lower(),
    )
    url = _authorize_url(db, connection, state=state, admin=True, user_id=user_id, login_hint=None)
    db.commit()
    return url


# -- the callback ---------------------------------------------------------------


def _consume(db: Session, *, state: str, user_id: uuid.UUID, dealership_id: uuid.UUID) -> MailLinkState:
    """Single use, in one statement: of two concurrent callbacks with the same
    state exactly one gets the row."""

    now = utcnow()
    row_id = db.scalar(
        update(MailLinkState)
        .where(
            MailLinkState.state_hash == _state_hash(state),
            MailLinkState.user_id == user_id,
            MailLinkState.tenant_id == dealership_id,
            MailLinkState.consumed_at.is_(None),
            MailLinkState.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(MailLinkState.id)
    )
    db.commit()
    row = db.get(MailLinkState, row_id) if row_id is not None else None
    if row is None:
        raise _refusal("link_expired", "This link has expired or was already used; start again.")
    return row


def complete_link(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    state: str,
    code: str | None,
    error: str | None,
    admin_consent: str | None,
    tenant: str | None,
    may_connect_business_mailbox: bool,
) -> LinkOutcome:
    row = _consume(db, state=state, user_id=user_id, dealership_id=dealership_id)
    if row.kind == SendingIdentityKind.BUSINESS_MAILBOX and not may_connect_business_mailbox:
        # The manager flag was taken away since the link started.
        raise ForbiddenError("Not permitted to write 'sending_identities'.")
    if error:
        # The provider's error code is not shown (it may be anything).
        raise _refusal("provider_declined", "The sign-in at the provider was cancelled or refused.")
    connection = _require_app_connection(db, row.provider_code)
    if row.leg == MailLinkLeg.ADMIN_CONSENT:
        return _finish_microsoft_consent(db, row, connection, admin_consent=admin_consent, tenant=tenant)
    if not code:
        raise _refusal("provider_declined", "The provider sent no authorisation code.")
    if row.leg == MailLinkLeg.USER_SIGNIN:
        return _finish_user_link(db, row, connection, code=code, state=state)
    return _finish_admin_sign_in(db, row, connection, code=code, state=state)


def _redeem(
    db: Session, connection: IntegrationConnection, *, code: str, state: str, admin: bool, actor_id: uuid.UUID
):
    try:
        with call_capability(
            db, connection=connection, capability=MAIL_LINK_CAPABILITY, actor_id=actor_id, purpose=MAIL_LINK_CAPABILITY
        ) as raw:
            adapter = cast(MailGateway, raw)
            grant = adapter.redeem_code(
                code=code, code_verifier=code_verifier(state), redirect_uri=redirect_uri(), admin=admin
            )
            sign_in = adapter.verify_sign_in(grant, nonce=oidc_nonce(state))
            return adapter, grant, sign_in
    except (ProviderGatewayError, CircuitOpenError) as exc:
        raise _gateway_error(exc) from exc


def _finish_user_link(
    db: Session, row: MailLinkState, connection: IntegrationConnection, *, code: str, state: str
) -> LinkOutcome:
    _, grant, sign_in = _redeem(db, connection, code=code, state=state, admin=False, actor_id=row.user_id)
    if grant.refresh_token is None or not sign_in.email:
        raise _refusal("consent_incomplete", "The provider did not grant the permission to send mail.")
    if live_user_identity(db, dealership_id=row.tenant_id, user_id=row.user_id) is not None:
        raise _already_connected()
    db.commit()  # nothing of ours pending before platform's own commit (ADR-047)
    new_id = uuid.uuid4()
    linked = link_connected_account(
        db,
        user_id=row.user_id,
        provider=GRANT_PROVIDER[row.provider_code],
        external_account_id=sign_in.subject,
        label=sign_in.email,
        scopes=list(grant.scopes),
        new_account_id=new_id,
        new_secret_ref=secrets_backend.secret_ref_for(connection_id=new_id, slot=SecretSlot.REFRESH_TOKEN.value),
    )

    def _compensate() -> None:
        db.rollback()
        if linked.created:
            revoke_connected_account(db, connected_account_id=linked.connected_account_id, user_id=row.user_id)

    try:
        _store_refresh_token(linked.connected_account_id, grant, created=linked.created)
        identity = SendingIdentity(
            tenant_id=row.tenant_id,
            kind=SendingIdentityKind.USER_MAILBOX,
            provider_code=row.provider_code,
            owner_user_id=row.user_id,
            connected_account_id=linked.connected_account_id,
            mailbox_address=sign_in.email,
            external_account_id=sign_in.subject,
            provider_tenant=sign_in.provider_tenant,
            status=SendingIdentityStatus.ACTIVE,
            connected_at=utcnow(),
            created_by=row.user_id,
            updated_by=row.user_id,
        )
        db.add(identity)
        db.flush()
    except IntegrityError as exc:
        _compensate()
        raise _already_connected() from exc
    except Exception:
        _compensate()
        raise
    audit_identity(db, identity, action="mailbox_connected", actor_id=row.user_id)
    db.commit()
    return LinkOutcome(kind=identity.kind, status="connected", redirect_url=None, identity_id=identity.id)


def _store_refresh_token(grant_id: uuid.UUID, grant: CodeGrant, *, created: bool) -> None:
    assert grant.refresh_token is not None
    slot = SecretSlot.REFRESH_TOKEN.value
    if created:
        secrets_backend.create_secret(connection_id=grant_id, slot=slot, value=grant.refresh_token)
    else:
        secrets_backend.update_secret(connection_id=grant_id, slot=slot, value=grant.refresh_token)


def _refuse_tenant_bound_elsewhere(
    db: Session, *, dealership_id: uuid.UUID, provider_code: MailProviderCode, provider_tenant: str
) -> None:
    """F1: a provider tenant (Microsoft `tid`, Google domain) whose shared
    mailbox another dealer group connected is not this Betrieb's."""

    holders = set(
        db.scalars(
            select(SendingIdentity.tenant_id).where(
                SendingIdentity.kind == SendingIdentityKind.BUSINESS_MAILBOX,
                SendingIdentity.provider_code == provider_code,
                SendingIdentity.provider_tenant == provider_tenant,
                SendingIdentity.status != SendingIdentityStatus.DISCONNECTED,
                SendingIdentity.tenant_id != dealership_id,
            )
        )
    )
    if not holders:
        return
    own_group = get_dealership_or_404(db, dealership_id).dealer_group_id

    def _group(holder: uuid.UUID) -> uuid.UUID | None:
        try:
            return get_dealership_or_404(db, holder).dealer_group_id
        except NotFoundError:
            return None  # a Betrieb that no longer exists binds nothing

    if any((group := _group(holder)) is not None and group != own_group for holder in sorted(holders)):
        raise _refusal(
            "tenant_bound_elsewhere",
            "This mailbox's organisation is already connected to a Betrieb of another dealer group.",
        )


def _finish_admin_sign_in(
    db: Session, row: MailLinkState, connection: IntegrationConnection, *, code: str, state: str
) -> LinkOutcome:
    adapter, grant, sign_in = _redeem(db, connection, code=code, state=state, admin=True, actor_id=row.user_id)
    assert row.mailbox_address is not None
    _refuse_tenant_bound_elsewhere(
        db, dealership_id=row.tenant_id, provider_code=row.provider_code, provider_tenant=sign_in.provider_tenant
    )
    try:
        with call_capability(
            db, connection=connection, capability=MAIL_LINK_CAPABILITY, actor_id=row.user_id,
            purpose=MAIL_LINK_CAPABILITY,
        ) as raw:
            adapter = cast(MailGateway, raw)
            if not adapter.administers_tenant(grant, sign_in):
                raise MailLinkRefused("not_an_administrator")
            mailbox = None
            if not adapter.needs_admin_consent:
                mailbox = adapter.find_business_mailbox(
                    provider_tenant=sign_in.provider_tenant, address=row.mailbox_address, admin_grant=grant
                )
                if mailbox is None:
                    raise MailLinkRefused("mailbox_not_in_tenant")
    except (ProviderGatewayError, CircuitOpenError) as exc:
        raise _gateway_error(exc) from exc
    if adapter.needs_admin_consent:
        # Microsoft: leg 2, the admin consent, for the tenant just verified.
        next_state = _new_state(
            db, dealership_id=row.tenant_id, user_id=row.user_id, kind=row.kind, provider_code=row.provider_code,
            leg=MailLinkLeg.ADMIN_CONSENT, mailbox_address=row.mailbox_address,
            provider_tenant=sign_in.provider_tenant,
        )
        url = adapter.consent_url(provider_tenant=sign_in.provider_tenant, state=next_state, redirect_uri=redirect_uri())
        db.commit()
        return LinkOutcome(kind=row.kind, status="consent_required", redirect_url=url, identity_id=None)
    assert mailbox is not None
    return _create_business_identity(
        db, row, provider_tenant=sign_in.provider_tenant, external_account_id=mailbox.external_account_id,
        address=mailbox.address,
    )


def _finish_microsoft_consent(
    db: Session,
    row: MailLinkState,
    connection: IntegrationConnection,
    *,
    admin_consent: str | None,
    tenant: str | None,
) -> LinkOutcome:
    # The tenant is the one the verified sign-in named; the browser's copy
    # must agree with it, and is never used.
    if row.provider_tenant is None or row.mailbox_address is None:
        raise _refusal("link_expired", "This link has expired or was already used; start again.")
    if (admin_consent or "").lower() != "true" or (tenant or "").lower() != row.provider_tenant.lower():
        raise _refusal("consent_not_granted", "The administrator did not grant the consent.")
    _refuse_tenant_bound_elsewhere(
        db, dealership_id=row.tenant_id, provider_code=row.provider_code, provider_tenant=row.provider_tenant
    )
    try:
        with call_capability(
            db, connection=connection, capability=MAIL_LINK_CAPABILITY, actor_id=row.user_id,
            purpose=MAIL_LINK_CAPABILITY,
        ) as raw:
            mailbox = cast(MailGateway, raw).find_business_mailbox(
                provider_tenant=row.provider_tenant, address=row.mailbox_address, admin_grant=None
            )
            if mailbox is None:
                raise MailLinkRefused("mailbox_not_in_tenant")
    except (ProviderGatewayError, CircuitOpenError) as exc:
        raise _gateway_error(exc) from exc
    return _create_business_identity(
        db, row, provider_tenant=row.provider_tenant, external_account_id=mailbox.external_account_id,
        address=mailbox.address,
    )


def _create_business_identity(
    db: Session, row: MailLinkState, *, provider_tenant: str, external_account_id: str, address: str
) -> LinkOutcome:
    identity = SendingIdentity(
        tenant_id=row.tenant_id,
        kind=SendingIdentityKind.BUSINESS_MAILBOX,
        provider_code=row.provider_code,
        owner_user_id=None,
        connected_account_id=None,
        mailbox_address=address,
        external_account_id=external_account_id,
        provider_tenant=provider_tenant,
        status=SendingIdentityStatus.ACTIVE,
        connected_at=utcnow(),
        created_by=row.user_id,
        updated_by=row.user_id,
    )
    try:
        with db.begin_nested():
            db.add(identity)
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _already_connected() from exc
    audit_identity(db, identity, action="mailbox_connected", actor_id=row.user_id)
    db.commit()
    return LinkOutcome(kind=identity.kind, status="connected", redirect_url=None, identity_id=identity.id)


def audit_identity(
    db: Session, identity: SendingIdentity, *, action: str, actor_id: uuid.UUID | None, reason: str | None = None
) -> None:
    # The address of a colleague's own mailbox stays out of the Betrieb's
    # audit (D-A-15): the kind, the provider and the status only.
    after: dict[str, object] = {
        "kind": identity.kind.value,
        "provider": identity.provider_code.value,
        "status": identity.status.value,
    }
    if identity.kind == SendingIdentityKind.BUSINESS_MAILBOX:
        after["mailboxAddress"] = identity.mailbox_address
    record_audit_event(
        db,
        entity_type=_ENTITY_TYPE,
        entity_id=identity.id,
        tenant_id=identity.tenant_id,
        action=action,
        actor_id=actor_id,
        after=after,
        reason=reason,
    )
