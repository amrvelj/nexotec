"""Login/logout via Zitadel (WP-4, ADR-016/ADR-007) + credential management
(issue #8, being retired alongside the login cutover — see
app.platform.services.auth's own docstring).

Zitadel authenticates; it never authorises — access_roles/is_dealer_manager
live on User and are resolved here exactly as the old password login did,
from the User row matched by auth_identity_id, never from anything Zitadel's
ID token or userinfo response carries. The session itself is unchanged: an
httpOnly + Secure + SameSite=strict cookie holding OUR OWN RS256 JWT
(app.core.auth.create_access_token), never the JSON response body and never
a Zitadel-issued token — the browser never holds a service token either way.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
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
from app.platform.models.user import User, UserStatus
from app.platform.schemas.auth import (
    CredentialSetRequest,
    DealershipMembershipSummary,
    LoginResponse,
    LogoutResponse,
    SwitchDealershipRequest,
)
from app.platform.schemas.user import UserRead
from app.platform.services import auth as auth_service
from app.platform.services import dealership as dealership_service
from app.platform.services import user as user_service
from app.platform.services.oidc import OidcClient, OidcError, get_oidc_client

router = APIRouter(tags=["auth"])
settings = get_settings()
logger = logging.getLogger(__name__)

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


def _success_redirect_url() -> str:
    return f"{settings.post_login_redirect_base_url}/"


def _error_redirect_url() -> str:
    return f"{settings.post_login_redirect_base_url}/sign-in-error"


@router.get("/auth/oidc/login")
async def oidc_login(request: Request, oidc: OidcClient = Depends(get_oidc_client)):
    """A full-page browser navigation to Zitadel's hosted login, never an
    XHR/fetch target — this package builds no Nexotec login screen at all
    (see the WP-4 brief). GET, not POST: there is no body, and this has to
    be something a plain <a href> / window.location.href can trigger.
    """

    return await oidc.begin_login(request)


@router.get("/auth/oidc/callback")
async def oidc_callback(
    request: Request,
    oidc: OidcClient = Depends(get_oidc_client),
    db: Session = Depends(get_db),
):
    """Zitadel proves WHO; this endpoint decides WHETHER that person may
    have a Nexotec session and, if so, WHAT they may do here — the second
    and third parts are entirely ours, resolved the same way the retired
    password login resolved them: from the matching User row, never from
    anything Zitadel's token/userinfo response carries.

    On any rejection (denied at Zitadel, no matching User, wrong status)
    there is no User row to hang an audit event on in the "no match" case,
    and no behavior change from today's password login in the "wrong
    status" case (that never audited either) — logged via the structured
    application logger instead, sub only, never any other claim.
    """

    try:
        identity = await oidc.complete_login(request)
    except OidcError as exc:
        logger.warning("oidc_login_failed", extra={"reason": str(exc)})
        return RedirectResponse(_error_redirect_url())

    user = db.scalar(select(User).where(User.auth_identity_id == identity.sub))
    if user is None:
        logger.warning("oidc_login_rejected_not_provisioned", extra={"sub": identity.sub})
        return RedirectResponse(_error_redirect_url())
    if user.status in (UserStatus.SUSPENDED, UserStatus.DEACTIVATED):
        logger.warning("oidc_login_rejected_inactive_user", extra={"sub": identity.sub, "user_id": str(user.id)})
        return RedirectResponse(_error_redirect_url())

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
    # The cookie must be set directly on the Response object this endpoint
    # actually returns — FastAPI only merges an injected `response: Response`
    # dependency's headers/cookies into the final response when the route
    # returns a plain value it wraps itself, not when the route returns its
    # own Response (RedirectResponse here) explicitly.
    redirect = RedirectResponse(_success_redirect_url())
    _set_session_cookie(redirect, token)
    return redirect


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
