"""Identity administration of a dealership (KAN-196), split from
`dealerships.py` (KAN-668, a pure move): a user's invitation, connected
accounts and login status, the Betrieb's auth events and its sign-in policy.
Mounted after `dealership_memberships.py` in `app/api/v1/__init__.py`.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import Principal, require_mfa_session
from app.core.concurrency import check_version, require_if_match
from app.core.errors import ForbiddenError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import (
    require_read,
    require_write,
)
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.api.auth import client_ip
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.user import UserStatus
from app.platform.reference.services.mailer import PlatformMailer, get_mailer
from app.platform.schemas.auth import (
    AuthConfigRead,
    AuthConfigUpdate,
    AuthEventList,
    AuthEventRead,
    LoginStatusRead,
    SessionPolicyBounds,
)
from app.platform.schemas.me import ConnectedAccountManagerListRead, ConnectedAccountManagerRead
from app.platform.services import (
    auth_events,
    connected_account,
    identity,
    session_policy,
)
from app.platform.services import dealership as dealership_service
from app.platform.services import user as user_service
from app.platform.services import user_reads as user_reads_service

router = APIRouter(tags=["dealerships"], route_class=IdempotentRoute)


# --- Identity administration (KAN-196: FR-AU-10/11/12/17/21/25) -------------------------


@router.post("/dealerships/{dealership_id}/users/{user_id}/invitations", status_code=202, dependencies=[Depends(require_mfa_session())])
def reissue_invitation(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
    mailer: PlatformMailer = Depends(get_mailer),
):
    """FR-AU-13: a fresh single-use invitation (the previous one is
    consumed) — never a password — for a user still `invited` (onboarding,
    the manager's own business). For an account that is no longer `invited`
    a new invitation is account recovery: platform staff's, with a stated
    reason, at `POST /v1/platform/users/{id}/recovery/reissue-invitation`
    (KAN-202; Anto, 2026-10-22: one door for recovery)."""

    require_tenant_match(dealership_id, principal)
    # The user row before the tokens (KAN-380/KAN-400): the invitation stamps
    # `invited_at` (KAN-200) after ending the live invitations, and an
    # acceptance holds the user row before it consumes its token.
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    user_service.assert_user_is_not_deactivated(user)
    if user.status != UserStatus.INVITED:
        # D-A-16 / FR-AU-25: once the account is no longer `invited`, a new
        # invitation is a recovery — platform staff's, with a reason, at the
        # recovery door; staff too go there (KAN-202).
        raise ForbiddenError(
            "Re-inviting a user who already accepted is account recovery, which platform staff handle.",
            details={"recovery": "reissue-invitation"},
        )
    identity.issue_invitation(db, mailer, user=user, actor_id=principal.user_id, ip_address=client_ip(request))
    db.commit()


@router.get(
    "/dealerships/{dealership_id}/users/{user_id}/connected-accounts", response_model=ConnectedAccountManagerListRead
)
def list_user_connected_accounts(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("connected_accounts")),
    db: Session = Depends(get_db),
):
    """FR-AU-24 / D-A-15: the manager sees that a user's grant exists — its
    provider, scopes and status, never the account's address or its usage,
    and never mail or calendar content (nothing here can reach it). Linking
    is the user's own, in My account."""

    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    rows = connected_account.list_for_user(db, user_id=user.id)
    return ConnectedAccountManagerListRead(
        items=[ConnectedAccountManagerRead.model_validate(r, from_attributes=True) for r in rows]
    )


@router.delete(
    "/dealerships/{dealership_id}/users/{user_id}/connected-accounts/{account_id}",
    response_model=ConnectedAccountManagerRead,
)
def revoke_user_connected_account(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("connected_accounts")),
    db: Session = Depends(get_db),
):
    """The manager may revoke a user's grant (D-A-15: "may revoke only");
    the row stays, revoked — the user sees it was a manager
    (`revokedReason`), the Betrieb's auth events name who."""

    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_for_update_or_404(db, dealership_id, user_id)  # KAN-380
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    account = connected_account.get_for_update_or_404(db, user_id=user.id, account_id=account_id)
    check_version(account.version, if_match, entity_name="ConnectedAccount")
    connected_account.revoke(
        db, account=account, user=user, actor_id=principal.user_id, reason=connected_account.REASON_MANAGER
    )
    db.commit()
    db.refresh(account)
    return ConnectedAccountManagerRead.model_validate(account, from_attributes=True)


@router.get("/dealerships/{dealership_id}/users/{user_id}/login-status", response_model=LoginStatusRead, dependencies=[Depends(require_mfa_session())])
def get_login_status(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_users")),
    db: Session = Depends(get_db),
):
    """FR-AU-10: the manager sees that a user is locked out; nothing more —
    and never a staff account's (KAN-97)."""

    require_tenant_match(dealership_id, principal)
    user = user_reads_service.get_user_or_404(db, dealership_id, user_id)
    user_service.assert_may_touch(user=user, actor_roles=principal.roles)
    return LoginStatusRead(**identity.login_status(db, user))


@router.get("/dealerships/{dealership_id}/auth-events", response_model=AuthEventList)
def list_auth_events(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("audit_logs")),
    db: Session = Depends(get_db),
):
    """FR-AU-21: the manager reads the authentication stream of his own
    Betrieb — the newest 200 events."""

    require_tenant_match(dealership_id, principal)
    rows = auth_events.list_for_dealership(db, dealership_id=dealership_id)
    return AuthEventList(
        items=[
            AuthEventRead(
                id=row.id,
                kind=row.kind.value,
                user_id=row.user_id,
                actor_id=row.actor_id,
                ip_address=row.ip_address,
                occurred_at=row.occurred_at,
                details=row.details,
            )
            for row in rows
        ]
    )


@router.get("/dealerships/{dealership_id}/auth-config", response_model=AuthConfigRead)
def get_auth_config(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    return _auth_config_read(identity.read_auth_config(db, dealership_id))


@router.put(
    "/dealerships/{dealership_id}/auth-config",
    response_model=AuthConfigRead,
    dependencies=[Depends(require_mfa_session())],
)
def set_auth_config(
    dealership_id: uuid.UUID,
    body: AuthConfigUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    """D-A-09 / FR-AU-11: the manager decides whether his Betrieb enforces a
    second factor. Platform staff are always enforced (D-A-36), whatever
    this says.

    KAN-229 (D-A-37, FR-AU-28): the manager also sets the Betrieb's session
    policy, stricter than the platform's, never looser (422 with a stable
    `details.reason` outside the bounds). A field left out of the body is
    unchanged; null sets it back to the platform value. It applies to
    sessions minted from now on — a live session keeps the bounds it was
    minted with until its next switch or sign-in."""

    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    sent = body.model_fields_set
    session_policy.validate(
        idle_minutes=body.session_idle_minutes if "session_idle_minutes" in sent else None,
        absolute_hours=body.session_absolute_hours if "session_absolute_hours" in sent else None,
    )
    config = identity.auth_config_for(db, dealership_id)
    check_version(config.version, if_match, entity_name="DealershipAuthConfig")
    wanted = {"mfa_enforced": body.mfa_enforced}
    for field in ("session_idle_minutes", "session_absolute_hours"):
        if field in sent:
            wanted[field] = getattr(body, field)
    before = {field: getattr(config, field) for field in wanted if getattr(config, field) != wanted[field]}
    if before:
        record_audit_event(
            db,
            entity_type="dealership",
            entity_id=dealership_id,
            tenant_id=dealership_id,
            action="auth_config_set",
            actor_id=principal.user_id,
            before=before,
            after={field: wanted[field] for field in before},
        )
        for field in before:
            setattr(config, field, wanted[field])
        config.updated_by = principal.user_id
        config.version += 1
    db.commit()
    return _auth_config_read(config)


def _auth_config_read(config: DealershipAuthConfig) -> AuthConfigRead:
    return AuthConfigRead(
        auth_method=config.auth_method.value,
        mfa_enforced=config.mfa_enforced,
        session_idle_minutes=config.session_idle_minutes,
        session_absolute_hours=config.session_absolute_hours,
        session_bounds=SessionPolicyBounds(
            idle_minutes_min=session_policy.IDLE_MINUTES_MIN,
            idle_minutes_max=session_policy.IDLE_MINUTES_MAX,
            absolute_hours_min=session_policy.ABSOLUTE_HOURS_MIN,
            absolute_hours_max=session_policy.ABSOLUTE_HOURS_MAX,
            platform_idle_minutes=session_policy.platform_policy().idle_minutes,
        ),
        version=config.version,
    )
