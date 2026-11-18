"""Fake Microsoft 365 and Google Workspace for the mail tests (KAN-215).

Nothing here talks to a real provider (Anto's Q1 C): the fakes answer the
endpoints the adapters call, as the providers document them publicly — OpenID
discovery, a key set, the token endpoint (authorisation code with PKCE,
refresh, client credentials, the JWT bearer of domain-wide delegation), Graph's
`users/{id}` and `sendMail`, the Directory API's `users.get`, Gmail's
`messages.send` and Google's revoke. id_tokens are RS256-signed with a key the
fake publishes; the fake checks the PKCE verifier against the challenge the
authorisation URL carried, so a wrong verifier fails as at the provider.

A test "signs in" by reading the authorisation URL the API answered and asking
the fake for a code (`sign_in`), with the claims it wants in the id_token.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import secrets
import time
import urllib.parse
import uuid
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.integration.adapters import mail_providers

MS_CLIENT_ID = "nexotec-ms-client-id"
GOOGLE_CLIENT_ID = "nexotec-google-client-id"
CLIENT_SECRET = "invented-client-secret"
SERVICE_ACCOUNT = "mail-sender@nexotec-invented.iam.gserviceaccount.com"

_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "fake-key-1"
_JWK = json.loads(RSAAlgorithm.to_jwk(_SIGNING_KEY.public_key()))
_JWK.update({"kid": _KID, "use": "sig", "alg": "RS256"})
SERVICE_ACCOUNT_KEY = _SIGNING_KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def query_of(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


@dataclasses.dataclass
class _PendingCode:
    challenge: str
    claims: dict[str, Any]
    scope: str
    refresh_token: str | None
    access_token: str


@dataclasses.dataclass
class SentRecord:
    url: str
    body: dict[str, Any]
    authorization: str


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


class FakeMailProviders:
    """Both providers behind one MockTransport. Tests shape their state:
    `ms_mailboxes[tid]` / `google_users[domain]`, `ms_consented`,
    `google_delegated`, `fail_sends_with`, …"""

    def __init__(self) -> None:
        self.codes: dict[str, _PendingCode] = {}
        self.refresh_tokens: dict[str, str] = {}  # refresh token -> access token
        self.access_tokens: dict[str, dict[str, Any]] = {}  # access token -> who
        self.ms_mailboxes: dict[str, dict[str, str]] = {}  # tid -> address -> object id
        self.ms_consented: set[str] = set()
        self.google_users: dict[str, dict[str, dict[str, Any]]] = {}  # domain -> address -> {id, isAdmin, sub}
        self.google_delegated: set[str] = set()  # mailboxes the service account may act as
        self.sent: list[SentRecord] = []
        self.revoked: list[str] = []
        self.requests: list[httpx.Request] = []
        self.fail_sends_with: int | None = None
        self.refuse_refresh = False

    # -- the test's side ---------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def sign_in(
        self, authorize_url: str, *, claims: dict[str, Any], issue_refresh_token: bool = True
    ) -> dict[str, str]:
        """The user signs in at the provider: the callback query the browser
        would come back with (state and code)."""

        query = query_of(authorize_url)
        code = "code-" + secrets.token_hex(8)
        access = "at-" + secrets.token_hex(8)
        full = {"nonce": query["nonce"], **claims}
        refresh = "rt-" + secrets.token_hex(8) if issue_refresh_token else None
        self.codes[code] = _PendingCode(
            challenge=query["code_challenge"], claims=full, scope=query["scope"], refresh_token=refresh,
            access_token=access,
        )
        self.access_tokens[access] = full
        if refresh:
            self.refresh_tokens[refresh] = access
        return {"state": query["state"], "code": code}

    # -- tokens ------------------------------------------------------------------

    def _id_token(self, issuer: str, audience: str, claims: dict[str, Any]) -> str:
        now = int(time.time())
        payload = {"iss": issuer, "aud": audience, "iat": now, "exp": now + 3600, **claims}
        payload.setdefault("sub", claims.get("oid", "subject"))
        return jwt.encode(payload, _SIGNING_KEY, algorithm="RS256", headers={"kid": _KID})

    def _redeem(self, form: dict[str, str], *, issuer_for, audience: str) -> httpx.Response:
        pending = self.codes.pop(form.get("code", ""), None)
        if pending is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        if _b64url(hashlib.sha256(verifier.encode()).digest()) != pending.challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        body: dict[str, Any] = {
            "access_token": pending.access_token,
            "token_type": "Bearer",
            "scope": pending.scope,
            "id_token": self._id_token(issuer_for(pending.claims), audience, pending.claims),
        }
        if pending.refresh_token:
            body["refresh_token"] = pending.refresh_token
        return httpx.Response(200, json=body)

    def _refresh(self, form: dict[str, str], *, rotate: bool) -> httpx.Response:
        access = self.refresh_tokens.get(form.get("refresh_token", ""))
        if access is None or self.refuse_refresh:
            return httpx.Response(400, json={"error": "invalid_grant"})
        body = {"access_token": access, "token_type": "Bearer"}
        if rotate:
            new = "rt-" + secrets.token_hex(8)
            self.refresh_tokens[new] = access
            del self.refresh_tokens[form["refresh_token"]]
            body["refresh_token"] = new
        return httpx.Response(200, json=body)

    # -- the transport -----------------------------------------------------------

    def _send_result(self, request: httpx.Request, body: dict[str, Any], *, status: int, answer) -> httpx.Response:
        if self.fail_sends_with is not None:
            return httpx.Response(self.fail_sends_with, json={"error": {"code": "invented"}})
        self.sent.append(SentRecord(str(request.url), body, request.headers.get("authorization", "")))
        return httpx.Response(status, json=answer)

    def _handle(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - one fake, many endpoints
        self.requests.append(request)
        url = urllib.parse.urlsplit(str(request.url))
        host, path = url.netloc, url.path
        form = dict(urllib.parse.parse_qsl(request.content.decode())) if request.method == "POST" else {}
        auth = request.headers.get("authorization", "").removeprefix("Bearer ")

        # Microsoft identity platform
        if host == "login.microsoftonline.com":
            if path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json={
                    "issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
                    "jwks_uri": "https://login.microsoftonline.com/common/discovery/v2.0/keys",
                })
            if path == "/common/discovery/v2.0/keys":
                return httpx.Response(200, json={"keys": [_JWK]})
            if path.endswith("/oauth2/v2.0/token"):
                if form.get("client_secret") != CLIENT_SECRET:
                    return httpx.Response(401, json={"error": "invalid_client"})
                grant = form.get("grant_type")
                if grant == "authorization_code":
                    return self._redeem(
                        form, issuer_for=lambda c: f"https://login.microsoftonline.com/{c['tid']}/v2.0",
                        audience=MS_CLIENT_ID,
                    )
                if grant == "refresh_token":
                    return self._refresh(form, rotate=True)
                if grant == "client_credentials":
                    tid = path.split("/")[1]
                    if tid not in self.ms_consented:
                        return httpx.Response(400, json={"error": "unauthorized_client"})
                    token = "app-" + tid
                    self.access_tokens[token] = {"app_tenant": tid}
                    return httpx.Response(200, json={"access_token": token, "token_type": "Bearer"})
            return httpx.Response(404)
        # Microsoft Graph
        if host == "graph.microsoft.com":
            who = self.access_tokens.get(auth)
            if who is None:
                return httpx.Response(401)
            if request.method == "GET" and path.startswith("/v1.0/users/"):
                address = urllib.parse.unquote(path.removeprefix("/v1.0/users/")).lower()
                object_id = self.ms_mailboxes.get(who.get("app_tenant", ""), {}).get(address)
                if object_id is None:
                    return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})
                return httpx.Response(200, json={"id": object_id, "mail": address, "userPrincipalName": address})
            if request.method == "POST" and path.endswith("/sendMail"):
                if path.startswith("/v1.0/users/"):
                    sender = urllib.parse.unquote(path.removeprefix("/v1.0/users/").removesuffix("/sendMail")).lower()
                    if sender not in self.ms_mailboxes.get(who.get("app_tenant", ""), {}):
                        return httpx.Response(403)
                return self._send_result(request, json.loads(request.content), status=202, answer=None)
            return httpx.Response(404)
        # Google
        if host == "accounts.google.com" and path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": "https://accounts.google.com",
                "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            })
        if host == "www.googleapis.com" and path == "/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [_JWK]})
        if host == "oauth2.googleapis.com" and path == "/token":
            grant = form.get("grant_type")
            if grant == "urn:ietf:params:oauth:grant-type:jwt-bearer":
                claims = jwt.decode(form["assertion"], _SIGNING_KEY.public_key(), algorithms=["RS256"],
                                    audience="https://oauth2.googleapis.com/token")
                if claims.get("iss") != SERVICE_ACCOUNT or claims.get("sub") not in self.google_delegated:
                    return httpx.Response(400, json={"error": "unauthorized_client"})
                token = "dwd-" + secrets.token_hex(6)
                self.access_tokens[token] = {"delegated": claims["sub"]}
                return httpx.Response(200, json={"access_token": token, "token_type": "Bearer"})
            if form.get("client_secret") != CLIENT_SECRET:
                return httpx.Response(401, json={"error": "invalid_client"})
            if grant == "authorization_code":
                return self._redeem(form, issuer_for=lambda c: "https://accounts.google.com", audience=GOOGLE_CLIENT_ID)
            if grant == "refresh_token":
                return self._refresh(form, rotate=False)
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        if host == "oauth2.googleapis.com" and path == "/revoke":
            token = form.get("token", "")
            if token not in self.refresh_tokens:
                return httpx.Response(400, json={"error": "invalid_token"})
            self.revoked.append(token)
            del self.refresh_tokens[token]
            return httpx.Response(200)
        if host == "admin.googleapis.com" and path.startswith("/admin/directory/v1/users/"):
            who = self.access_tokens.get(auth)
            if who is None:
                return httpx.Response(401)
            key = urllib.parse.unquote(path.removeprefix("/admin/directory/v1/users/")).lower()
            users = self.google_users.get(str(who.get("hd", "")), {})
            for address, user in users.items():
                if key in (address, str(user.get("sub", "")).lower()):
                    return httpx.Response(200, json={"id": user["id"], "primaryEmail": address,
                                                     "isAdmin": bool(user.get("isAdmin"))})
            return httpx.Response(404)
        if host == "gmail.googleapis.com" and path == "/gmail/v1/users/me/messages/send":
            if auth not in self.access_tokens:
                return httpx.Response(401)
            return self._send_result(request, json.loads(request.content), status=200,
                                     answer={"id": "gmail-" + secrets.token_hex(4)})
        return httpx.Response(404)


def install(monkeypatch) -> tuple[FakeMailProviders, FakeSecrets]:
    from app.integration.services import secrets_backend

    providers = FakeMailProviders()
    secrets_store = FakeSecrets()
    monkeypatch.setattr(mail_providers, "http_transport", providers.transport)
    mail_providers._JWKS_CACHE.clear()
    for name in ("create_secret", "update_secret", "resolve_secret", "delete_secret"):
        monkeypatch.setattr(secrets_backend, name, getattr(secrets_store, name))
    return providers, secrets_store
