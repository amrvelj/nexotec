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

RS256, not HS256 (ADR-007, WP-2 PR-1): this process holds the private key
and is the only issuer; every verifier — including this same process's own
_decode_token, but eventually a second service — checks against the public
key alone, published as JWKS (get_jwks) at GET /.well-known/jwks.json. No
shared secret exists anywhere past this module.
"""

import base64
import dataclasses
import datetime as dt
import enum
import hashlib
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import Cookie, Depends, Header

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError

settings = get_settings()

_ALGORITHM = "RS256"
# .replace("\\n", "\n") tolerates a PEM stored as a single-line env var with
# escaped newlines (docker-compose's .env format has no clean way to hold a
# genuinely multi-line value substituted into a YAML ${...}, so
# docker-compose.yml/Makefile's local dev flow stores it this way — CI's
# workflow YAML and Render's own env-var UI both supply real newlines
# already, where this is a no-op).
_pem_text = settings.jwt_private_key.replace("\\n", "\n")
_loaded_key = serialization.load_pem_private_key(_pem_text.encode("ascii"), password=None)
assert isinstance(_loaded_key, RSAPrivateKey), (
    "DMS_JWT_PRIVATE_KEY must be an RSA private key (RS256) — got a different key type."
)
_PRIVATE_KEY: RSAPrivateKey = _loaded_key
_PUBLIC_KEY: RSAPublicKey = _PRIVATE_KEY.public_key()


def _b64url_uint(value: int) -> str:
    """Unsigned big-endian, base64url, no padding — RFC 7518 §6.3's
    encoding for a JWK RSA key's `n`/`e` members. Not the same as a plain
    base64 encode of the integer's bytes; JWK requires the padding
    stripped.
    """

    length = (value.bit_length() + 7) // 8 or 1
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def _derive_key_id(public_key: RSAPublicKey) -> str:
    """kid derived from the public key itself, not a separate setting —
    it changes exactly when the key does, with nothing else to keep in
    sync during rotation.
    """

    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()[:16]


_KEY_ID = _derive_key_id(_PUBLIC_KEY)


def get_jwks() -> dict:
    """The public half of the signing key, as a JWK Set (RFC 7517).
    Single-key today — this process has exactly one active signing key —
    but the `keys` list is the format's own extension point for rotation
    (publish the new key here before switching create_access_token over to
    it, so verifiers already trust it).
    """

    numbers = _PUBLIC_KEY.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": _ALGORITHM,
                "kid": _KEY_ID,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }

# Name of the httpOnly session cookie the login endpoint (issue #8) sets as
# an alternative to an Authorization header — browser clients can't read an
# httpOnly cookie to build that header themselves, so get_bearer_token below
# accepts either. Defined here (not in api/v1/auth.py) so the read side
# (this file) and the write side (the login endpoint that sets the cookie)
# can't drift on the cookie name.
SESSION_COOKIE_NAME = "dms_session"


class AccessRole(str, enum.Enum):
    """Functional roles (WP-2 PR-2, Roles & Permissions RP-3). `dealer_admin`
    is gone — "runs the dealership" and "may use this module" were the same
    field by accident, not by decision (the RP-1 leak); administration is
    now Principal.is_dealer_manager, orthogonal to this set. aftersales,
    parts, finance and technician are added now even though those modules
    don't exist yet — adding a role later means revisiting every guard that
    was written before it existed.
    """

    PLATFORM_ADMIN = "platform_admin"
    SALES = "sales"
    AFTERSALES = "aftersales"
    PARTS = "parts"
    INVENTORY = "inventory"
    FINANCE = "finance"
    TECHNICIAN = "technician"
    AUDITOR = "auditor"


@dataclasses.dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    roles: frozenset[AccessRole]
    # Administration of THIS principal's own dealership — invite/deactivate
    # users, integrations, dealership settings — and nothing outside it
    # (Roles & Permissions, RP-1/ADR-026). Orthogonal to `roles`: a manager
    # holds whatever functional roles their job also needs, same as anyone
    # else. app.core.permissions is where this flag actually grants write
    # access to a capability; this dataclass only carries the claim.
    is_dealer_manager: bool


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    roles: frozenset[AccessRole] | set[AccessRole],
    is_dealer_manager: bool = False,
    ttl_seconds: int | None = None,
) -> str:
    now = dt.datetime.now(dt.UTC)
    ttl = ttl_seconds if ttl_seconds is not None else settings.jwt_access_token_ttl_seconds
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "roles": sorted(role.value for role in roles),
        "is_dealer_manager": is_dealer_manager,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, _PRIVATE_KEY, algorithm=_ALGORITHM, headers={"kid": _KEY_ID})


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            _PUBLIC_KEY,
            algorithms=[_ALGORITHM],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "tenant_id", "roles", "is_dealer_manager", "exp", "iat"]},
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
            roles=frozenset(AccessRole(role) for role in claims["roles"]),
            is_dealer_manager=bool(claims["is_dealer_manager"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise UnauthorizedError("Access token claims are malformed.") from exc


def require_access_role(*allowed: AccessRole):
    """Dependency factory: `Depends(require_access_role(AccessRole.SALES))`.

    Passes if the principal holds ANY of the listed roles. platform_admin
    always passes, in addition to whatever roles are listed, since it's the
    cross-tenant operator role. This is a plain role-membership gate — it
    does not know about is_dealer_manager or the read/write distinction;
    those live in app.core.permissions's capability checks, which is what
    every write endpoint that used to read `_WRITE_ROLES` now uses instead.
    This one still covers the platform_admin-only endpoints (dealer
    onboarding, the canonical taxonomy), which were never part of that leak.
    """

    allowed_set = set(allowed) | {AccessRole.PLATFORM_ADMIN}

    def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not (principal.roles & allowed_set):
            raise ForbiddenError("None of this principal's roles are permitted to perform this action.")
        return principal

    return _check
