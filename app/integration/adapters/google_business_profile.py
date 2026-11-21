"""The Google Business Profile adapter (KAN-226 PR-2; D-A-32, FR-A-35) over
`httpx`: Google's OAuth 2.0 endpoints (authorisation code with PKCE,
refresh), the Account Management API (the accounts a sign-in may manage), the
Business Information API (a location read and its PATCH with an update mask)
and the v4 media API (a photo in three steps).

Built and tested against a recorded fixture only: Nexotec's Google Cloud
project has no Business Profile API access yet (Google approves it on
request), so nothing here has run against Google. The endpoints and shapes
were checked against Google's published reference on 2026-11-19 (the ticket's
plan lists them).

The connection is Nexotec's own OAuth client: the one platform-scope
connection to `google_business_profile` (config `clientId`, slot
`client_secret`). A location binding's refresh token lives in the secrets
manager under the binding's id; only this module reads it.

No revoke (KAN-774, Anto's option B): Google's revoke «removes all OAuth 2.0
scopes previously granted to a project», so revoking one binding's token would
end every binding the same Google user made. A binding that ends only loses
Nexotec's copy of its token (`services/gbp_link.py`).

Error mapping (the gateway charges the breaker on every failure but the
provider's coherent answers: `ProviderRequestError`, `ProviderAccountRefusedError`):
- `invalid_client` (Nexotec's client refused) → `ProviderRejectedError`;
- a 401 / 403 whose reason concerns Nexotec's project (`PROJECT_REFUSALS`: the
  API not enabled or not approved, billing, a suspended project) →
  `ProviderRejectedError`, charged: the project's refusal, never one
  binding's (KAN-774);
- a 401 / 403 / 429 whose reason is a quota (`QUOTA_REASONS`) →
  `ProviderTransportError`, charged: it passes by itself, so «try again»;
- the token endpoint's `invalid_grant` / `unauthorized_client`, any other 401 /
  403 → `ProviderAccountRefusedError`: one binding's answer (access withdrawn
  at Google, the location gone) — the binding goes to error and the manager
  reconnects; it never charges the shared client's breaker;
- 400 / 404 / 422 → `ProviderRequestError`;
- 5xx, a transport failure → `ProviderTransportError`; a timeout → `ProviderTimeoutError`.
No provider text is copied into an exception: it may name accounts.
"""

import dataclasses
import re
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.integration.errors import (
    ProviderAccountConfigurationError,
    ProviderAccountRefusedError,
    ProviderConfigurationError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.google_business_profile import GBP_PROVIDER_CODE
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.metering import provider_request

GBP_CAPABILITY = "business_profile"
SCOPE = "https://www.googleapis.com/auth/business.manage"

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
INFORMATION_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
MEDIA_API = "https://mybusiness.googleapis.com/v4"
UPLOAD_API = "https://mybusiness.googleapis.com/upload/v1/media"

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# KAN-774: the error reasons that name the consumer project, not the caller's
# token. Upper case: a `google.rpc.ErrorInfo` reason (domain googleapis.com),
# from Google's published `google/api/error_reason.proto`, read 2026-11-20.
# Camel case: the older `error.errors[].reason` shape some Google APIs (the v4
# media API among them, possibly) still answer in. None of them has been seen
# in a real Business Profile answer yet (no API access).
PROJECT_REFUSALS: frozenset[str] = frozenset(
    {
        "SERVICE_DISABLED",
        "SERVICE_NOT_VISIBLE",
        "BILLING_DISABLED",
        "USER_PROJECT_DENIED",
        "CONSUMER_INVALID",
        "CONSUMER_SUSPENDED",
        "GCP_SUSPENDED",
        "SECURITY_POLICY_VIOLATED",
        "RESOURCE_USAGE_RESTRICTION_VIOLATED",
        "ENDPOINT_USAGE_RESTRICTION_VIOLATED",
        "ORG_RESTRICTION_VIOLATION",
        "accessNotConfigured",
    }
)
# A quota is used up: it passes by itself — an outage, never broken setup.
QUOTA_REASONS: frozenset[str] = frozenset(
    {
        "RATE_LIMIT_EXCEEDED",
        "RESOURCE_QUOTA_EXCEEDED",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "dailyLimitExceeded",
        "quotaExceeded",
    }
)
_ERROR_INFO_TYPE = "type.googleapis.com/google.rpc.ErrorInfo"
_REFRESH_SLOT = SecretSlot.REFRESH_TOKEN.value

# Google's documented page sizes: accounts at most 20, locations at most 100.
_ACCOUNTS_PAGE_SIZE = 20
_LOCATIONS_PAGE_SIZE = 100
# A bounded walk: a manager picks one location among at most this many pages.
MAX_PAGES = 10

# The fields the link step reads to let the manager pick a location.
PICKER_READ_MASK = ("name", "title", "storefrontAddress")

# FR-A-35: the only Location fields Nexotec pushes (Nexotec → Google, one way).
# A path outside this list is refused before anything is sent.
PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {
        "phoneNumbers.primaryPhone",
        "storefrontAddress",
        "websiteUri",
        "regularHours",
        "specialHours",
        "moreHours",
    }
)

# The v4 media categories of a location photo.
PHOTO_CATEGORIES: frozenset[str] = frozenset(
    {"COVER", "PROFILE", "LOGO", "EXTERIOR", "INTERIOR", "PRODUCT", "AT_WORK", "TEAMS", "ADDITIONAL"}
)
PHOTO_CONTENT_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})

# Google's resource names, as Google hands them out. A name from a request body
# is checked against these before it becomes part of a URL.
_ACCOUNT_NAME = re.compile(r"^accounts/[0-9A-Za-z_-]{1,64}$")
_LOCATION_NAME = re.compile(r"^locations/[0-9A-Za-z_-]{1,64}$")
_MEDIA_RESOURCE = re.compile(r"^[0-9A-Za-z_\-./]{1,512}$")


class GbpMaskRefused(ProviderRequestError):
    """A field path outside `PATCHABLE_FIELDS` (or none at all): refused before
    anything is sent, so no request is counted."""

    def __init__(self, paths: tuple[str, ...]) -> None:
        super().__init__(f"Not a Business Profile field Nexotec pushes: {', '.join(paths) or '(none)'}.")
        self.paths = paths


def is_account_name(value: str) -> bool:
    return bool(_ACCOUNT_NAME.fullmatch(value))


def is_location_name(value: str) -> bool:
    return bool(_LOCATION_NAME.fullmatch(value))


@dataclasses.dataclass(frozen=True)
class GbpGrant:
    """What the token endpoint answered for an authorisation code. Token
    material is kept out of `repr`."""

    access_token: str = dataclasses.field(repr=False)
    refresh_token: str | None = dataclasses.field(repr=False)
    scopes: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class GbpAccount:
    name: str  # `accounts/…`
    account_name: str  # the account's display name at Google


@dataclasses.dataclass(frozen=True)
class GbpLocation:
    name: str  # `locations/…`
    title: str
    body: Mapping[str, Any]  # the Location as Google answered it (the read mask's fields)


@dataclasses.dataclass(frozen=True)
class GbpMedia:
    name: str  # `accounts/…/locations/…/media/…`


def http_transport() -> httpx.BaseTransport | None:
    """The transport every request uses — httpx's own in the application; a
    test replaces this function with a `MockTransport` (the recorded fixture)."""

    return None


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, ProviderTimeoutError))


def _mask(paths: tuple[str, ...]) -> str:
    refused = tuple(path for path in paths if path not in PATCHABLE_FIELDS)
    if not paths or refused:
        raise GbpMaskRefused(refused)
    return ",".join(dict.fromkeys(paths))  # in order, once each


class GoogleBusinessProfileAdapter:
    provider_code = GBP_PROVIDER_CODE
    provider_label = "Google Business Profile"

    def __init__(
        self, db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
    ) -> None:
        self._db = db
        self._connection = connection
        self._actor_id = actor_id
        self._purpose = purpose
        self._access_tokens: dict[uuid.UUID, str] = {}

    # -- Nexotec's OAuth client ------------------------------------------------

    def _client_id(self) -> str:
        value = (self._connection.config or {}).get("clientId")
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError("Nexotec's Google Business Profile client has no 'clientId' set.")
        return value.strip()

    def _resolve(
        self,
        *,
        folder_id: uuid.UUID,
        slot: str,
        what: str,
        unreadable: type[ProviderConfigurationError] = ProviderConfigurationError,
    ) -> str:
        try:
            value = secrets_backend.resolve_secret(connection_id=folder_id, slot=slot)
        except Exception as exc:
            # Class name only: the secrets manager's message may name its path.
            raise unreadable(f"The {what} could not be read ({type(exc).__name__}).") from exc
        record_audit_event(
            self._db,
            entity_type="integration_secret_ref",
            entity_id=folder_id,
            tenant_id=self._connection.tenant_id,
            action="secret_resolved",
            actor_id=self._actor_id,
            reason=f"slot={slot} purpose={self._purpose}",
        )
        return value

    def _client_secret(self) -> str:
        return self._resolve(
            folder_id=self._connection.id, slot=SecretSlot.CLIENT_SECRET.value, what="OAuth client's secret"
        )

    # -- the one choke point -------------------------------------------------------

    def _request(
        self,
        operation: str,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """One metered request — one call-log row per attempt."""

        with provider_request(operation, is_timeout=_is_timeout) as request:

            def _send() -> httpx.Response:
                try:
                    with httpx.Client(timeout=_TIMEOUT, transport=http_transport()) as client:
                        response = client.request(
                            method, url, headers=headers, params=params, data=data, json=json_body, content=content
                        )
                except httpx.TimeoutException as exc:
                    raise ProviderTimeoutError("Google Business Profile did not answer in time.") from exc
                except httpx.HTTPError as exc:
                    raise ProviderTransportError(
                        f"Google Business Profile could not be reached ({type(exc).__name__})."
                    ) from exc
                # KAN-767: the status on this attempt's row, and a refusal's
                # error identifier (never its description).
                request.note_response(
                    response.status_code,
                    provider_error=self._error_code(response) if response.status_code >= 400 else None,
                )
                return response

            response = request.attempt(_send)
            self._raise_for_status(response)
            return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        code = self._error_code(response)
        if code == "invalid_client":
            raise ProviderRejectedError("Google refused Nexotec's OAuth client.")
        reason = self._google_reason(response)
        if status in (401, 403, 429) and reason in QUOTA_REASONS:
            raise ProviderTransportError(f"Google Business Profile's quota is used up for now ({reason}).")
        if status in (401, 403) and reason in PROJECT_REFUSALS:
            raise ProviderRejectedError(f"Google refused Nexotec's project for Business Profile ({reason}).")
        if status in (401, 403) or (status == 400 and code in ("invalid_grant", "unauthorized_client")):
            raise ProviderAccountRefusedError(f"Google refused the location's access (HTTP {status}).")
        if status in (400, 404, 422):
            raise ProviderRequestError(f"Google Business Profile refused the request (HTTP {status}).")
        raise ProviderTransportError(f"Google Business Profile failed to answer (HTTP {status}).")

    @staticmethod
    def _identifier(value: object) -> str | None:
        return value if isinstance(value, str) and value and value.replace("_", "").isalnum() else None

    @classmethod
    def _error_code(cls, response: httpx.Response) -> str | None:
        """An identifier only: the OAuth `error` code, or a Google API error's
        reason (`SERVICE_DISABLED`, `accessNotConfigured`, …) when it carries
        one, else its `status` (`PERMISSION_DENIED`, …) — a word of letters,
        digits and underscores, never the message."""

        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        code = body.get("error")
        if isinstance(code, dict):
            code = cls._google_reason(response) or code.get("status")
        return cls._identifier(code)

    @classmethod
    def _google_reason(cls, response: httpx.Response) -> str | None:
        """A Google API error's reason: its `google.rpc.ErrorInfo` detail
        (domain googleapis.com, AIP-193), else the first `error.errors[]`
        reason of the older shape. An identifier only."""

        try:
            body = response.json()
        except ValueError:
            return None
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return None
        details = error.get("details")
        for detail in details if isinstance(details, list) else []:
            if (
                isinstance(detail, dict)
                and detail.get("@type") == _ERROR_INFO_TYPE
                and detail.get("domain") == "googleapis.com"
            ):
                return cls._identifier(detail.get("reason"))
        legacy = error.get("errors")
        if isinstance(legacy, list) and legacy and isinstance(legacy[0], dict):
            return cls._identifier(legacy[0].get("reason"))
        return None

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderTransportError("Google Business Profile sent an answer that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderTransportError("Google Business Profile sent an answer of an unexpected shape.")
        return body

    @staticmethod
    def _bearer(access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    # -- OAuth ---------------------------------------------------------------------

    def authorize_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id(),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",  # a refresh token on every link
            "include_granted_scopes": "false",
        }
        return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def redeem_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> GbpGrant:
        body = self._json(
            self._request(
                "oauth2_token",
                "POST",
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self._client_id(),
                    "client_secret": self._client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
            )
        )
        access = body.get("access_token")
        if not isinstance(access, str):
            raise ProviderTransportError("Google sent no access token.")
        refresh = body.get("refresh_token")
        return GbpGrant(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) else None,
            scopes=tuple(str(body.get("scope") or "").split()),
        )

    def access_token(self, *, binding_id: uuid.UUID) -> str:
        """A fresh access token for a binding, from its refresh token; one
        refresh per adapter (per `call_capability` block)."""

        cached = self._access_tokens.get(binding_id)
        if cached is not None:
            return cached
        # The client's own credentials first (KAN-780): a missing `clientId`
        # or an unreadable client secret is the client's, whatever the grant.
        client_id = self._client_id()
        client_secret = self._client_secret()
        # A binding's grant is one account's: unreadable, it is that
        # binding's failure, never the client's.
        refresh_token = self._resolve(
            folder_id=binding_id,
            slot=_REFRESH_SLOT,
            what="location binding's grant",
            unreadable=ProviderAccountConfigurationError,
        )
        body = self._json(
            self._request(
                "oauth2_token_refresh",
                "POST",
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
        )
        token = body.get("access_token")
        if not isinstance(token, str):
            raise ProviderTransportError("Google sent no access token.")
        rotated = body.get("refresh_token")
        if isinstance(rotated, str) and rotated != refresh_token:
            secrets_backend.update_secret(connection_id=binding_id, slot=_REFRESH_SLOT, value=rotated)
        self._access_tokens[binding_id] = token
        return token

    # -- accounts and locations ------------------------------------------------------

    def list_accounts(self, *, access_token: str) -> list[GbpAccount]:
        accounts: list[GbpAccount] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            params = {"pageSize": str(_ACCOUNTS_PAGE_SIZE)}
            if page_token:
                params["pageToken"] = page_token
            body = self._json(
                self._request("accounts_list", "GET", ACCOUNTS_URL, headers=self._bearer(access_token), params=params)
            )
            for item in body.get("accounts") or []:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and is_account_name(name):
                    accounts.append(GbpAccount(name=name, account_name=str(item.get("accountName") or "")))
            page_token = body.get("nextPageToken") if isinstance(body.get("nextPageToken"), str) else None
            if not page_token:
                break
        return accounts

    def list_locations(
        self, *, access_token: str, account_name: str, read_mask: tuple[str, ...] = PICKER_READ_MASK
    ) -> list[GbpLocation]:
        if not is_account_name(account_name):
            raise ProviderRequestError("Not a Google Business Profile account name.")
        if not read_mask:
            raise ProviderRequestError("Google requires a read mask to list locations.")
        locations: list[GbpLocation] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            params = {"readMask": ",".join(read_mask), "pageSize": str(_LOCATIONS_PAGE_SIZE)}
            if page_token:
                params["pageToken"] = page_token
            body = self._json(
                self._request(
                    "locations_list",
                    "GET",
                    f"{INFORMATION_API}/{account_name}/locations",
                    headers=self._bearer(access_token),
                    params=params,
                )
            )
            for item in body.get("locations") or []:
                location = self._location(item)
                if location is not None:
                    locations.append(location)
            page_token = body.get("nextPageToken") if isinstance(body.get("nextPageToken"), str) else None
            if not page_token:
                break
        return locations

    @staticmethod
    def _location(item: object) -> GbpLocation | None:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        if not isinstance(name, str) or not is_location_name(name):
            return None
        return GbpLocation(name=name, title=str(item.get("title") or ""), body=dict(item))

    def get_location(self, *, access_token: str, location_name: str, read_mask: tuple[str, ...]) -> GbpLocation:
        if not is_location_name(location_name):
            raise ProviderRequestError("Not a Google Business Profile location name.")
        if not read_mask:
            raise ProviderRequestError("Google requires a read mask to read a location.")
        body = self._json(
            self._request(
                "locations_get",
                "GET",
                f"{INFORMATION_API}/{location_name}",
                headers=self._bearer(access_token),
                params={"readMask": ",".join(read_mask)},
            )
        )
        location = self._location(body)
        if location is None:
            raise ProviderTransportError("Google Business Profile sent a location of an unexpected shape.")
        return location

    def patch_location(
        self,
        *,
        access_token: str,
        location_name: str,
        fields: Mapping[str, Any],
        update_mask: tuple[str, ...],
        validate_only: bool = False,
    ) -> GbpLocation:
        """Writes the listed fields of a location; `update_mask` is checked
        against `PATCHABLE_FIELDS` before anything is sent."""

        mask = _mask(update_mask)
        if not is_location_name(location_name):
            raise ProviderRequestError("Not a Google Business Profile location name.")
        params = {"updateMask": mask}
        if validate_only:
            params["validateOnly"] = "true"
        body = self._json(
            self._request(
                "locations_patch",
                "PATCH",
                f"{INFORMATION_API}/{location_name}",
                headers=self._bearer(access_token),
                params=params,
                json_body=dict(fields),
            )
        )
        location = self._location(body)
        if location is None:
            raise ProviderTransportError("Google Business Profile sent a location of an unexpected shape.")
        return location

    # -- photos (v4 media, three steps) ---------------------------------------------

    def upload_photo(
        self,
        *,
        access_token: str,
        account_name: str,
        location_name: str,
        content: bytes,
        content_type: str,
        category: str,
    ) -> GbpMedia:
        """Our photo, never a URL: start an upload, send the bytes to the data
        reference Google answered, then create the media item from it."""

        if not is_account_name(account_name) or not is_location_name(location_name):
            raise ProviderRequestError("Not a Google Business Profile location.")
        if category not in PHOTO_CATEGORIES:
            raise ProviderRequestError(f"Not a Business Profile photo category: {category}.")
        if content_type not in PHOTO_CONTENT_TYPES or not content:
            raise ProviderRequestError("A location photo is a JPEG or PNG image.")
        parent = f"{MEDIA_API}/{account_name}/{location_name}/media"
        started = self._json(
            self._request("media_start_upload", "POST", f"{parent}:startUpload", headers=self._bearer(access_token))
        )
        resource = started.get("resourceName")
        if not isinstance(resource, str) or not _MEDIA_RESOURCE.fullmatch(resource) or ".." in resource:
            raise ProviderTransportError("Google Business Profile sent no upload reference.")
        self._request(
            "media_upload_bytes",
            "POST",
            f"{UPLOAD_API}/{resource}",
            headers={**self._bearer(access_token), "Content-Type": content_type},
            params={"uploadType": "media"},
            content=content,
        )
        created = self._json(
            self._request(
                "media_create",
                "POST",
                parent,
                headers=self._bearer(access_token),
                json_body={
                    "mediaFormat": "PHOTO",
                    "locationAssociation": {"category": category},
                    "dataRef": {"resourceName": resource},
                },
            )
        )
        name = created.get("name")
        if not isinstance(name, str):
            raise ProviderTransportError("Google Business Profile sent no media item.")
        return GbpMedia(name=name)


def _register() -> None:
    # Imported lazily, as the other adapters do; `services/gbp_link.py` casts
    # the yielded adapter back to GoogleBusinessProfileAdapter.
    from app.integration.adapters.base import ProviderAdapter
    from app.integration.services.gateway import register_adapter_factory

    factory = cast(
        Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter],
        lambda db, connection, actor_id, purpose: GoogleBusinessProfileAdapter(db, connection, actor_id, purpose),
    )
    register_adapter_factory(GBP_PROVIDER_CODE, factory)


_register()
