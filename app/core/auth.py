"""Auth boundary: JWT verification + tenant/access-role extraction.

The concrete IdP is unselected (spec cross-cutting #10) — User.auth_identity_id
is a placeholder FK to an external subject and MDM never stores credentials.
This module only defines the *shape* of the trust boundary so downstream
issues (#2 Dealer/User bootstrap) can plug a real token-issuance flow in
without changing how every other endpoint gates access. Until then,
create_access_token() is the only issuer, gated to tests/local dev.

Tenant is always resolved from this JWT claim, never a path/body param
(cross-cutting #6) — request handlers must not accept a tenant_id from the
client for scoping purposes.
"""

import dataclasses
import datetime as dt
import enum
import uuid

import jwt
from fastapi import Cookie, Depends, Header

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError

settings = get_settings()

# Name of the httpOnly session cookie the login endpoint (issue #8) sets as
# an alternative to an Authorization header — browser clients can't read an
# httpOnly cookie to build that header themselves, so get_bearer_token below
# accepts either. Defined here (not in api/v1/auth.py) so the read side
# (this file) and the write side (the login endpoint that sets the cookie)
# can't drift on the cookie name.
SESSION_COOKIE_NAME = "dms_session"


class AccessRole(str, enum.Enum):
    PLATFORM_ADMIN = "platform_admin"
    DEALER_ADMIN = "dealer_admin"
    SALES = "sales"
    INVENTORY = "inventory"
    AUDITOR = "auditor"


@dataclasses.dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    access_role: AccessRole


def create_access_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, access_role: AccessRole, ttl_seconds: int | None = None
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    ttl = ttl_seconds if ttl_seconds is not None else settings.jwt_access_token_ttl_seconds
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "access_role": access_role.value,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "tenant_id", "access_role", "exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Access token is invalid.") from exc


def get_bearer_token(
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if session_cookie:
        return session_cookie
    raise UnauthorizedError("Missing or malformed Authorization header.")


def get_current_principal(token: str = Depends(get_bearer_token)) -> Principal:
    claims = _decode_token(token)
    try:
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tenant_id"]),
            access_role=AccessRole(claims["access_role"]),
        )
    except (ValueError, KeyError) as exc:
        raise UnauthorizedError("Access token claims are malformed.") from exc


def require_access_role(*allowed: AccessRole):
    """Dependency factory: `Depends(require_access_role(AccessRole.SALES))`.

    platform_admin is always allowed through, in addition to whatever roles
    are listed, since it's the cross-tenant operator role.
    """

    allowed_set = set(allowed) | {AccessRole.PLATFORM_ADMIN}

    def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.access_role not in allowed_set:
            raise ForbiddenError(
                f"Role '{principal.access_role.value}' is not permitted to perform this action."
            )
        return principal

    return _check
