"""Connected accounts (KAN-201; FR-AU-23/24, D-A-15): the one module that
writes `user_connected_account`, and the only reader of its grants
(tests/architecture/test_connected_account_is_never_an_identity.py; nightly
reconciliation reads its actor ids).

A connected account is a grant to act on the user's behalf, never an
identity. The user lists and revokes their own (`/v1/me/connected-accounts`);
a manager lists a user's — provider, scopes and status, never the account's
address, its usage or any content — and may revoke
(`/v1/dealerships/{id}/users/{user_id}/connected-accounts`); deactivating a
user revokes every one (FR-AU-24). Linking is the Outlook integration's, not
built yet: until then no row exists anywhere.

Revoking marks the row in Nexotec's own transaction (it is never deleted).
Ending the grant at the provider — and dropping its secret from the secrets
manager — is the provider's registered revoker's job, run by the caller
**after** the commit (`end_at_providers`): a provider that is down never
undoes a revoke or a leaver's deactivation; its failure is logged at ERROR
and the row keeps its `secret_ref`, so the secret can still be found and
removed. No revoker is registered yet — the Outlook integration registers
Microsoft's when it links the first account.
"""

import logging
import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

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

# provider -> ends the grant at the provider and drops its secret; called
# after the revoke committed, with the revoked row. Registered by the
# integration that links the provider's accounts (the Outlook integration,
# not built).
GrantRevoker = Callable[[UserConnectedAccount], None]
# What a revoke hands back for `end_at_providers` — named so callers need
# not name the model (the architecture test keeps it in this module).
RevokedGrants = list[UserConnectedAccount]
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
    the `security` notification to the user is KAN-216's. Does not commit;
    the caller runs `end_at_providers` after its commit."""

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
    return account


def revoke_all(
    db: Session, *, user: User, actor_id: uuid.UUID | None, reason: ConnectedAccountRevokedReason
) -> RevokedGrants:
    """FR-AU-24: every live grant of the user ends (the deactivation). Does
    not commit; returns the revoked rows for `end_at_providers`."""

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
    return list(live)


def end_at_providers(accounts: Sequence[UserConnectedAccount]) -> None:
    """After the revoke committed: each provider's revoker ends the grant at
    the provider and drops its secret. A failure is logged at ERROR and
    never raised — the grant is already ended in Nexotec, and the row keeps
    its `secret_ref` for the clean-up."""

    for account in accounts:
        revoker = _REVOKERS.get(account.provider)
        context = {"connectedAccountId": str(account.id), "userId": str(account.user_id)}
        if revoker is None:
            logger.warning(
                "platform.connected_account: no revoker for provider %s; grant %s ended in Nexotec only",
                account.provider.value,
                account.id,
                extra=context,
            )
            continue
        try:
            revoker(account)
        except Exception:  # the provider's failure never undoes the revoke
            logger.exception(
                "platform.connected_account: the provider did not end grant %s; its secret_ref is kept",
                account.id,
                extra=context,
            )
