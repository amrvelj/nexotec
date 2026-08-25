"""Unit tests for app.platform.services.oidc.AuthlibOidcClient — the
authlib boundary is mocked directly (constructor injection), not real
network. See tests/test_login.py for the FakeOidcClient-backed route-level
tests that prove the callback endpoint itself behaves correctly end to end.
"""

import importlib
import socket
from unittest.mock import AsyncMock

import pytest

from app.platform.services.oidc import AuthlibOidcClient, OidcError


@pytest.mark.asyncio
async def test_complete_login_maps_userinfo_fields():
    fake_client = AsyncMock()
    fake_client.authorize_access_token.return_value = {"access_token": "at", "id_token": "it"}
    fake_client.userinfo.return_value = {
        "sub": "zitadel-sub-1", "email": "anna@example.ch", "name": "Anna Muster",
    }

    identity = await AuthlibOidcClient(client=fake_client).complete_login(request=object())

    assert identity.sub == "zitadel-sub-1"
    assert identity.email == "anna@example.ch"
    assert identity.name == "Anna Muster"
    fake_client.userinfo.assert_awaited_once_with(token={"access_token": "at", "id_token": "it"})


@pytest.mark.asyncio
async def test_complete_login_wraps_a_failing_token_exchange():
    fake_client = AsyncMock()
    fake_client.authorize_access_token.side_effect = RuntimeError("state mismatch")

    with pytest.raises(OidcError):
        await AuthlibOidcClient(client=fake_client).complete_login(request=object())


@pytest.mark.asyncio
async def test_complete_login_wraps_a_failing_live_userinfo_call():
    """The actual mechanism behind "a revoked Zitadel user cannot obtain a
    session": the ID token exchange can succeed (it reflects claims as of
    issuance) while the live userinfo call fails because the account was
    revoked in the meantime.
    """

    fake_client = AsyncMock()
    fake_client.authorize_access_token.return_value = {"access_token": "at"}
    fake_client.userinfo.side_effect = RuntimeError("401 from Zitadel: account disabled")

    with pytest.raises(OidcError):
        await AuthlibOidcClient(client=fake_client).complete_login(request=object())


@pytest.mark.asyncio
async def test_complete_login_rejects_a_userinfo_response_with_no_sub():
    fake_client = AsyncMock()
    fake_client.authorize_access_token.return_value = {"access_token": "at"}
    fake_client.userinfo.return_value = {"email": "anna@example.ch"}

    with pytest.raises(OidcError):
        await AuthlibOidcClient(client=fake_client).complete_login(request=object())


def test_importing_the_oidc_module_requires_no_network_access(monkeypatch):
    """.register() must only construct the client and store config —
    confirmed against authlib's source that the discovery document is
    fetched lazily on first real use, never at import/registration time.
    This whole test suite (every test transitively imports app.main via
    conftest) relies on that being true; this is the direct lock-in so a
    future authlib version change surfaces here first, not as a mysterious
    CI-wide hang waiting on a DNS timeout.
    """

    def _blocked(*args, **kwargs):
        raise AssertionError("app.platform.services.oidc made a network call at import/reload time")

    monkeypatch.setattr(socket, "create_connection", _blocked)

    import app.platform.services.oidc as oidc_module

    importlib.reload(oidc_module)
