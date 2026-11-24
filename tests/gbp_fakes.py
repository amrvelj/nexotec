"""A fake Google Business Profile for the KAN-226 tests, answering from the
recorded fixture `tests/fixtures/google_business_profile_recorded.json`.

Nothing here talks to Google: Nexotec's Google Cloud project has no Business
Profile API access yet. The fake answers the endpoints the adapter calls, in
the shapes Google documents — the token endpoint (authorisation code with
PKCE, refresh), revoke (which Nexotec must never call: KAN-774, Anto's
option B — the fake records any request to it), the accounts list (two pages), the locations of an
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
# Google's revoke endpoint. Nexotec never calls it (KAN-774): a revoke ends the
# Google user's whole grant to Nexotec's project, every binding they made.
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


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
        self.refuse_listing = False
        # KAN-774: every API call answers 403 with this `google.rpc.ErrorInfo`
        # reason (the project's refusal, e.g. SERVICE_DISABLED), not the grant's.
        self.project_refusal: str | None = None
        self.refusal_status = 403
        # The same in Google's older shape (`error.errors[].reason`).
        self.legacy_refusal: str | None = None
        self.grant_refusal_reason: str | None = None
        # KAN-226 PR-3: the `hoursTypeId`s the location's category offers
        # (answered on a read whose mask names `categories`), and a status
        # every PATCH answers with instead of success.
        self.more_hours_types: list[str] = []
        self.patch_failure_status: int | None = None
        # KAN-226 PR-4: a location the test tracks keeps its fields — a PATCH
        # writes the masked ones, a read answers the masked ones (and the
        # categories) — so a drift check reads back what was pushed, or what
        # the test changed «at Google». A status every read answers with.
        self.location_bodies: dict[str, dict[str, Any]] = {}
        self.get_failure_status: int | None = None
        # KAN-784: a status every photo upload's first step answers with.
        self.media_failure_status: int | None = None
        # KAN-784 review: image bytes Google refuses (400) when they are uploaded.
        self.refused_media: set[bytes] = set()

    def track(self, location_name: str, **fields: Any) -> None:
        self.location_bodies[location_name] = {"name": location_name, **fields}

    def _tracked_read(self, location_name: str, read_mask: str) -> httpx.Response:
        body = self.location_bodies[location_name]
        fields = set(read_mask.split(","))
        answer = {"name": location_name, **{k: v for k, v in body.items() if k in fields}}
        if "categories" in fields:
            answer["categories"] = self._categories(location_name)["categories"]
        return _json(200, answer)

    def _tracked_patch(self, location_name: str, request: httpx.Request) -> None:
        sent = json.loads(request.content)
        body = self.location_bodies[location_name]
        for path in request.url.params["updateMask"].split(","):
            top = path.split(".")[0]
            if top in sent and sent[top] not in ({}, []):
                body[top] = sent[top]
            else:
                body.pop(top, None)  # cleared at Google

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
        if url == REVOKE_URL:
            token = dict(urllib.parse.parse_qsl(request.content.decode()))["token"]
            self.revoked.append(token)
            return httpx.Response(200)
        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.access_tokens:
            return _json(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        if self.project_refusal is not None:
            body = _error_info(self.project_refusal, consumer="projects/900000000001")
            return _json(self.refusal_status, body)
        if self.legacy_refusal is not None:
            return _json(self.refusal_status, _legacy_error(self.legacy_refusal))
        if self.grant_refusal_reason is not None:
            return _json(403, _error_info(self.grant_refusal_reason))
        params = dict(request.url.params)
        if url == gbp.ACCOUNTS_URL and self.refuse_listing:
            return _json(403, {"error": {"code": 403, "status": "PERMISSION_DENIED"}})
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
            name = url.removeprefix(gbp.INFORMATION_API + "/")
            if request.method == "GET" and self.get_failure_status is not None:
                return _json(self.get_failure_status, {"error": {"code": self.get_failure_status}})
            if name in self.location_bodies:
                if request.method == "GET":
                    return self._tracked_read(name, params.get("readMask", ""))
                if request.method == "PATCH" and self.patch_failure_status is None and params.get("updateMask"):
                    self._tracked_patch(name, request)
            if request.method == "PATCH" and self.patch_failure_status is not None:
                return _json(self.patch_failure_status, {"error": {"code": self.patch_failure_status}})
            if request.method == "GET" and "categories" in params.get("readMask", "").split(","):
                return _json(200, self._categories(url.removeprefix(gbp.INFORMATION_API + "/")))
            if request.method == "PATCH":
                if not params.get("updateMask"):
                    return _json(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
                return _json(200, {**RECORDED["location_get"], **json.loads(request.content)})
            return _json(200, RECORDED["location_get"])
        if url.endswith("/media:startUpload"):
            if self.media_failure_status is not None:
                return _json(self.media_failure_status, {"error": {"code": self.media_failure_status}})
            return _json(200, RECORDED["media_start_upload"])
        if url.startswith(gbp.UPLOAD_API + "/"):
            if request.content in self.refused_media:
                return _json(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
            return httpx.Response(200)
        if url.startswith(gbp.MEDIA_API + "/accounts/") and url.endswith("/media"):
            return _json(200, RECORDED["media_create"])
        return _json(404, {"error": {"code": 404, "status": "NOT_FOUND"}})

    def _categories(self, location_name: str) -> dict[str, Any]:
        """A location's categories in Business Information v1's shape: an
        invented category offering the test's `more_hours_types`."""

        return {
            "name": location_name,
            "categories": {
                "primaryCategory": {
                    "name": "categories/gcid:invented_car_dealer",
                    "displayName": "Invented car dealer",
                    "moreHoursTypes": [
                        {"hoursTypeId": type_id, "displayName": type_id.title(), "localizedDisplayName": type_id}
                        for type_id in self.more_hours_types
                    ],
                }
            },
        }

    def patches(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "PATCH"]

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


def _error_info(reason: str, *, consumer: str | None = None) -> dict[str, Any]:
    """A Google API error (AIP-193) carrying one `google.rpc.ErrorInfo`, in the
    shape `google/api/error_reason.proto` documents; invented values, not a
    recorded answer (Nexotec has no Business Profile API access yet)."""

    metadata = {"service": "mybusinessaccountmanagement.googleapis.com"}
    if consumer is not None:
        metadata["consumer"] = consumer
    return {
        "error": {
            "code": 403,
            "message": "invented message that names nothing",
            "status": "PERMISSION_DENIED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": reason,
                    "domain": "googleapis.com",
                    "metadata": metadata,
                }
            ],
        }
    }


def _legacy_error(reason: str) -> dict[str, Any]:
    """Google's older error shape (`error.errors[]`), invented values."""

    return {
        "error": {
            "code": 403,
            "message": "invented message that names nothing",
            "errors": [{"message": "invented", "domain": "usageLimits", "reason": reason}],
            "status": "PERMISSION_DENIED",
        }
    }


def install(monkeypatch) -> tuple[FakeGoogle, FakeSecrets]:
    from app.integration.services import secrets_backend

    google = FakeGoogle()
    secrets_store = FakeSecrets()
    monkeypatch.setattr(gbp, "http_transport", google.transport)
    for name in ("create_secret", "update_secret", "resolve_secret", "delete_secret"):
        monkeypatch.setattr(secrets_backend, name, getattr(secrets_store, name))
    return google, secrets_store
