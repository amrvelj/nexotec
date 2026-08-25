"""Test double for app.platform.services.oidc.OidcClient (WP-4) — the
fake this repo's tests inject via the FastAPI dependency override
(get_oidc_client), same pattern as get_db and the outbox's
InProcessTransport. Never talks to a real Zitadel tenant; a test scripts
exactly what the "exchange" should resolve to.
"""

from fastapi import Request
from starlette.responses import RedirectResponse

from app.platform.services.oidc import OidcError, ZitadelIdentity

FAKE_AUTHORIZATION_URL = "https://fake-zitadel.example/oauth/v2/authorize?fake=1"


class FakeOidcClient:
    def __init__(self) -> None:
        self._next_identity: ZitadelIdentity | None = None
        self._next_error: OidcError | None = None

    def enqueue_identity(self, identity: ZitadelIdentity) -> None:
        self._next_identity = identity
        self._next_error = None

    def enqueue_error(self, error: OidcError) -> None:
        self._next_error = error
        self._next_identity = None

    async def begin_login(self, request: Request) -> RedirectResponse:
        return RedirectResponse(FAKE_AUTHORIZATION_URL)

    async def complete_login(self, request: Request) -> ZitadelIdentity:
        if self._next_error is not None:
            raise self._next_error
        if self._next_identity is not None:
            return self._next_identity
        raise OidcError("FakeOidcClient.complete_login called with nothing enqueued.")
