"""The auto-i OAuth 2.0 client (KAN-488; ADR-111 as amended 2026-11-07 and 2026-11-19).

auto-i's authorization server issues the Bearer tokens the identAPI accepts.
Which authorization server, which identAPI and which grant is decided by the
connection's ``environment`` alone — one table of provider constants,
``ENVIRONMENTS`` (KAN-765; not dealer config, the Integrations PRD's rule):

* ``production`` — authority ``https://www.autoi.ch``, identAPI
  ``https://api.ident.autoi.ch``, the **User Enrollment Grant**;
* ``sandbox`` — authority ``https://www.test.autoi.ch``, identAPI
  ``https://api.ident.test.autoi.ch``, the **Client Credentials Grant**.

Both environments share two things:

* **Metadata discovery** — the authorization server's endpoints are read from
  ``{authority}/.well-known/oauth-authorization-server`` and never hard-coded
  (the spec's own rule). The document is cached in process for at most 24
  hours, keyed by its URL, so the two authorities never share an entry.
* **Client authentication** — HTTP Basic (``client_secret_basic``: the
  service account's client id and client secret) and ``scope=ident``.

**Production — the User Enrollment Grant** (auto-i's instruction of
2026-11-07, unchanged by Anto's ruling of 2026-11-19):
``grant_type=user_enrollment``, naming the Nexotec user on whose behalf the
call is made: ``user_id``, ``email``, ``first_name``, ``last_name`` and the
user's UI language in ``Accept-Language`` (KAN-585: ``de-CH``, ``fr-CH``,
``it-CH`` or ``en-CH``, read through ``platform.public.ui_language_of``). auto-i
registers that person on first access. The token is therefore **per
connection and per user**; ``user_quota_exceeded`` is that user's own refusal
(KAN-586).

**Sandbox — the Client Credentials Grant** (auto-i-dat's test environment,
mail of 2026-11-11): ``grant_type=client_credentials`` and ``scope=ident``,
nothing else — no user field, no ``Accept-Language``: the test environment
enrols nobody and wants no user data. The token is **per connection only**,
shared by every user of it.

Either token is valid ``expires_in`` seconds (1200 today), with no refresh
token. What this module is not: it never persists a token (process memory
only, like the circuit breaker's state), never logs one (``access_token`` and
``Authorization`` are redacted field names, ``app/core/redact.py``), and never
stores a secret — the client secret is resolved by the adapter per token
request and passed in here for the length of that one request.

Every token request is one ``integration_call_log`` row named ``oauth_token``
with ``cost_units = 0`` (KAN-486: the provider counts identAPI requests, not
logins). A transport failure or a 5xx from the token endpoint is a
``ProviderTransportError`` (charged to the connection's circuit breaker like
any other); a 400 or 401 is a ``ProviderRejectedError`` naming the token
endpoint's answer, which ``gateway.test_connection`` stores in ``last_error`` —
the same in both environments.
"""

from __future__ import annotations

import base64
import enum
import threading
import time
import uuid
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

import httpx

from app.integration.errors import (
    ProviderConfigurationError,
    ProviderRejectedError,
    ProviderTimeoutError,
    ProviderTransportError,
    ProviderUserQuotaExceededError,
)
from app.integration.models.connection import ConnectionEnvironment
from app.integration.services.metering import ProviderRequest, provider_request
from app.integration.services.resilience import DEFAULT_TIMEOUT_SECONDS, call_with_retry

SCOPE = "ident"
# The enrollment grant's refusal when nobody can be enrolled — the adapter's
# `NO_ACTOR_REASON` is this same sentence, so a production call reads the same
# `last_error` whichever of the two refuses it.
NO_USER_TO_ENROL_REASON = (
    "identAPI calls need a signed-in user: auto-i enrols each user by name and e-mail, and this call has none."
)
OAUTH_TOKEN_OPERATION = "oauth_token"
OAUTH_DISCOVERY_OPERATION = "oauth_discovery"
_CLIENT_AUTH_METHOD = "client_secret_basic"


class Grant(str, enum.Enum):
    """The OAuth grant a connection's environment logs in with (KAN-765)."""

    USER_ENROLLMENT = "user_enrollment"
    CLIENT_CREDENTIALS = "client_credentials"


@dataclass(frozen=True)
class AutoiEnvironment:
    """One row of the provider constants: where an environment's
    authorization server and identAPI are, and which grant it speaks. Not
    dealer config (the Integrations PRD): a dealer chooses the environment,
    never a host."""

    authority: str
    api_base_url: str
    grant: Grant

    @property
    def discovery_url(self) -> str:
        return f"{self.authority}/.well-known/oauth-authorization-server"


PRODUCTION = AutoiEnvironment(
    authority="https://www.autoi.ch", api_base_url="https://api.ident.autoi.ch", grant=Grant.USER_ENROLLMENT
)
# auto-i-dat's test environment (mail of 2026-11-11): its own authority, its
# own identAPI host (its OpenAPI page at `/docs`) and the Client Credentials
# Grant. Provider constants, not an account value.
SANDBOX = AutoiEnvironment(
    authority="https://www.test.autoi.ch",
    api_base_url="https://api.ident.test.autoi.ch",
    grant=Grant.CLIENT_CREDENTIALS,
)
ENVIRONMENTS: Mapping[ConnectionEnvironment, AutoiEnvironment] = MappingProxyType(
    {ConnectionEnvironment.PRODUCTION: PRODUCTION, ConnectionEnvironment.SANDBOX: SANDBOX}
)


def environment_for(environment: ConnectionEnvironment) -> AutoiEnvironment:
    return ENVIRONMENTS[ConnectionEnvironment(environment)]


_DISCOVERY_CACHE_SECONDS = 24 * 3600.0
# A token is treated as expired this long before the provider says so, so a
# request sent with it is never answered 401 for a token that just lapsed.
_TOKEN_SAFETY_MARGIN_SECONDS = 60.0


@dataclass(frozen=True)
class EnrolledUser:
    """The Nexotec user a token is requested for. `user_id` is the user's
    immutable id in Nexotec (`user.id`), which auto-i keys its registration
    by (max. 50 characters; a UUID is 36). `language` is a BCP 47 tag for
    `Accept-Language`, the UI language auto-i registers the user with:
    the user's own UI language (KAN-585: their `uiLanguage` preference, else
    their home Betrieb's default correspondence language — the KAN-104 rule,
    `platform.public.ui_language_of`) as `de-CH`, `fr-CH`, `it-CH` or
    `en-CH`. The default `de-CH` stands only where no language is known. Not
    part of the token cache key: a language change never asks for a second
    enrolment (auto-i keys it by `user_id`)."""

    user_id: str
    email: str
    first_name: str
    last_name: str
    language: str = "de-CH"


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    token_endpoint: str
    token_endpoint_auth_methods_supported: tuple[str, ...]
    scopes_supported: tuple[str, ...]


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: float  # time.monotonic()


@dataclass(frozen=True)
class _CachedDiscovery:
    metadata: AuthorizationServerMetadata
    loaded_at: float


# discovery url -> the cached document. Process memory; each web or worker
# process reads the document once a day.
_discovery_cache: dict[str, _CachedDiscovery] = {}
# (connection id, cache key, user id) -> the user's token on that connection;
# user id None for a Client Credentials token, the one token of a sandbox
# connection that every user of it shares (KAN-765). The cache key carries the
# connection's `version` and `rotated_at`, so an edit of the connection or a
# rotation of the secret leaves every token it issued behind (the key the
# retired SOAP adapter's WSDL cache used, KAN-213).
_token_cache: dict[tuple[uuid.UUID, Hashable, str | None], AccessToken] = {}
_cache_lock = threading.Lock()


def clear_caches() -> None:
    """Test-only: both caches are module state and would otherwise carry
    tokens and discovery documents between tests."""

    with _cache_lock:
        _discovery_cache.clear()
        _token_cache.clear()


def invalidate_tokens(connection_id: uuid.UUID, user_id: str | None = None) -> None:
    """Drops the cached token of one user on a connection (the adapter's
    answer to a 401 from the identAPI: that token was revoked or lapsed
    early), or every token on it when `user_id` is None — for a sandbox
    connection, its one shared token."""

    with _cache_lock:
        for key in [k for k in _token_cache if k[0] == connection_id and (user_id is None or k[2] == user_id)]:
            del _token_cache[key]


def _evict_expired(now: float) -> None:
    """Called under the lock when a token is stored: entries past their
    expiry are dropped, so the cache never grows past the live tokens."""

    for key in [k for k, t in _token_cache.items() if t.expires_at <= now]:
        del _token_cache[key]


def is_timeout(exc: BaseException) -> bool:
    """`httpx.TimeoutException` covers connect, read, write and pool
    timeouts; a plain `TimeoutError` is what a socket raises underneath."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, (httpx.TimeoutException, TimeoutError)):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def transport_error(message: str, exc: BaseException) -> ProviderTransportError:
    return ProviderTimeoutError(message) if is_timeout(exc) else ProviderTransportError(message)


def basic_authorization(client_id: str, client_secret: str) -> str:
    """`client_secret_basic` as the OAuth 2.0 client-authentication spec
    encodes it: `base64(urlencode(client_id) + ":" + urlencode(client_secret))`."""

    raw = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}".encode("ascii")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def build_http_client(*, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> httpx.Client:
    """The one place a real HTTP client is constructed for auto-i, so a test
    passes a client over `httpx.MockTransport` instead (no network, no real
    host). Timeouts bound connecting and each wait for the next byte."""

    return httpx.Client(timeout=httpx.Timeout(timeout), headers={"Accept": "application/json"})


class AutoiOAuthClient:
    """Discovery plus the environment's grant over one injected
    `httpx.Client`. `_send` is the only method that touches the client;
    `_discover` and `request_token` are the two metered requests
    (`oauth_discovery`, `oauth_token`, both at no cost) — the architecture
    test guards all three by name."""

    def __init__(self, http: httpx.Client, *, environment: AutoiEnvironment) -> None:
        self._http = http
        self._environment = environment
        self._discovery_url = environment.discovery_url

    @property
    def grant(self) -> Grant:
        return self._environment.grant

    # -- discovery -----------------------------------------------------------

    def metadata(self) -> AuthorizationServerMetadata:
        now = time.monotonic()
        with _cache_lock:
            entry = _discovery_cache.get(self._discovery_url)
        if entry is not None and now - entry.loaded_at < _DISCOVERY_CACHE_SECONDS:
            return entry.metadata
        metadata = self._discover()
        with _cache_lock:
            _discovery_cache[self._discovery_url] = _CachedDiscovery(metadata=metadata, loaded_at=now)
        return metadata

    def _discover(self) -> AuthorizationServerMetadata:
        # Metered like every other request to auto-i (KAN-486), at no cost:
        # the row is what tells a failed Test connection apart from a failed
        # login when the authorization server itself is unreachable.
        with provider_request(OAUTH_DISCOVERY_OPERATION, cost_units=Decimal(0), is_timeout=is_timeout) as request:
            response = call_with_retry(lambda: request.attempt(lambda: self._send(request, "GET", self._discovery_url)))
            return self._read_metadata(response)

    @staticmethod
    def _read_metadata(response: httpx.Response) -> AuthorizationServerMetadata:
        if response.status_code != 200:
            raise ProviderTransportError(
                f"The auto-i authorization server metadata answered HTTP {response.status_code}."
            )
        try:
            document = response.json()
            token_endpoint = str(document["token_endpoint"])
            methods = tuple(str(m) for m in document.get("token_endpoint_auth_methods_supported", []))
            scopes = tuple(str(s) for s in document.get("scopes_supported", []))
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderConfigurationError(
                "The auto-i authorization server metadata has no token_endpoint — the document is not the "
                "OAuth 2.0 Authorization Server Metadata this client expects."
            ) from exc
        # auto-i's instruction is `client_secret_basic` with scope `ident`, and
        # the enrollment grant (production) or the client credentials grant
        # (the test environment, KAN-765). The live document lists neither
        # `user_enrollment` nor `client_credentials` under
        # `grant_types_supported` — auto-i's own spec says so — so the grant is
        # never checked there; the client authentication method and the scope
        # can be, and are.
        if _CLIENT_AUTH_METHOD not in methods:
            raise ProviderConfigurationError(
                "The auto-i authorization server no longer offers client_secret_basic authentication; "
                "the identAPI adapter needs a decision before it can log in."
            )
        if scopes and SCOPE not in scopes:
            raise ProviderConfigurationError(
                f"The auto-i authorization server no longer lists the '{SCOPE}' scope; "
                "the identAPI adapter needs a decision before it can log in."
            )
        return AuthorizationServerMetadata(
            token_endpoint=token_endpoint, token_endpoint_auth_methods_supported=methods, scopes_supported=scopes
        )

    # -- the token -------------------------------------------------------------

    def access_token(
        self,
        *,
        connection_id: uuid.UUID,
        cache_key: Hashable,
        user: EnrolledUser | None,
        client_id: str,
        resolve_client_secret: Callable[[], str],
    ) -> str:
        """The token for this call: cached while it is valid, else requested —
        and only then is the client secret resolved (`resolve_client_secret()`,
        the adapter's audited lookup), so a cached token costs no secret read
        and no audit row. Under the enrollment grant it is the user's own
        token on the connection; under the client credentials grant the
        connection's one token, whoever asks (`user` is not read)."""

        key = (connection_id, cache_key, self._token_owner(user))
        now = time.monotonic()
        with _cache_lock:
            cached = _token_cache.get(key)
        if cached is not None and cached.expires_at - _TOKEN_SAFETY_MARGIN_SECONDS > now:
            return cached.value
        token = self.request_token(client_id=client_id, resolve_client_secret=resolve_client_secret, user=user)
        with _cache_lock:
            _evict_expired(now)
            _token_cache[key] = token
        return token.value

    def _token_owner(self, user: EnrolledUser | None) -> str | None:
        """The user part of the token cache key: the user's id under the
        enrollment grant, None under the client credentials grant. The
        adapter refuses a production call with nobody to enrol before it gets
        here (with its own, more specific reasons); this refusal is the
        client's own guarantee for any other caller."""

        if self.grant is Grant.CLIENT_CREDENTIALS:
            return None
        if user is None:
            raise ProviderConfigurationError(NO_USER_TO_ENROL_REASON)
        return user.user_id

    def _token_form(self, user: EnrolledUser | None) -> tuple[dict[str, str], dict[str, str]]:
        """The grant's own headers and body, beyond client authentication.
        Client credentials: `grant_type` and `scope`, nothing else — no user
        field, no `Accept-Language` (the test environment wants no user
        data). User enrollment: the user named in full, their language in
        `Accept-Language` (KAN-585)."""

        if self.grant is Grant.CLIENT_CREDENTIALS:
            return {}, {"grant_type": Grant.CLIENT_CREDENTIALS.value, "scope": SCOPE}
        if user is None:  # `request_token` called directly with nobody to enrol
            raise ProviderConfigurationError(NO_USER_TO_ENROL_REASON)
        return {"Accept-Language": user.language}, {
            "grant_type": Grant.USER_ENROLLMENT.value,
            "scope": SCOPE,
            "user_id": user.user_id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

    def request_token(
        self, *, client_id: str, resolve_client_secret: Callable[[], str], user: EnrolledUser | None
    ) -> AccessToken:
        """One `POST token_endpoint` — one `oauth_token` call-log row at no
        cost, the retry a second row (KAN-486). Only a transport failure or a
        5xx is retried; 400/401 is the provider's answer and is raised as a
        rejection naming the endpoint's status. Discovery comes first and the
        secret is resolved only once there is an endpoint to send it to, so a
        resolution (and its audit row) never happens for a login that could
        not be attempted."""

        grant_headers, form = self._token_form(user)
        token_endpoint = self.metadata().token_endpoint
        client_secret = resolve_client_secret()
        headers = {
            "Authorization": basic_authorization(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            **grant_headers,
        }
        with provider_request(OAUTH_TOKEN_OPERATION, cost_units=Decimal(0), is_timeout=is_timeout) as request:
            response = call_with_retry(
                lambda: request.attempt(lambda: self._send(request, "POST", token_endpoint, data=form, headers=headers))
            )
            return _accept_token_response(response, grant=self.grant)

    def _send(self, request: ProviderRequest, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """The one place this module touches the network: a transport failure
        or a 5xx becomes a `ProviderTransportError` (retryable); every other
        answer is returned for the caller to read. The message names the
        exception's class only — never the URL, which could carry a query.
        Every answer's status — and a refusal's OAuth `error` code, never its
        `error_description` — goes on this attempt's call-log row first
        (KAN-767)."""

        try:
            response = self._http.request(method, url, **kwargs)
        except Exception as exc:
            raise transport_error(f"The request to the auto-i authorization server failed ({type(exc).__name__}).", exc) from exc
        request.note_response(
            response.status_code, provider_error=_error_code(response) if response.status_code >= 400 else None
        )
        if response.status_code >= 500:
            raise ProviderTransportError(f"The auto-i authorization server answered HTTP {response.status_code}.")
        return response


def _accept_token_response(response: httpx.Response, *, grant: Grant) -> AccessToken:
    if response.status_code != 200:
        error_code = _error_code(response)
        detail = f" ({error_code})" if error_code else ""
        # KAN-586's user quota is the enrolment's: a client credentials login
        # enrols nobody, so there its 400 is a refused login like any other.
        if error_code == "user_quota_exceeded" and grant is Grant.USER_ENROLLMENT:
            raise ProviderUserQuotaExceededError(
                "auto-i refused to enrol this user: the account's user quota is exceeded — "
                "auto-i-dat has to raise it (token endpoint answered HTTP 400, user_quota_exceeded)."
            )
        raise ProviderRejectedError(
            f"The auto-i token endpoint refused the login with HTTP {response.status_code}{detail} — "
            "check the client id and the client secret of this connection."
        )
    try:
        body = response.json()
        value = str(body["access_token"])
        expires_in = float(body.get("expires_in", 0))
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderTransportError("The auto-i token endpoint answered without an access token.") from exc
    if not value:
        raise ProviderTransportError("The auto-i token endpoint answered with an empty access token.")
    return AccessToken(value=value, expires_at=time.monotonic() + expires_in)


def _error_code(response: httpx.Response) -> str | None:
    """The OAuth `error` code alone (`invalid_client`, `user_quota_exceeded`,
    …) — a closed vocabulary. `error_description` is free text from the
    server and is not persisted."""

    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code.replace("_", "").isalnum() else None
