"""The auto-i OAuth 2.0 client (KAN-488, ADR-111 as amended 2026-11-07).

auto-i's authorization server (``https://www.autoi.ch``) issues the Bearer
tokens the identAPI (``https://api.ident.autoi.ch``) accepts. auto-i's own
instruction for Nexotec (mail of 2026-11-07) names two things to implement:

* **Metadata discovery** — the authorization server's endpoints are read from
  ``/.well-known/oauth-authorization-server`` and never hard-coded (the spec's
  own rule). The document is cached in process for at most 24 hours.
* **The User Enrollment Grant** — ``grant_type=user_enrollment`` with
  ``scope=ident``. The client authenticates with HTTP Basic
  (``client_secret_basic``: the client id and the client secret) and names the
  Nexotec user on whose behalf it calls: ``user_id``, ``email``, ``first_name``,
  ``last_name`` and a UI language in ``Accept-Language`` (``de-CH`` for every
  user until KAN-585 reads the user's own through ``platform.public``). auto-i
  registers that person on first access. The token is therefore **per
  connection and per user**, valid ``expires_in`` seconds (1200 today), with no
  refresh token.

What this module is not: it never persists a token (process memory only, like
the circuit breaker's state), never logs one (``access_token`` and
``Authorization`` are redacted field names, ``app/core/redact.py``), and never
stores a secret — the client secret is resolved by the adapter per token
request and passed in here for the length of that one request.

Every token request is one ``integration_call_log`` row named ``oauth_token``
with ``cost_units = 0`` (KAN-486: the provider counts identAPI requests, not
logins). A transport failure or a 5xx from the token endpoint is a
``ProviderTransportError`` (charged to the connection's circuit breaker like
any other); a 400 or 401 is a ``ProviderRejectedError`` naming the token
endpoint's answer, which ``gateway.test_connection`` stores in ``last_error``.
"""

from __future__ import annotations

import base64
import threading
import time
import uuid
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from app.integration.errors import (
    ProviderConfigurationError,
    ProviderRejectedError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.services.metering import provider_request
from app.integration.services.resilience import DEFAULT_TIMEOUT_SECONDS, call_with_retry

DISCOVERY_URL = "https://www.autoi.ch/.well-known/oauth-authorization-server"
SCOPE = "ident"
GRANT_TYPE = "user_enrollment"
OAUTH_TOKEN_OPERATION = "oauth_token"
OAUTH_DISCOVERY_OPERATION = "oauth_discovery"
_CLIENT_AUTH_METHOD = "client_secret_basic"

_DISCOVERY_CACHE_SECONDS = 24 * 3600.0
# A token is treated as expired this long before the provider says so, so a
# request sent with it is never answered 401 for a token that just lapsed.
_TOKEN_SAFETY_MARGIN_SECONDS = 60.0


@dataclass(frozen=True)
class EnrolledUser:
    """The Nexotec user a token is requested for. `user_id` is the user's
    immutable id in Nexotec (`user.id`), which auto-i keys its registration
    by (max. 50 characters; a UUID is 36). `language` is a BCP 47 tag for
    `Accept-Language`, the UI language auto-i registers the user with. It is
    `de-CH` for every user today: the user's UI preference is not readable
    through `platform.public` (`user_preference.ui_language_of` is internal to
    the platform context); reading it is KAN-585, not a side effect here."""

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
# (connection id, cache key, user id) -> the user's token on that connection.
# The cache key carries the connection's `version` and `rotated_at`, so an
# edit of the connection or a rotation of the secret leaves every token it
# issued behind (the key the retired SOAP adapter's WSDL cache used, KAN-213).
_token_cache: dict[tuple[uuid.UUID, Hashable, str], AccessToken] = {}
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
    early), or every user's token on it when `user_id` is None."""

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
    """Discovery plus the enrollment grant over one injected `httpx.Client`.
    `_send` is the only method that touches the client; `_discover` and
    `request_token` are the two metered requests (`oauth_discovery`,
    `oauth_token`, both at no cost) — the architecture test guards all three
    by name."""

    def __init__(self, http: httpx.Client, *, discovery_url: str = DISCOVERY_URL) -> None:
        self._http = http
        self._discovery_url = discovery_url

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
            response = call_with_retry(lambda: request.attempt(lambda: self._send("GET", self._discovery_url)))
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
        # auto-i's instruction (2026-11-07) is `client_secret_basic` and the
        # enrollment grant with scope `ident`. The live document does not list
        # `user_enrollment` under `grant_types_supported` (nor
        # `client_credentials`), so the grant cannot be checked there; the
        # client authentication method and the scope can be, and are.
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

    # -- the enrollment grant ------------------------------------------------

    def access_token(
        self,
        *,
        connection_id: uuid.UUID,
        cache_key: Hashable,
        user: EnrolledUser,
        client_id: str,
        resolve_client_secret: Callable[[], str],
    ) -> str:
        """The user's token on this connection: cached while it is valid, else
        requested — and only then is the client secret resolved
        (`resolve_client_secret()`, the adapter's audited lookup), so a cached
        token costs no secret read and no audit row."""

        key = (connection_id, cache_key, user.user_id)
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

    def request_token(self, *, client_id: str, resolve_client_secret: Callable[[], str], user: EnrolledUser) -> AccessToken:
        """One `POST token_endpoint` — one `oauth_token` call-log row at no
        cost, the retry a second row (KAN-486). Only a transport failure or a
        5xx is retried; 400/401 is the provider's answer and is raised as a
        rejection naming the endpoint's status. Discovery comes first and the
        secret is resolved only once there is an endpoint to send it to, so a
        resolution (and its audit row) never happens for a login that could
        not be attempted."""

        token_endpoint = self.metadata().token_endpoint
        client_secret = resolve_client_secret()
        headers = {
            "Authorization": basic_authorization(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Accept-Language": user.language,
        }
        form = {
            "grant_type": GRANT_TYPE,
            "scope": SCOPE,
            "user_id": user.user_id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }
        with provider_request(OAUTH_TOKEN_OPERATION, cost_units=Decimal(0), is_timeout=is_timeout) as request:
            response = call_with_retry(
                lambda: request.attempt(lambda: self._send("POST", token_endpoint, data=form, headers=headers))
            )
            return _accept_token_response(response)

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """The one place this module touches the network: a transport failure
        or a 5xx becomes a `ProviderTransportError` (retryable); every other
        answer is returned for the caller to read. The message names the
        exception's class only — never the URL, which could carry a query."""

        try:
            response = self._http.request(method, url, **kwargs)
        except Exception as exc:
            raise transport_error(f"The request to the auto-i authorization server failed ({type(exc).__name__}).", exc) from exc
        if response.status_code >= 500:
            raise ProviderTransportError(f"The auto-i authorization server answered HTTP {response.status_code}.")
        return response


def _accept_token_response(response: httpx.Response) -> AccessToken:
    if response.status_code != 200:
        error_code = _error_code(response)
        detail = f" ({error_code})" if error_code else ""
        if error_code == "user_quota_exceeded":
            raise ProviderRejectedError(
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
