"""A fake Google Business Profile for the KAN-226 tests, answering from the
recorded fixture `tests/fixtures/google_business_profile_recorded.json`.

Nothing here talks to Google: Nexotec's Google Cloud project has no Business
Profile API access yet. The fake answers the endpoints the adapter calls, in
the shapes Google documents — the token endpoint (authorisation code with
PKCE, refresh), revoke, the accounts list (two pages), the locations of an
account (`readMask` required), a location's read and PATCH, and the v4 media
upload in three steps. It checks Nexotec's client id and secret, the PKCE
verifier against the challenge the consent URL carried, and the bearer token
of every API call against the grant it was minted for.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import secrets
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.integration.adapters import google_business_profile as gbp

CLIENT_ID = "nexotec-gbp-client-id-invented"
CLIENT_SECRET = "invented-gbp-client-secret"
RECORDED: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "google_business_profile_recorded.json").read_text(encoding="utf-8")
)
ACCOUNT_1 = "accounts/1100000000000000001"
ACCOUNT_2 = "accounts/1100000000000000002"
LOCATION_1 = "locations/2200000000000000001"
LOCATION_2 = "locations/2200000000000000002"
LOCATION_3 = "locations/2200000000000000003"


def query_of(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _json(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=body)


@dataclasses.dataclass
class _Code:
    challenge: str
    issue_refresh_token: bool
    scope: str


class FakeSecrets:
    """The secrets manager, in memory: (folder id, slot) -> value."""

    def __init__(self) -> None:
        self.values: dict[tuple[uuid.UUID, str], str] = {}
        self.deleted: list[tuple[uuid.UUID, str]] = []

    def create_secret(self, *, connection_id, slot, value):
        self.values[(connection_id, slot)] = value
        return f"/integrations/{connection_id}/{slot}"

    def update_secret(self, *, connection_id, slot, value):
        self.values[(connection_id, slot)] = value
        return f"/integrations/{connection_id}/{slot}"

    def resolve_secret(self, *, connection_id, slot):
        return self.values[(connection_id, slot)]

    def delete_secret(self, *, connection_id, slot):
        self.values.pop((connection_id, slot), None)
        self.deleted.append((connection_id, slot))


class FakeGoogle:
    def __init__(self) -> None:
        self.codes: dict[str, _Code] = {}
        self.refresh_tokens: set[str] = set()
        self.access_tokens: set[str] = set()
        self.refused_refresh_tokens: set[str] = set()
        self.revoked: list[str] = []
        self.requests: list[httpx.Request] = []
        self.rotate_refresh_token: str | None = None
        self.fail_revoke_with: int | None = None

    # -- the test's side ---------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def sign_in(self, authorize_url: str, *, issue_refresh_token: bool = True, scope: str = gbp.SCOPE) -> dict:
        """The manager signs in at Google: the callback query the browser
        would come back with."""

        query = query_of(authorize_url)
        assert query["client_id"] == CLIENT_ID and query["code_challenge_method"] == "S256"
        code = "code-" + secrets.token_hex(8)
        self.codes[code] = _Code(
            challenge=query["code_challenge"], issue_refresh_token=issue_refresh_token, scope=scope
        )
        return {"state": query["state"], "code": code}

    def calls(self, path_part: str) -> list[httpx.Request]:
        return [r for r in self.requests if path_part in str(r.url)]

    # -- Google's side ------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if url == gbp.TOKEN_URL:
            return self._token(request)
        if url == gbp.REVOKE_URL:
            if self.fail_revoke_with is not None:
                return _json(self.fail_revoke_with, {"error": "backend_error"})
            token = dict(urllib.parse.parse_qsl(request.content.decode()))["token"]
            self.revoked.append(token)
            return httpx.Response(200)
        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.access_tokens:
            return _json(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        params = dict(request.url.params)
        if url == gbp.ACCOUNTS_URL:
            page = "accounts_page_2" if params.get("pageToken") == "page-2-token-invented" else "accounts_page_1"
            return _json(200, RECORDED[page])
        if url.startswith(gbp.INFORMATION_API + "/accounts/") and url.endswith("/locations"):
            if not params.get("readMask"):
                return _json(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
            account = url.removeprefix(gbp.INFORMATION_API + "/").removesuffix("/locations")
            if account not in RECORDED["locations"]:
                return _json(404, {"error": {"code": 404, "status": "NOT_FOUND"}})
            return _json(200, RECORDED["locations"][account])
        if url.startswith(gbp.INFORMATION_API + "/locations/"):
            if request.method == "PATCH":
                if not params.get("updateMask"):
                    return _json(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
                return _json(200, {**RECORDED["location_get"], **json.loads(request.content)})
            return _json(200, RECORDED["location_get"])
        if url.endswith("/media:startUpload"):
            return _json(200, RECORDED["media_start_upload"])
        if url.startswith(gbp.UPLOAD_API + "/"):
            return httpx.Response(200)
        if url.startswith(gbp.MEDIA_API + "/accounts/") and url.endswith("/media"):
            return _json(200, RECORDED["media_create"])
        return _json(404, {"error": {"code": 404, "status": "NOT_FOUND"}})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return _json(401, {"error": "invalid_client"})
        access = "ya29.invented-" + secrets.token_hex(8)
        if form["grant_type"] == "authorization_code":
            pending = self.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if pending is None or challenge != pending.challenge:
                return _json(400, {"error": "invalid_grant"})
            self.access_tokens.add(access)
            body: dict[str, Any] = {"access_token": access, "expires_in": 3599, "scope": pending.scope}
            if pending.issue_refresh_token:
                refresh = "1//invented-refresh-" + secrets.token_hex(8)
                self.refresh_tokens.add(refresh)
                body["refresh_token"] = refresh
            return _json(200, body)
        if form["grant_type"] == "refresh_token":
            refresh = form.get("refresh_token", "")
            if refresh not in self.refresh_tokens or refresh in self.refused_refresh_tokens:
                return _json(400, {"error": "invalid_grant"})
            self.access_tokens.add(access)
            body = {"access_token": access, "expires_in": 3599, "scope": gbp.SCOPE}
            if self.rotate_refresh_token:
                self.refresh_tokens.add(self.rotate_refresh_token)
                body["refresh_token"] = self.rotate_refresh_token
            return _json(200, body)
        return _json(400, {"error": "unsupported_grant_type"})


def install(monkeypatch) -> tuple[FakeGoogle, FakeSecrets]:
    from app.integration.services import secrets_backend

    google = FakeGoogle()
    secrets_store = FakeSecrets()
    monkeypatch.setattr(gbp, "http_transport", google.transport)
    for name in ("create_secret", "update_secret", "resolve_secret", "delete_secret"):
        monkeypatch.setattr(secrets_backend, name, getattr(secrets_store, name))
    return google, secrets_store
