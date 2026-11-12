"""Connected accounts (KAN-201; FR-AU-23/24, D-A-15): the one module that
writes `user_connected_account`, and the only reader of its grants
(tests/architecture/test_connected_account_is_never_an_identity.py; nightly
reconciliation reads its actor ids and its revoked grants' references).

A connected account is a grant to act on the user's behalf, never an
identity. The user lists and revokes their own (`/v1/me/connected-accounts`);
a manager lists a user's — provider, scopes and status, never the account's
address, its usage or any content — and may revoke
(`/v1/dealerships/{id}/users/{user_id}/connected-accounts`); deactivating a
user revokes every one (FR-AU-24). Linking is the Outlook integration's, not
built yet: until then no row exists anywhere.

Revoking marks the row in Nexotec's own transaction (it is never deleted)
and, in that same transaction, publishes `platform.connected_account.revoked`
(KAN-412; rule 4). Ending the grant at the provider — and dropping its
secret from the secrets manager — is the provider's registered revoker's
job, run by the consumer `platform.connected_account_provider_revocation`
(`end_at_provider`) after the revoke committed: a provider that is down
never undoes a revoke or a leaver's deactivation. The revoker receives a
`RevokedGrant` — plain data, never the row — and is registered through
`app.platform.public.register_grant_revoker` (the integration context's
door). When it returns, the end is recorded: the row's `secret_ref` is
dropped (the secret it pointed to is gone) with an audit row. When it
raises, the consumer raises: the outbox redelivers on its backoff and
finally dead-letters, which alerts; the row keeps its `secret_ref` so the
secret can still be found. Nightly reconciliation reports a revoked grant
still holding a reference a day after the revoke (rule 10), and the daily
repair after it (`repair_revoked_grants`, KAN-559) runs `end_at_provider`
again for every one: a provider outage longer than the outbox's retries, or
a grant revoked before its provider's revoker was registered, is ended the
next night without anyone replaying the event. No revoker is registered
yet — the Outlook integration registers Microsoft's when it links the first
account.
"""

import dataclasses
import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.connected_account import (
    ConnectedAccountProvider,
    ConnectedAccountRevokedReason,
    ConnectedAccountStatus,
    UserConnectedAccount,
)
from app.platform.models.user import User
from app.platform.services import auth_events

logger = logging.getLogger(__name__)

REASON_USER = ConnectedAccountRevokedReason.USER
REASON_MANAGER = ConnectedAccountRevokedReason.MANAGER
REASON_DEACTIVATED = ConnectedAccountRevokedReason.DEACTIVATED

REVOKED_EVENT = "platform.connected_account.revoked"
PRODUCER = "platform"


@dataclasses.dataclass(frozen=True)
class RevokedGrant:
    """What a provider's revoker receives (KAN-412): plain data, never the
    platform row. `secret_ref` is where the grant's tokens live in the
    secrets manager — a reference, not a secret, handed over so the revoker
    can end the grant and drop the secret; it is still kept out of `repr`, so
    a log line of the grant never prints it, and it is never returned by an
    endpoint."""

    connected_account_id: uuid.UUID
    provider: ConnectedAccountProvider
    external_account_id: str
    secret_ref: str | None = dataclasses.field(repr=False)


# provider -> ends the grant at the provider and drops its secret from the
# secrets manager; returns when both are done, raises otherwise. Run by the
# consumer after the revoke committed, at least once per revoke — so ending
# an already-ended grant must succeed. Registered through
# app.platform.public by the integration that links the provider's
# accounts (the Outlook integration, not built).
GrantRevoker = Callable[[RevokedGrant], None]
_REVOKERS: dict[ConnectedAccountProvider, GrantRevoker] = {}


def register_grant_revoker(provider: ConnectedAccountProvider, revoker: GrantRevoker) -> None:
    _REVOKERS[provider] = revoker


def unregister_grant_revoker(provider: ConnectedAccountProvider) -> None:
    _REVOKERS.pop(provider, None)


def list_for_user(db: Session, *, user_id: uuid.UUID) -> list[UserConnectedAccount]:
    """Every grant the user holds or held, newest first — a revoked one stays
    listed with its stamps."""

    return list(
        db.scalars(
            select(UserConnectedAccount)
            .where(UserConnectedAccount.user_id == user_id)
            .order_by(UserConnectedAccount.linked_at.desc(), UserConnectedAccount.id)
        ).all()
    )


def get_for_update_or_404(db: Session, *, user_id: uuid.UUID, account_id: uuid.UUID) -> UserConnectedAccount:
    """The user's own grant, locked; another user's id answers 404."""

    account = db.scalar(
        select(UserConnectedAccount)
        .where(UserConnectedAccount.id == account_id, UserConnectedAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise NotFoundError(f"Connected account {account_id} was not found.")
    return account


def revoke(
    db: Session,
    *,
    account: UserConnectedAccount,
    user: User,
    actor_id: uuid.UUID | None,
    reason: ConnectedAccountRevokedReason,
) -> UserConnectedAccount:
    """Ends one grant in Nexotec: the row is marked revoked (never deleted),
    and the revocation is an auth event and an audit row naming the actor —
    the `security` notification to the user is KAN-216's — and the outbox
    event `platform.connected_account.revoked` the provider's end hangs on
    (KAN-412). Does not commit: the caller's commit lands all of it, or
    none."""

    if account.status == ConnectedAccountStatus.REVOKED:
        raise ConflictError(
            "This connected account is already revoked.",
            details={"reason": "already_revoked", "currentStatus": account.status.value},
        )
    before = account.status.value
    account.status = ConnectedAccountStatus.REVOKED
    account.revoked_at = utcnow()
    account.revoked_by = actor_id
    account.revoked_reason = reason
    account.updated_by = actor_id
    account.version += 1
    details = {"connectedAccountId": str(account.id), "provider": account.provider.value, "reason": reason.value}
    auth_events.record(
        db,
        AuthEventKind.CONNECTED_ACCOUNT_REVOKED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        actor_id=actor_id,
        details=details,
    )
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="connected_account_revoked",
        actor_id=actor_id,
        before={"connected_account_id": str(account.id), "status": before},
        after={"connected_account_id": str(account.id), "status": account.status.value},
        reason=reason.value,
    )
    outbox.publish(
        db,
        outbox.OutboxEvent(
            event_type=REVOKED_EVENT,
            tenant_id=user.tenant_id,
            producer=PRODUCER,
            aggregate_type="connected_account",
            aggregate_id=account.id,
            # Ids and closed codes only: the consumer reads the reference
            # from the row, so the outbox never carries it.
            payload={
                "connectedAccountId": str(account.id),
                "userId": str(user.id),
                "provider": account.provider.value,
                "reason": reason.value,
            },
            occurred_at=account.revoked_at,
        ),
    )
    return account


def revoke_all(
    db: Session, *, user: User, actor_id: uuid.UUID | None, reason: ConnectedAccountRevokedReason
) -> int:
    """FR-AU-24: every live grant of the user ends (the deactivation), each
    with its own event. Does not commit; returns how many ended."""

    live = db.scalars(
        select(UserConnectedAccount)
        .where(
            UserConnectedAccount.user_id == user.id,
            UserConnectedAccount.status != ConnectedAccountStatus.REVOKED,
        )
        .order_by(UserConnectedAccount.id)
        .with_for_update()
    ).all()
    for account in live:
        revoke(db, account=account, user=user, actor_id=actor_id, reason=reason)
    return len(live)


def end_at_provider(db: Session, *, account_id: uuid.UUID) -> bool:
    """The consumer's step (KAN-412), after the revoke committed: the
    provider's revoker ends the grant there and drops its secret, and the
    end is recorded — `secret_ref` dropped, the version moved, an audit row.
    Does not commit (the consumer harness commits it with its
    processed-event row). Returns whether it recorded an end.

    A revoker that raises is not caught: the consumer fails, the outbox
    redelivers and finally dead-letters (an alert), and the row keeps its
    `secret_ref`. Nothing to end — the row gone, not revoked, or holding no
    reference (already ended) — is no call. No revoker registered for the
    provider is no call either, logged at WARNING: the grant is ended in
    Nexotec only, and nightly reconciliation reports the reference still
    held."""

    # One row, locked: a second delivery waits here and then finds the
    # reference gone. A single-row write, so no user lock first (KAN-380).
    account = db.scalar(
        select(UserConnectedAccount)
        .where(UserConnectedAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None or account.status != ConnectedAccountStatus.REVOKED or account.secret_ref is None:
        return False
    context = {"connectedAccountId": str(account.id), "userId": str(account.user_id)}
    revoker = _REVOKERS.get(account.provider)
    if revoker is None:
        logger.warning(
            "platform.connected_account: no revoker for provider %s; grant %s ended in Nexotec only, its secret_ref is kept",
            account.provider.value,
            account.id,
            extra=context,
        )
        return False
    revoker(
        RevokedGrant(
            connected_account_id=account.id,
            provider=account.provider,
            external_account_id=account.external_account_id,
            secret_ref=account.secret_ref,
        )
    )
    account.secret_ref = None
    account.version += 1
    tenant_id = db.scalar(select(User.tenant_id).where(User.id == account.user_id))
    record_audit_event(
        db,
        entity_type="user",
        entity_id=account.user_id,
        tenant_id=tenant_id,
        action="connected_account_provider_ended",
        actor_id=None,
        before={"connected_account_id": str(account.id), "secret_held": True},
        after={"connected_account_id": str(account.id), "secret_held": False},
        reason=account.provider.value,
    )
    return True


@dataclasses.dataclass(frozen=True)
class GrantRepairRun:
    """What one night's repair did (KAN-559): grants ended at the provider,
    grants whose provider has no registered revoker (left as they were), and
    grants whose revoker raised (left holding their reference, tried again
    the next night)."""

    ended: int
    no_revoker: int
    failed: int


def repair_revoked_grants(db: Session) -> GrantRepairRun:
    """The daily repair (KAN-559; ADR-118, decided as option (a)): every
    revoked grant still holding a reference gets `end_at_provider` again,
    each in a transaction of its own, so one provider still refusing never
    holds back the others. A revoker that raises is rolled back and logged at
    ERROR; the grant keeps its `secret_ref` and reconciliation keeps
    reporting it until a night succeeds. The row lock in `end_at_provider`
    makes a delivery of the event running at the same moment a no-op on the
    second arrival — the provider is asked once.

    Commits (a daily job owns its transactions)."""

    account_ids = list(
        db.scalars(
            select(UserConnectedAccount.id)
            .where(
                UserConnectedAccount.status == ConnectedAccountStatus.REVOKED,
                UserConnectedAccount.secret_ref.is_not(None),
            )
            .order_by(UserConnectedAccount.revoked_at, UserConnectedAccount.id)
        )
    )
    db.rollback()
    ended = no_revoker = failed = 0
    for account_id in account_ids:
        provider = db.scalar(select(UserConnectedAccount.provider).where(UserConnectedAccount.id == account_id))
        try:
            if end_at_provider(db, account_id=account_id):
                db.commit()
                ended += 1
            else:
                db.rollback()
                if provider is not None and provider not in _REVOKERS:
                    no_revoker += 1
        except Exception:  # one provider's refusal must not stop the others
            db.rollback()
            failed += 1
            # The exception names no token material (the revoker's contract); the id says which grant.
            logger.exception(
                "platform.connected_account.repair: the provider refused to end grant %s; kept, tried again tomorrow",
                account_id,
                extra={"connectedAccountId": str(account_id)},
            )
    return GrantRepairRun(ended=ended, no_revoker=no_revoker, failed=failed)
