"""Zitadel OIDC integration (WP-4, ADR-016/ADR-007). Wraps authlib rather
than having route code depend on its OAuth registry directly, so tests
substitute a fake at the FastAPI dependency boundary — same pattern as
Depends(get_db) and the outbox's InProcessTransport, not a new idiom.

Zitadel authenticates; it never authorises. This module returns only
sub/email/name from Zitadel's userinfo response — access_roles/
is_dealer_manager live on User and are resolved by the caller
(app.platform.api.auth), never derived from anything here or from any
custom claim Zitadel might include.
"""

import dataclasses
from typing import Protocol

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from starlette.responses import RedirectResponse

from app.core.config import get_settings

settings = get_settings()

_oauth = OAuth()
_oauth.register(
    "zitadel",
    server_metadata_url=f"{settings.zitadel_issuer}/.well-known/openid-configuration",
    client_id=settings.zitadel_client_id,
    client_secret=settings.zitadel_client_secret,
    client_kwargs={"scope": "openid profile email"},
)
# .register() only constructs the client and stores this config — confirmed
# against authlib's source that it makes no network call itself; the
# discovery document is fetched lazily, inside authorize_redirect/
# authorize_access_token, on first real use. Importing this module (and so
# app.main, which every test and several CI jobs import transitively) must
# never require network access to Zitadel — see tests/test_oidc.py's own
# import-is-offline assertion.
_ZITADEL_CLIENT = _oauth.zitadel


@dataclasses.dataclass(frozen=True)
class ZitadelIdentity:
    sub: str
    email: str | None
    name: str | None


class OidcError(Exception):
    """Any failure in the OIDC exchange — denied consent, a state/nonce
    mismatch, an invalid token, or (the one that matters most here) a
    userinfo call failing because Zitadel has revoked the account. Callers
    never need to distinguish these: any OidcError means "no session,"
    full stop.
    """


class OidcClient(Protocol):
    async def begin_login(self, request: Request) -> RedirectResponse: ...
    async def complete_login(self, request: Request) -> ZitadelIdentity: ...


class AuthlibOidcClient:
    """The real implementation, backed by authlib and a live Zitadel tenant."""

    def __init__(self, client=_ZITADEL_CLIENT) -> None:
        self._client = client

    async def begin_login(self, request: Request) -> RedirectResponse:
        return await self._client.authorize_redirect(request, settings.zitadel_redirect_uri)

    async def complete_login(self, request: Request) -> ZitadelIdentity:
        try:
            token = await self._client.authorize_access_token(request)
            # A LIVE call, not token["userinfo"] (which only reflects the ID
            # token's claims as of issuance) — Zitadel resolves this against
            # current account state, so a user revoked after the ID token was
            # minted but before this call still gets rejected here. This is
            # the actual mechanism behind "a revoked Zitadel user cannot
            # obtain a session," not the ID token validation a line above it.
            userinfo = await self._client.userinfo(token=token)
        except Exception as exc:
            # authlib/httpx raise several distinct exception types here
            # (OAuthError, a state/nonce mismatch error, httpx's
            # HTTPStatusError from a non-2xx userinfo response) — all of
            # them collapse to the same thing for a caller: no session.
            raise OidcError(str(exc)) from exc

        sub = userinfo.get("sub")
        if not sub:
            raise OidcError("Zitadel userinfo response had no 'sub' claim.")
        return ZitadelIdentity(sub=sub, email=userinfo.get("email"), name=userinfo.get("name"))


def get_oidc_client() -> OidcClient:
    return AuthlibOidcClient()
