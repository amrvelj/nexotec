"""KAN-488 test support: a fake auto-i authorization server and identAPI over
`httpx.MockTransport`, and the rows a connection to them needs.

Nothing here reaches the network. Every value is invented (KAN-491): the
client id, the secret, the token, the users. The fake answers the four hosts
the real adapter speaks to — per environment (KAN-765) an authorization server
(`www.autoi.ch` / `www.test.autoi.ch`: discovery, token endpoint) and an
identAPI (`api.ident.autoi.ch` / `api.ident.test.autoi.ch`) — and records
every request it saw, so a test can assert what was sent (and what was not: a
secret, a token in a log line, an API call after a refused login, a user
field on a sandbox login). Each authorization server expects its own
environment's grant, and each identAPI accepts only the tokens its own
authority issued, so a request sent to the other environment's host fails.

`make_connection` builds a **production** connection unless told otherwise:
the behaviour the KAN-488 … KAN-588 tests pin (the enrollment grant, the
per-user token, the no-user refusals, the user quota, the language tag) is
production's since KAN-765; the sandbox's own tests ask for
`ConnectionEnvironment.SANDBOX`.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx

from app.integration.adapters import auto_i_dat_ident, autoi_oauth
from app.integration.models.connection import ConnectionEnvironment, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.platform.identity.models.user import User, UserRole, UserStatus

CLIENT_ID = "test-client-id-0001"
CLIENT_SECRET = "test-client-secret-not-real"
CAPABILITY_CODES = ["ident_basis", "ausstattung", "bewertung", "vin", "pneu", "energielabel"]

PRODUCTION_AUTHORITY = "www.autoi.ch"
PRODUCTION_API = "api.ident.autoi.ch"
SANDBOX_AUTHORITY = "www.test.autoi.ch"
SANDBOX_API = "api.ident.test.autoi.ch"
# authorization server -> the grant it expects; identAPI -> its authority.
_GRANT_OF = {PRODUCTION_AUTHORITY: "user_enrollment", SANDBOX_AUTHORITY: "client_credentials"}
_AUTHORITY_OF = {PRODUCTION_API: PRODUCTION_AUTHORITY, SANDBOX_API: SANDBOX_AUTHORITY}

DISCOVERY = {
    "issuer": "https://www.autoi.ch",
    "token_endpoint": "https://www.autoi.ch/api/oauth2/token",
    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    "scopes_supported": ["openid", "ident"],
    "grant_types_supported": ["authorization_code", "refresh_token", "password"],
}

# The probe paths as the adapter sends them (its own constants, so a renamed
# probe breaks these tests rather than silently passing).
PROBE_PATHS = {
    "ident_basis": f"/vehiclecatalog/autoi/{auto_i_dat_ident.PROBE_FZ_KEY_CATALOG}",
    "ausstattung": f"/vehicleoption/catalog/autoi/{auto_i_dat_ident.PROBE_FZ_KEY_OPTIONS}",
    "bewertung": f"/residualvalue/{auto_i_dat_ident.PROBE_FZ_KEY_RESIDUAL}/{auto_i_dat_ident.PROBE_MODEL_YEAR_RESIDUAL}",
    "vin": f"/vehiclebuild/vin/{auto_i_dat_ident.PROBE_VIN}",
    "pneu": f"/tyresandrims/typeapproval/{auto_i_dat_ident.PROBE_TYPE_APPROVAL_TYRES}",
    "energielabel": f"/energylabel/info/typeapproval/{auto_i_dat_ident.PROBE_TYPE_APPROVAL_ENERGY}",
}
PROBE_OPERATIONS = {
    "ident_basis": auto_i_dat_ident.OP_VEHICLECATALOG,
    "ausstattung": auto_i_dat_ident.OP_VEHICLEOPTION_CATALOG,
    "bewertung": auto_i_dat_ident.OP_RESIDUALVALUE,
    "vin": auto_i_dat_ident.OP_VEHICLE_VIN,
    "pneu": auto_i_dat_ident.OP_TYRESANDRIMS_TYPEAPPROVAL,
    "energielabel": auto_i_dat_ident.OP_ENERGYLABEL_INFO,
}


def _json_api_error(status: int, title: str, detail: str = "") -> httpx.Response:
    return httpx.Response(status, json={"self": "", "status": str(status), "title": title, "detail": detail})


@dataclass
class SeenRequest:
    method: str
    host: str
    path: str
    headers: dict[str, str]
    form: dict[str, str]
    query: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FakeAutoi:
    """Scriptable: `api_status[path]` sets what the identAPI answers for a
    path (default 200 with an empty json:api body); `token_status` what the
    token endpoint answers after a correct login; `discovery` the metadata.
    `expire_tokens_once` makes the identAPI answer the next request 401,
    whatever token it carries (a token revoked under the adapter)."""

    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    discovery: dict = field(default_factory=lambda: dict(DISCOVERY))
    discovery_status: int = 200
    token_status: int = 200
    expires_in: int = 1200
    api_status: dict[str, int] = field(default_factory=dict)
    api_fail_once: dict[str, int] = field(default_factory=dict)
    # KAN-547: a scripted 200 body per path (json:api `data` + `meta`),
    # for the identification and browse operations; a path not listed
    # answers the empty body below.
    api_bodies: dict[str, dict] = field(default_factory=dict)
    expire_tokens_once: bool = False
    raise_on_api: Exception | None = None
    seen: list[SeenRequest] = field(default_factory=list)
    tokens_issued: list[str] = field(default_factory=list)
    # authority host -> the tokens it issued (KAN-765): an identAPI accepts
    # only its own authority's.
    tokens_issued_by: dict[str, list[str]] = field(default_factory=dict)

    # -- what the adapter saw -----------------------------------------------------

    def token_requests(self) -> list[SeenRequest]:
        return [r for r in self.seen if r.path == "/api/oauth2/token"]

    def api_requests(self) -> list[SeenRequest]:
        return [r for r in self.seen if r.host in _AUTHORITY_OF]

    def api_requests_to(self, path: str) -> list[SeenRequest]:
        return [r for r in self.api_requests() if r.path == path]

    # -- the transport --------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport(), headers={"Accept": "application/json"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode("utf-8")).items()} if request.content else {}
        self.seen.append(
            SeenRequest(
                request.method, request.url.host, request.url.path, dict(request.headers), form,
                list(request.url.params.multi_items()),
            )
        )
        if request.url.host in _GRANT_OF:
            return self._authorization_server(request, form)
        if request.url.host in _AUTHORITY_OF:
            return self._ident_api(request)
        return httpx.Response(404)

    def discovery_for(self, host: str) -> dict:
        """The metadata as `host` serves it: the scripted document, its
        issuer and endpoints moved onto that host."""

        return {
            key: value.replace(f"//{PRODUCTION_AUTHORITY}", f"//{host}") if isinstance(value, str) else value
            for key, value in self.discovery.items()
        }

    def _authorization_server(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        host = request.url.host
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(self.discovery_status, json=self.discovery_for(host))
        if request.url.path == "/api/oauth2/token":
            expected = "Basic " + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            if request.headers.get("Authorization") != expected:
                return httpx.Response(401, json={"error": "invalid_client"})
            if form.get("grant_type") != _GRANT_OF[host] or form.get("scope") != "ident":
                return httpx.Response(400, json={"error": "unsupported_grant_type"})
            if self.token_status == 400:
                return httpx.Response(400, json={"error": "user_quota_exceeded", "error_description": "..."})
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "server_error"})
            token = f"tok-{uuid.uuid4().hex}"
            self.tokens_issued.append(token)
            self.tokens_issued_by.setdefault(host, []).append(token)
            return httpx.Response(200, json={"access_token": token, "token_type": "Bearer", "expires_in": self.expires_in})
        return httpx.Response(404)

    def _ident_api(self, request: httpx.Request) -> httpx.Response:
        if self.raise_on_api is not None:
            raise self.raise_on_api
        if self.expire_tokens_once:
            self.expire_tokens_once = False
            return _json_api_error(401, "Unauthorized")
        authorization = request.headers.get("Authorization", "")
        own_tokens = self.tokens_issued_by.get(_AUTHORITY_OF[request.url.host], [])
        token = authorization[7:]
        if not authorization.startswith("Bearer ") or token not in self.tokens_issued or token not in own_tokens:
            return _json_api_error(401, "Unauthorized")
        path = request.url.path
        if path in self.api_fail_once:
            status = self.api_fail_once.pop(path)
            return _json_api_error(status, "Transient")
        status = self.api_status.get(path, 200)
        if status == 200:
            body = self.api_bodies.get(path)
            if body is not None:
                return httpx.Response(200, json=body)
            return httpx.Response(
                200, json={"data": [], "links": {"self": str(request.url)}, "meta": {"totalCount": 0, "dataIsEmpty": True}}
            )
        if status == 403:
            return _json_api_error(403, "Forbidden", "The licence does not include this option.")
        if status == 422:
            return _json_api_error(422, "Unprocessable", "No vehicle for this key.")
        if status == 400:
            return _json_api_error(400, "Bad Request", "The filter is malformed.")
        if status == 404:
            return httpx.Response(404, text="Not Found")
        return _json_api_error(status, "Server error")


class FakeSecretsBackend:
    """`resolve_secret` only — what the adapter reads. `connections.set_secret`
    takes its own `secrets_backend_module` with the write side."""

    def __init__(self, values: dict[tuple[uuid.UUID, str], str] | None = None) -> None:
        self.values = dict(values or {})
        self.attempts = 0  # every read the adapter asked for
        self.resolutions = 0  # those that handed out a value

    def resolve_secret(self, *, connection_id: uuid.UUID, slot: str) -> str:
        self.attempts += 1
        value = self.values[(connection_id, slot)]  # a missing slot raises, as the real SDK does
        self.resolutions += 1
        return value

    # the write side, so `connection_service.set_secret(..., secrets_backend_module=self)` works too
    def create_secret(self, *, connection_id, slot, value):
        self.values[(connection_id, slot)] = value
        return f"/integrations/{connection_id}/{slot}"

    def update_secret(self, *, connection_id, slot, value):
        self.values[(connection_id, slot)] = value
        return f"/integrations/{connection_id}/{slot}"

    def delete_secret(self, *, connection_id, slot):
        self.values.pop((connection_id, slot), None)

    def delete_connection_folder(self, *, connection_id):
        pass  # KAN-604: the real backend removes /integrations/<id>; nothing to remove in memory


def install(monkeypatch, fake: FakeAutoi, secrets: FakeSecretsBackend) -> None:
    """The real, registered identAPI adapter over the fake hosts and the
    in-memory secrets manager. Caches are module state: cleared on install
    so no token or discovery document leaks between tests."""

    autoi_oauth.clear_caches()
    auto_i_dat_ident.set_http_client_factory(fake.client)
    monkeypatch.setattr(auto_i_dat_ident, "secrets_backend", secrets)


def uninstall() -> None:
    auto_i_dat_ident.set_http_client_factory(None)
    autoi_oauth.clear_caches()


def make_provider(db_session, **overrides) -> IntegrationProvider:
    defaults = {
        "provider_code": "auto_i_dat",
        "category": "vehicle_data",
        "display_name": "auto-i-dat (identAPI)",
        "auth_type": "oauth2_user_enrollment",
        "required_secret_slots": ["client_secret"],
        "required_config_keys": ["clientId"],
        "capability_codes": list(CAPABILITY_CODES),
        "supports_sandbox": True,
    }
    defaults.update(overrides)
    provider = IntegrationProvider(**defaults)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def make_user(db_session, *, tenant_id: uuid.UUID, email: str = "ada.tester@example.test", first_name="Ada", last_name="Tester") -> User:
    user = User(
        tenant_id=tenant_id, first_name=first_name, last_name=last_name, email=email, role=UserRole.SALES,
        access_roles=[], is_dealer_manager=True, status=UserStatus.ACTIVE, auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def make_connection(
    db_session, provider: IntegrationProvider, *, tenant_id: uuid.UUID, secrets: FakeSecretsBackend,
    client_id: str = CLIENT_ID, client_secret: str | None = CLIENT_SECRET,
    environment: ConnectionEnvironment = ConnectionEnvironment.PRODUCTION,
) -> IntegrationConnection:
    connection = connection_service.create_connection(
        db_session,
        tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=environment,
            config={"clientId": client_id},
        ),
        actor_id=uuid.uuid4(),
    )
    if client_secret is not None:
        secrets.values[(connection.id, "client_secret")] = client_secret
    return connection


@dataclass
class Setup:
    fake: FakeAutoi
    secrets: FakeSecretsBackend
    connection: IntegrationConnection
    user: User


def connected(
    db_session, monkeypatch, *, fake: FakeAutoi | None = None, tenant_id: uuid.UUID | None = None,
    environment: ConnectionEnvironment = ConnectionEnvironment.PRODUCTION,
) -> Setup:
    """Provider, connection, secret, an active user homed in the tenant and
    the fake installed — everything a `test_connection` needs."""

    fake = fake or FakeAutoi()
    secrets = FakeSecretsBackend()
    install(monkeypatch, fake, secrets)
    tenant_id = tenant_id or uuid.uuid4()
    provider = make_provider(db_session)
    connection = make_connection(db_session, provider, tenant_id=tenant_id, secrets=secrets, environment=environment)
    user = make_user(db_session, tenant_id=tenant_id)
    return Setup(fake=fake, secrets=secrets, connection=connection, user=user)


def json_body(response: httpx.Response) -> dict:
    return json.loads(response.content)
