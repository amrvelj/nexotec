"""Login/logout + credential management (issue #8).

Interim internal login: bcrypt-hashed credentials in a dedicated table
(app.models.credential), separate from User's business record. Explicitly
throwaway — replaced, not extended, once a real external IdP (Auth0/Entra/
Okta) becomes an actual multi-dealer-SSO requirement. The JWT is delivered
only as an httpOnly + Secure + SameSite=strict cookie, never in the JSON
response body — an XSS-exposed token in a JS-readable place (localStorage,
a response the client stores itself) isn't an acceptable trade against
standard CSRF handling, given this sits in front of customer PII.
"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import (
    SESSION_COOKIE_NAME,
    AccessRole,
    Principal,
    create_access_token,
    get_current_principal,
    require_access_role,
)
from app.core.config import get_settings
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.schemas.auth import CredentialSetRequest, LoginRequest, LoginResponse, LogoutResponse
from app.schemas.user import UserRead
from app.services import auth as auth_service
from app.services import user as user_service

router = APIRouter(tags=["auth"])
settings = get_settings()

_COOKIE_KWARGS = {"httponly": True, "secure": True, "samesite": "strict", "path": "/"}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=settings.jwt_access_token_ttl_seconds, **_COOKIE_KWARGS
    )


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, email=body.email, password=body.password)
    token = create_access_token(user_id=user.id, tenant_id=user.tenant_id, access_role=user.access_role)
    _set_session_cookie(response, token)
    return LoginResponse(user=UserRead.model_validate(user, from_attributes=True))


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

    user = user_service.get_user_or_404(db, principal.tenant_id, principal.user_id)
    return LoginResponse(user=UserRead.model_validate(user, from_attributes=True))


@router.post("/dealers/{dealer_id}/users/{user_id}/credential", status_code=204)
def set_user_credential(
    dealer_id: uuid.UUID,
    user_id: uuid.UUID,
    body: CredentialSetRequest,
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN)),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    user = user_service.get_user_or_404(db, dealer_id, user_id)
    auth_service.set_credential(db, user=user, password=body.password, actor_id=principal.user_id)
