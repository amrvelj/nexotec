"""Binding a location to Google Business Profile (KAN-226 PR-2; D-A-32,
FR-A-35): the OAuth round trip, the location pick, and the disconnect.

KAN-215's mailbox link is the pattern (`services/mail_link.py`):

**The state.** `start_link` mints a random `state`, stores only its SHA-256 on
a `GbpLinkState` row bound to the user, the Betrieb and the Nexotec location,
valid ten minutes, and hands the browser Google's consent URL. The PKCE
verifier is derived from the state with HKDF over the deployment's signing key,
so it is stored nowhere. The callback consumes the row in one conditional
UPDATE: a second use, an expired state or a state of another user or Betrieb
is the same 409 `link_expired`, which confirms nothing.

**The callback** redeems the code with the verifier through Nexotec's OAuth
client (the platform-scope connection to `google_business_profile`), writes
the refresh token to the secrets manager under a new `pending` binding's id,
and answers the Google accounts and locations the sign-in may manage.

**The pick.** The manager names one of them; it must be a location of that
account as Google lists it for this binding's grant (409
`google_location_not_accessible` otherwise), and no other live binding — of
any Betrieb — may hold it (409 `google_location_already_bound`, which names
nobody). One live binding per Nexotec location: a second start while one is
active is 409 `location_already_bound`; a pending one is replaced.

**Disconnect** revokes the grant at Google, drops the secret and keeps the row
(`disconnected`). A grant Google already refuses counts as ended.

Every request goes through `call_capability` on Nexotec's client connection,
so each is a call-log row; a refused grant (`ProviderAccountRefusedError`) is
the binding's answer — it goes to `error`, never charged to the breaker.
"""

import dataclasses
import datetime as dt
import hashlib
import logging
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from app.integration.adapters.google_business_profile import (
    GBP_CAPABILITY,
    SCOPE,
    GbpAccount,
    GbpGrant,
    GbpLocation,
    GoogleBusinessProfileAdapter,
)
from app.integration.adapters.mail_providers import _b64url
from app.integration.errors import (
    ProviderAccountRefusedError,
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderRequestError,
)
from app.integration.models.connection import ConnectionScope, IntegrationConnection
from app.integration.models.google_business_profile import (
    GBP_PROVIDER_CODE,
    GbpBindingStatus,
    GbpLinkState,
    GbpLocationBinding,
)
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.gateway import call_capability, resolve_adapter
from app.integration.services.resilience import CircuitOpenError
from app.platform.public import get_location_or_404

logger = logging.getLogger("app.integration.gbp_link")

LINK_TTL = dt.timedelta(minutes=10)
# KAN-763's rule for the mailbox link states, applied to these: kept a day
# past use or expiry for a support question, then deleted.
LINK_STATE_RETENTION = dt.timedelta(days=1)
CALLBACK_PATH = "/business-profile-link/callback"
_HKDF_SALT = b"nexotec/integration/business-profile-link/v1"
_ENTITY_TYPE = "integration_gbp_location_binding"
_REFRESH_SLOT = SecretSlot.REFRESH_TOKEN.value
_PURPOSE = "business_profile_link"


@dataclasses.dataclass(frozen=True)
class GoogleChoice:
    """A location the sign-in may manage, as Google listed it."""

    account_name: str
    account_display_name: str
    location_name: str
    title: str


@dataclasses.dataclass(frozen=True)
class LinkOutcome:
    binding: GbpLocationBinding
    choices: list[GoogleChoice]


def _refusal(reason: str, message: str) -> ConflictError:
    return ConflictError(message, details={"reason": reason})


def redirect_uri() -> str:
    return get_settings().app_base_url.rstrip("/") + CALLBACK_PATH


# -- the state and the verifier ---------------------------------------------------


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def code_verifier(state: str) -> str:
    """32 bytes from the deployment's signing key, bound to the state; never stored."""

    ikm = get_settings().jwt_private_key.encode("utf-8")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=b"pkce-verifier:" + state.encode())
    return _b64url(hkdf.derive(ikm))


def code_challenge(state: str) -> str:
    return _b64url(hashlib.sha256(code_verifier(state).encode("ascii")).digest())


# -- Nexotec's OAuth client -----------------------------------------------------------


def client_connection(db: Session) -> IntegrationConnection | None:
    """Nexotec's own OAuth client at Google: the enabled platform-scope
    connection (secret refs only). None until platform staff set it up."""

    return db.scalar(
        select(IntegrationConnection)
        .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        .where(
            IntegrationProvider.provider_code == GBP_PROVIDER_CODE,
            IntegrationConnection.scope == ConnectionScope.PLATFORM,
            IntegrationConnection.enabled.is_(True),
        )
        .order_by(IntegrationConnection.created_at)
    )


def _require_client(db: Session) -> IntegrationConnection:
    connection = client_connection(db)
    if connection is None:
        raise _refusal("business_profile_not_configured", "Nexotec has not set up Google Business Profile yet.")
    return connection


def _unavailable() -> UnprocessableEntityError:
    return UnprocessableEntityError(
        "Google Business Profile could not be reached; try again in a moment.",
        details={"reason": "provider_unavailable"},
    )


@contextmanager
def _google(db: Session, connection: IntegrationConnection, *, actor_id: uuid.UUID | None) -> Iterator[
    GoogleBusinessProfileAdapter
]:
    """One `call_capability` block on Nexotec's client. A transport failure or
    an open breaker is 422 `provider_unavailable`; an incomplete client 409
    `business_profile_not_configured`. A refused grant passes through as
    `ProviderAccountRefusedError` for the caller to judge."""

    try:
        with call_capability(
            db, connection=connection, capability=GBP_CAPABILITY, actor_id=actor_id, purpose=_PURPOSE
        ) as raw:
            yield cast(GoogleBusinessProfileAdapter, raw)
    except ProviderAccountRefusedError:
        raise
    except ProviderConfigurationError as exc:
        raise _refusal(
            "business_profile_not_configured", "Nexotec's Google Business Profile client is incomplete."
        ) from exc
    except (ProviderGatewayError, CircuitOpenError) as exc:
        raise _unavailable() from exc


# -- reads ------------------------------------------------------------------------------


def list_bindings(db: Session, *, dealership_id: uuid.UUID) -> list[GbpLocationBinding]:
    """The Betrieb's bindings that are not disconnected, by location name."""

    return list(
        db.scalars(
            select(GbpLocationBinding)
            .where(
                GbpLocationBinding.tenant_id == dealership_id,
                GbpLocationBinding.status != GbpBindingStatus.DISCONNECTED,
            )
            .order_by(GbpLocationBinding.location_label, GbpLocationBinding.id)
        )
    )


def get_binding_or_404(db: Session, *, dealership_id: uuid.UUID, binding_id: uuid.UUID) -> GbpLocationBinding:
    binding = db.scalar(
        select(GbpLocationBinding).where(
            GbpLocationBinding.id == binding_id, GbpLocationBinding.tenant_id == dealership_id
        )
    )
    if binding is None:
        raise NotFoundError(f"Business Profile binding {binding_id} was not found.")
    return binding


def get_binding_for_update_or_404(
    db: Session, *, dealership_id: uuid.UUID, binding_id: uuid.UUID
) -> GbpLocationBinding:
    binding = db.scalar(
        select(GbpLocationBinding)
        .where(GbpLocationBinding.id == binding_id, GbpLocationBinding.tenant_id == dealership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if binding is None:
        raise NotFoundError(f"Business Profile binding {binding_id} was not found.")
    return binding


def _live_binding_of_location(db: Session, location_id: uuid.UUID) -> GbpLocationBinding | None:
    return db.scalar(
        select(GbpLocationBinding).where(
            GbpLocationBinding.location_id == location_id,
            GbpLocationBinding.status != GbpBindingStatus.DISCONNECTED,
        )
    )


def _location_label(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> str:
    try:
        return get_location_or_404(db, dealership_id=dealership_id, location_id=location_id).name
    except NotFoundError:
        raise UnprocessableEntityError(
            "The location is not one of this business's.",
            details={"reason": "unknown_location", "locationId": str(location_id)},
        ) from None


def _refuse_bound(binding: GbpLocationBinding | None) -> None:
    if binding is not None and binding.status != GbpBindingStatus.PENDING:
        raise _refusal(
            "location_already_bound",
            "This location is already connected to Google Business Profile; disconnect it first.",
        )


# -- start ------------------------------------------------------------------------------


def start_link(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID, location_id: uuid.UUID) -> str:
    """The manager connects one location: Google's consent URL."""

    _location_label(db, dealership_id=dealership_id, location_id=location_id)
    _refuse_bound(_live_binding_of_location(db, location_id))
    connection = _require_client(db)
    state = secrets.token_urlsafe(32)
    db.add(
        GbpLinkState(
            state_hash=_state_hash(state),
            tenant_id=dealership_id,
            user_id=user_id,
            location_id=location_id,
            expires_at=utcnow() + LINK_TTL,
        )
    )
    # Building the URL sends nothing: resolved without a call-log row.
    adapter = cast(GoogleBusinessProfileAdapter, resolve_adapter(db, connection, actor_id=user_id, purpose=_PURPOSE))
    try:
        url = adapter.authorize_url(state=state, code_challenge=code_challenge(state), redirect_uri=redirect_uri())
    except ProviderConfigurationError as exc:
        raise _refusal(
            "business_profile_not_configured", "Nexotec's Google Business Profile client is incomplete."
        ) from exc
    db.commit()
    return url


# -- the callback ---------------------------------------------------------------------


def _consume(db: Session, *, state: str, user_id: uuid.UUID, dealership_id: uuid.UUID) -> GbpLinkState:
    """Single use, in one statement: of two concurrent callbacks with the same
    state exactly one gets the row."""

    now = utcnow()
    row_id = db.scalar(
        update(GbpLinkState)
        .where(
            GbpLinkState.state_hash == _state_hash(state),
            GbpLinkState.user_id == user_id,
            GbpLinkState.tenant_id == dealership_id,
            GbpLinkState.consumed_at.is_(None),
            GbpLinkState.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(GbpLinkState.id)
    )
    db.commit()
    row = db.get(GbpLinkState, row_id) if row_id is not None else None
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
    may_connect: bool,
) -> LinkOutcome:
    row = _consume(db, state=state, user_id=user_id, dealership_id=dealership_id)
    if not may_connect:
        # The manager flag was taken away since the link started.
        raise ForbiddenError("Not permitted to write 'integration_connections'.")
    if error or not code:
        # Google's error code is not shown (it may be anything).
        raise _refusal("provider_declined", "The sign-in at Google was cancelled or refused.")
    label = _location_label(db, dealership_id=row.tenant_id, location_id=row.location_id)
    connection = _require_client(db)
    # A refusal is raised outside each block: inside, it would count as a
    # failed call against Nexotec's client.
    try:
        with _google(db, connection, actor_id=user_id) as adapter:
            grant = adapter.redeem_code(code=code, code_verifier=code_verifier(state), redirect_uri=redirect_uri())
    except ProviderAccountRefusedError as exc:
        raise _refusal("provider_declined", "Google refused the sign-in.") from exc
    if grant.refresh_token is None or SCOPE not in grant.scopes:
        _revoke_unused_grant(db, connection, grant, actor_id=user_id, link_id=row.id)
        raise _consent_incomplete()
    try:
        with _google(db, connection, actor_id=user_id) as adapter:
            choices = _choices(adapter, access_token=grant.access_token)
    except ProviderAccountRefusedError as exc:
        _revoke_unused_grant(db, connection, grant, actor_id=user_id, link_id=row.id)
        raise _consent_incomplete() from exc
    binding = _create_pending_binding(db, row, label=label, grant=grant, connection=connection)
    return LinkOutcome(binding=binding, choices=choices)


def _consent_incomplete() -> ConflictError:
    return _refusal(
        "business_profile_consent_incomplete", "Google did not grant Nexotec access to the Business Profile."
    )


def _revoke_unused_grant(
    db: Session, connection: IntegrationConnection, grant: GbpGrant, *, actor_id: uuid.UUID, link_id: uuid.UUID
) -> None:
    """A refresh token Google issued for a link that is refused anyway is
    ended at Google at once, never kept: best effort, one request. A failure is
    logged by the link's id only and never changes the refusal that follows."""

    if grant.refresh_token is None:
        return
    try:
        with _google(db, connection, actor_id=actor_id) as adapter:
            adapter.revoke_token(token=grant.refresh_token)
    except (ProviderAccountRefusedError, ConflictError, UnprocessableEntityError):
        logger.warning("business profile link %s: the unused grant could not be revoked at Google", link_id)


def _choices(adapter: GoogleBusinessProfileAdapter, *, access_token: str) -> list[GoogleChoice]:
    choices: list[GoogleChoice] = []
    for account in adapter.list_accounts(access_token=access_token):
        for location in adapter.list_locations(access_token=access_token, account_name=account.name):
            choices.append(_choice(account, location))
    return choices


def _choice(account: GbpAccount, location: GbpLocation) -> GoogleChoice:
    return GoogleChoice(
        account_name=account.name,
        account_display_name=account.account_name,
        location_name=location.name,
        title=location.title,
    )


def _create_pending_binding(
    db: Session, row: GbpLinkState, *, label: str, grant: GbpGrant, connection: IntegrationConnection
) -> GbpLocationBinding:
    assert grant.refresh_token is not None
    existing = _live_binding_of_location(db, row.location_id)
    try:
        _refuse_bound(existing)
    except ConflictError:
        # Bound meanwhile: the grant just redeemed is never kept (KAN-226 review).
        _revoke_unused_grant(db, connection, grant, actor_id=row.user_id, link_id=row.id)
        raise
    if existing is not None:
        # A pick never finished: the new sign-in replaces it.
        _end(
            db, existing, connection=connection, actor_id=row.user_id, action="business_profile_link_replaced",
            best_effort=True,
        )
    now = utcnow()
    binding = GbpLocationBinding(
        tenant_id=row.tenant_id,
        location_id=row.location_id,
        location_label=label,
        location_label_refreshed_at=now,
        status=GbpBindingStatus.PENDING,
        connected_at=now,
        created_by=row.user_id,
        updated_by=row.user_id,
    )
    try:
        with db.begin_nested():
            db.add(binding)
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        _revoke_unused_grant(db, connection, grant, actor_id=row.user_id, link_id=row.id)
        raise _refusal(
            "location_already_bound",
            "This location is already connected to Google Business Profile; disconnect it first.",
        ) from exc
    try:
        secrets_backend.create_secret(connection_id=binding.id, slot=_REFRESH_SLOT, value=grant.refresh_token)
        _audit(db, binding, action="business_profile_linked", actor_id=row.user_id)
        db.commit()
    except Exception:
        db.rollback()
        _drop_secret(binding.id)
        raise
    db.refresh(binding)
    return binding


# -- the pick ---------------------------------------------------------------------------


@contextmanager
def _binding_grant(
    db: Session, binding: GbpLocationBinding, *, actor_id: uuid.UUID
) -> Iterator[tuple[GoogleBusinessProfileAdapter, str]]:
    """A block with a fresh access token for the binding. A grant Google
    refuses sends the binding to `error` (its own commit) and is 409
    `business_profile_grant_refused`: the manager reconnects."""

    connection = _require_client(db)
    try:
        with _google(db, connection, actor_id=actor_id) as adapter:
            yield adapter, adapter.access_token(binding_id=binding.id)
    except ProviderAccountRefusedError as exc:
        mark_grant_refused(db, binding)
        raise _refusal(
            "business_profile_grant_refused", "Google no longer grants access to this location; connect it again."
        ) from exc


def list_choices(db: Session, *, binding: GbpLocationBinding, actor_id: uuid.UUID) -> list[GoogleChoice]:
    _require_pending(binding)
    with _binding_grant(db, binding, actor_id=actor_id) as (adapter, token):
        return _choices(adapter, access_token=token)


def _require_pending(binding: GbpLocationBinding) -> None:
    if binding.status != GbpBindingStatus.PENDING:
        raise _refusal("binding_not_pending", "This location's Google Business Profile location is already chosen.")


def bind_google_location(
    db: Session, *, binding: GbpLocationBinding, account_name: str, location_name: str, actor_id: uuid.UUID
) -> GbpLocationBinding:
    """The manager's pick. Google must list the location under that account for
    this grant; no other live binding may hold it."""

    _require_pending(binding)
    offered: dict[str, GbpLocation] = {}
    with _binding_grant(db, binding, actor_id=actor_id) as (adapter, token):
        try:
            offered = {
                location.name: location
                for location in adapter.list_locations(access_token=token, account_name=account_name)
            }
        except ProviderRequestError:
            pass  # Google does not know the account for this grant (404): nothing offered
    picked = offered.get(location_name)
    if picked is None:
        raise _refusal(
            "google_location_not_accessible", "This Google Business Profile location is not one this sign-in manages."
        )
    if _google_location_held(db, location_name, except_id=binding.id):
        raise _google_location_taken()
    binding.google_account_name = account_name
    binding.google_location_name = location_name
    binding.google_location_title = picked.title[:300] or None
    binding.status = GbpBindingStatus.ACTIVE
    binding.error_code = None
    binding.updated_by = actor_id
    binding.version += 1
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _google_location_taken() from exc
    _audit(db, binding, action="business_profile_location_bound", actor_id=actor_id)
    db.commit()
    db.refresh(binding)
    return binding


def _google_location_held(db: Session, location_name: str, *, except_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(GbpLocationBinding.id).where(
                GbpLocationBinding.google_location_name == location_name,
                GbpLocationBinding.status != GbpBindingStatus.DISCONNECTED,
                GbpLocationBinding.id != except_id,
            )
        )
        is not None
    )


def _google_location_taken() -> ConflictError:
    # Names nobody: another Betrieb's binding is not confirmed (rule 7).
    return _refusal(
        "google_location_already_bound", "This Google Business Profile location is already connected in Nexotec."
    )


# -- a refused grant, disconnect ----------------------------------------------------------


def mark_grant_refused(db: Session, binding: GbpLocationBinding) -> None:
    """Google refused the binding's grant: `error`, on its own commit, so the
    refusal stands whatever the caller does next."""

    db.rollback()
    db.refresh(binding)
    if binding.status in (GbpBindingStatus.DISCONNECTED, GbpBindingStatus.ERROR):
        return
    binding.status = GbpBindingStatus.ERROR
    binding.error_code = "grant_refused"
    binding.version += 1
    _audit(db, binding, action="business_profile_grant_refused", actor_id=None)
    db.commit()


def disconnect(db: Session, *, binding: GbpLocationBinding, actor_id: uuid.UUID) -> GbpLocationBinding:
    """Ends the binding: the grant is revoked at Google first (unreachable:
    422 `provider_unavailable`, nothing changed), then the secret is dropped and
    the row kept, `disconnected`. Commits."""

    if binding.status == GbpBindingStatus.DISCONNECTED:
        raise _refusal("binding_already_disconnected", "This location is already disconnected.")
    connection = client_connection(db)
    _end(db, binding, connection=connection, actor_id=actor_id, action="business_profile_disconnected")
    db.commit()
    db.refresh(binding)
    return binding


def _end(
    db: Session,
    binding: GbpLocationBinding,
    *,
    connection: IntegrationConnection | None,
    actor_id: uuid.UUID | None,
    action: str,
    best_effort: bool = False,
    reason: str | None = None,
    secret_must_go: bool = False,
) -> None:
    """Revoke at Google, drop the secret, mark the row; the caller commits.
    Without Nexotec's client (deleted by staff) nothing can be revoked: the
    secret goes all the same. Google unreachable is 422 `provider_unavailable`
    with nothing changed — unless `best_effort` (a pick that never finished,
    replaced by a new sign-in), which logs it and goes on."""

    if connection is not None:
        try:
            with _google(db, connection, actor_id=actor_id) as adapter:
                adapter.revoke(binding_id=binding.id)
        except ProviderAccountRefusedError:
            pass  # Google no longer honours the grant: it is ended.
        except ConflictError:
            pass  # the client is incomplete: nothing could be revoked
        except UnprocessableEntityError:
            if not best_effort:
                raise
            logger.warning("business profile binding %s: ended without revoking at Google", binding.id)
    if secret_must_go:
        # Raised, not logged: the consumer's harness retries, then dead-letters
        # with its alert (rule 10) — a disconnected row never keeps a live token.
        secrets_backend.delete_secret(connection_id=binding.id, slot=_REFRESH_SLOT)
    else:
        _drop_secret(binding.id)
    binding.status = GbpBindingStatus.DISCONNECTED
    binding.disconnected_at = utcnow()
    binding.updated_by = actor_id
    binding.version += 1
    _audit(db, binding, action=action, actor_id=actor_id, reason=reason)


def disconnect_offboarded_dealership_bindings(db: Session, *, tenant_id: uuid.UUID) -> int:
    """KAN-218 / KAN-226: on `platform.dealership.offboarded` every live
    Business Profile binding of the Betrieb ends — revoked at Google as a best
    effort (Google unreachable or the grant already refused: logged by id, the
    binding ends all the same), its secret dropped, `disconnected`, audited as
    a system write with the reason. Nothing is pushed to Google again.

    Never commits: the caller is the offboarding consumer, whose harness
    commits this with its processed-event row. A redelivery finds nothing live
    and changes nothing; a commit that failed after a revoke is retried safely
    (Google answers a revoked token `invalid_token`, and a dropped secret is
    already gone). Returns the bindings ended."""

    bindings = list(
        db.scalars(
            select(GbpLocationBinding)
            .where(
                GbpLocationBinding.tenant_id == tenant_id,
                GbpLocationBinding.status != GbpBindingStatus.DISCONNECTED,
            )
            .order_by(GbpLocationBinding.id)
            .with_for_update()
        )
    )
    if not bindings:
        return 0
    connection = client_connection(db)
    for binding in bindings:
        _end(
            db, binding, connection=connection, actor_id=None, action="business_profile_disconnected",
            best_effort=True, reason="dealership_offboarded", secret_must_go=True,
        )
    return len(bindings)


def _drop_secret(binding_id: uuid.UUID) -> None:
    try:
        secrets_backend.delete_secret(connection_id=binding_id, slot=_REFRESH_SLOT)
    except Exception:  # noqa: BLE001 - never the secret store's message
        logger.error("business profile binding %s: the refresh token could not be dropped", binding_id)


def _audit(
    db: Session, binding: GbpLocationBinding, *, action: str, actor_id: uuid.UUID | None, reason: str | None = None
) -> None:
    record_audit_event(
        db,
        entity_type=_ENTITY_TYPE,
        entity_id=binding.id,
        tenant_id=binding.tenant_id,
        action=action,
        actor_id=actor_id,
        after={
            "locationId": str(binding.location_id),
            "status": binding.status.value,
            "googleLocationName": binding.google_location_name,
        },
        reason=reason,
    )


# -- retention -----------------------------------------------------------------------------


def purge_spent_link_states(db: Session, *, now: dt.datetime | None = None) -> int:
    """Deletes every link state used or expired more than a day ago; a live
    link (unused, within its ten minutes) is never touched."""

    cutoff = (now or utcnow()) - LINK_STATE_RETENTION
    result = db.execute(
        delete(GbpLinkState).where(or_(GbpLinkState.expires_at < cutoff, GbpLinkState.consumed_at < cutoff))
    )
    db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]

