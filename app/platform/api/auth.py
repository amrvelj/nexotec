"""Login/logout + credential management (issue #8).

Interim internal login: bcrypt-hashed credentials in a dedicated table
(app.platform.models.credential), separate from User's business record. Explicitly
throwaway — replaced, not extended, once a real external IdP (Auth0/Entra/
Okta) becomes an actual multi-dealership-SSO requirement. The JWT is delivered
only as an httpOnly + Secure + SameSite=strict cookie, never in the JSON
response body — an XSS-exposed token in a JS-readable place (localStorage,
a response the client stores itself) isn't an acceptable trade against
standard CSRF handling, given this sits in front of customer PII.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import (
    SESSION_COOKIE_NAME,
    AccessRole,
    Principal,
    create_access_token,
    get_current_principal,
)
from app.core.config import get_settings
from app.core.errors import ForbiddenError
from app.core.permissions import require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.models.dealership import Dealership
from app.platform.schemas.auth import (
    CredentialSetRequest,
    DealershipMembershipSummary,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    SwitchDealershipRequest,
)
from app.platform.schemas.user import UserRead
from app.platform.services import auth as auth_service
from app.platform.services import dealership as dealership_service
from app.platform.services import user as user_service

router = APIRouter(tags=["auth"])
settings = get_settings()

# Typed dict[str, Any], not inferred — mypy can't distribute a spread of an
# untyped dict literal across set_cookie/delete_cookie's differently-typed
# keyword parameters (bool for httponly/secure, str for samesite/path).
_COOKIE_KWARGS: dict[str, Any] = {"httponly": True, "secure": True, "samesite": "strict", "path": "/"}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=settings.jwt_access_token_ttl_seconds, **_COOKIE_KWARGS
    )


def _membership_ids(db: Session, *, user_id: uuid.UUID, home_dealership_id: uuid.UUID) -> frozenset[uuid.UUID]:
    return frozenset({home_dealership_id}) | user_service.list_membership_dealership_ids(db, user_id=user_id)


def _login_response(db: Session, *, user, active_dealership: Dealership, membership_ids) -> LoginResponse:
    rows = dealership_service.list_dealerships_by_ids(db, membership_ids)
    # Active dealership first, then the rest — same list every time for a
    # given membership set rather than whatever order the IN(...) returned.
    rows.sort(key=lambda d: (d.id != active_dealership.id, d.legal_name))
    return LoginResponse(
        user=UserRead.model_validate(user, from_attributes=True),
        active_dealership=DealershipMembershipSummary.model_validate(active_dealership, from_attributes=True),
        memberships=[DealershipMembershipSummary.model_validate(d, from_attributes=True) for d in rows],
    )


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, email=body.email, password=body.password)
    dealership = dealership_service.get_dealership_or_404(db, user.tenant_id)
    membership_ids = _membership_ids(db, user_id=user.id, home_dealership_id=user.tenant_id)
    token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        group_id=dealership.dealer_group_id,
        memberships=membership_ids,
        roles=frozenset(AccessRole(role) for role in user.access_roles),
        is_dealer_manager=user.is_dealer_manager,
    )
    _set_session_cookie(response, token)
    return _login_response(db, user=user, active_dealership=dealership, membership_ids=membership_ids)


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, **_COOKIE_KWARGS)
    return LogoutResponse()


@router.get("/auth/me", response_model=LoginResponse)
def get_current_session_user(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    """Lets a browser client restore "logged in as X" state after a page
    reload — the httpOnly cookie survives, but the login response body's
    `user` object doesn't (nothing else holds it client-side).
    """

    user = user_service.get_own_user_or_404(db, principal.user_id)
    dealership = dealership_service.get_dealership_or_404(db, principal.tenant_id)
    return _login_response(db, user=user, active_dealership=dealership, membership_ids=principal.memberships)


@router.post("/auth/switch-dealership", response_model=LoginResponse)
def switch_dealership(
    body: SwitchDealershipRequest,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Re-issues the session token with a different active dealership
    (WP-3 PR-3). There is no cross-dealership WRITE, ever — to act on a
    sister dealership's data the user switches active dealership here, they
    don't gain a second simultaneous tenant_id. group_id is re-resolved from
    the TARGET dealership's own dealer_group_id, not assumed unchanged from
    the caller's current token, since a membership could in principle span
    two different groups.
    """

    if body.dealership_id not in principal.memberships:
        raise ForbiddenError("This dealership is not one of your memberships.")

    dealership = dealership_service.get_dealership_or_404(db, body.dealership_id)
    user = user_service.get_own_user_or_404(db, principal.user_id)
    membership_ids = _membership_ids(db, user_id=user.id, home_dealership_id=user.tenant_id)
    token = create_access_token(
        user_id=user.id,
        tenant_id=dealership.id,
        group_id=dealership.dealer_group_id,
        memberships=membership_ids,
        roles=frozenset(AccessRole(role) for role in user.access_roles),
        is_dealer_manager=user.is_dealer_manager,
    )
    _set_session_cookie(response, token)
    return _login_response(db, user=user, active_dealership=dealership, membership_ids=membership_ids)


@router.post("/dealerships/{dealership_id}/users/{user_id}/credential", status_code=204)
def set_user_credential(
    dealership_id: uuid.UUID,
    user_id: uuid.UUID,
    body: CredentialSetRequest,
    principal: Principal = Depends(require_write("dealership_users")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    user = user_service.get_user_or_404(db, dealership_id, user_id)
    auth_service.set_credential(db, user=user, password=body.password, actor_id=principal.user_id)
