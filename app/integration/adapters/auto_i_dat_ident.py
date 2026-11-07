"""AutoIDatIdentAdapter — the identAPI transport (KAN-488, ADR-111).

The identAPI (``https://api.ident.autoi.ch``) is a REST / json:api service
behind the OAuth 2.0 Bearer tokens ``autoi_oauth.py`` obtains. This module is
the **transport**: one choke point, ``_request``, through which every request
to the identAPI passes, metered as one ``integration_call_log`` row per attempt
under the operation's own name (KAN-486), retried once through
``resilience.call_with_retry`` for a timeout or a 5xx only, and translated
into the context's own failure vocabulary at that one place:

====== ==================================================================
status becomes
====== ==================================================================
401    the token is dropped and requested once more; a second 401 is a
       ``ProviderRejectedError``
403    ``ProviderNotEntitledError`` — the licence does not cover the
       operation (the entitlement probes read this as ``granted = False``)
400    ``ProviderRequestError`` with the json:api ``title``/``detail``
422    ``ProviderRequestError`` — syntactically fine, not processable
5xx    ``ProviderTransportError`` (retried once, then raised)
timeout ``ProviderTimeoutError`` (``CallStatus.TIMEOUT`` in the log)
====== ==================================================================

**Credentials** (Integrations & API Credentials rules 3 and 4): the client id
is the connection's ``config.clientId``; the client secret is resolved from
slot ``client_secret`` only when a token has to be requested, in memory, and
every resolution is audit-logged (``secret_resolved``) — one row per token
request, not per API call, because the token is the credential in flight.

**The user** (auto-i's User Enrollment Grant): a token names the Nexotec user
who is working, so the adapter is built with the acting user — read through
``platform.public`` from the gateway's ``actor_id`` — and a call with no
signed-in user has no token and is refused as a configuration error before
anything is sent.

What this adapter does **not** do yet: identification, browse, options,
valuation and the VIN rung are KAN-547, KAN-548, KAN-211, KAN-552 and KAN-549.
Every ``ProviderAdapter`` method they own raises ``ProviderGatewayError``
naming its ticket — an explicit "not built", never a silent empty answer.
What it does today: ``fetch_code_captions`` (the access check of *Test
connection*) and the six entitlement probes
(``services/entitlement_probes.py``), each a single documented identAPI call
with an input taken from auto-i's own public documentation examples.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import Any, NoReturn

import httpx
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.integration.adapters.autoi_oauth import (
    AutoiOAuthClient,
    EnrolledUser,
    build_http_client,
    invalidate_tokens,
    is_timeout,
    transport_error,
)
from app.integration.adapters.base import (
    BestMatchResult,
    BrandData,
    CodeMapEntryData,
    ForecastResult,
    ModelGroupData,
    ModelGroupShortData,
    OptionConditionData,
    OptionPackageContentData,
    PlateInfoData,
    SystemWatermark,
    TypeApprovalDataResult,
    ValuationResult,
    VariantColourData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantTyreSpecData,
    VehicleKindData,
    VehiclePriceData,
)
from app.integration.errors import (
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderNotEntitledError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTransportError,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.metering import provider_request
from app.integration.services.resilience import call_with_retry

BASE_URL = "https://api.ident.autoi.ch"
CLIENT_ID_CONFIG_KEY = "clientId"

# Operation names as they appear in `integration_call_log.capability` — the
# identAPI path with its slashes folded, so a dealer's usage view reads like
# auto-i's own statistics (KAN-486).
OP_CAPTION_CODE = "caption_code"
OP_VEHICLECATALOG = "vehiclecatalog"
OP_VEHICLEOPTION_CATALOG = "vehicleoption_catalog"
OP_RESIDUALVALUE = "residualvalue"
OP_VINREQUEST = "vinrequest"
OP_TYRESANDRIMS_TYPEAPPROVAL = "tyresandrims_typeapproval"
OP_ENERGYLABEL_INFO = "energylabel_info"

# Probe inputs — the examples auto-i's own public OpenAPI document uses in
# its "Try me" links (https://api.ident.autoi.ch/docs). Catalogue identifiers
# and type-approval numbers, never a registered vehicle's Stammnummer or
# plate; not an account value (KAN-491). The VIN is the documentation's
# sample VIN too.
PROBE_FZ_KEY_CATALOG = "152567"
PROBE_FZ_KEY_OPTIONS = "136838"
PROBE_FZ_KEY_RESIDUAL = "184748"
PROBE_MODEL_YEAR_RESIDUAL = 2024
PROBE_TYPE_APPROVAL_TYRES = "1AA157"
PROBE_TYPE_APPROVAL_ENERGY = "1MG788"
PROBE_VIN = "WVWZZZAUZGP532394"


class _Unauthorized(ProviderRejectedError):
    """Internal: the identAPI answered 401 — `_request` drops the token and
    tries once more; only the second 401 leaves this module."""


def _not_built(ticket: str, what: str) -> NoReturn:
    raise ProviderGatewayError(f"{what} is not available on the identAPI adapter until {ticket} is built.")


class AutoIDatIdentAdapter:
    def __init__(
        self,
        *,
        db: Session,
        connection: IntegrationConnection,
        http: httpx.Client,
        oauth: AutoiOAuthClient,
        actor_id: uuid.UUID | None,
        purpose: str,
        user: EnrolledUser | None,
        base_url: str = BASE_URL,
    ) -> None:
        self._db = db
        self._connection = connection
        self._http = http
        self._oauth = oauth
        self._actor_id = actor_id
        self._purpose = purpose
        self._user = user
        self._base_url = base_url.rstrip("/")

    # -- credentials ------------------------------------------------------

    def _client_id(self) -> str:
        client_id = self._connection.config.get(CLIENT_ID_CONFIG_KEY)
        if not client_id or not isinstance(client_id, str):
            raise ProviderConfigurationError(
                f"Connection {self._connection.id} has no config.{CLIENT_ID_CONFIG_KEY} — "
                "enter the client id auto-i-dat issued for the service account."
            )
        return client_id

    def _resolve_client_secret(self) -> str:
        try:
            value = secrets_backend.resolve_secret(connection_id=self._connection.id, slot=SecretSlot.CLIENT_SECRET.value)
        except Exception as exc:
            # The secrets manager's own errors, most usefully "no such secret":
            # a connection created and tested before the secret was set. Class
            # name only; the SDK's message may name the path it was reading.
            raise ProviderConfigurationError(
                f"The '{SecretSlot.CLIENT_SECRET.value}' credential of this connection could not be read "
                f"({type(exc).__name__}) — set it and test again."
            ) from exc
        record_audit_event(
            self._db,
            entity_type="integration_secret_ref",
            entity_id=self._connection.id,
            tenant_id=self._connection.tenant_id,
            action="secret_resolved",
            actor_id=self._actor_id,
            reason=f"slot={SecretSlot.CLIENT_SECRET.value} purpose={self._purpose}",
        )
        return value

    def _cache_key(self) -> Hashable:
        return (self._connection.version, self._connection.rotated_at)

    def _access_token(self) -> str:
        if self._user is None:
            raise ProviderConfigurationError(
                "identAPI calls need a signed-in user: auto-i enrols each user by name and e-mail, "
                "and this call has none."
            )
        return self._oauth.access_token(
            connection_id=self._connection.id,
            cache_key=self._cache_key(),
            user=self._user,
            client_id=self._client_id(),
            resolve_client_secret=self._resolve_client_secret,
        )

    # -- the one choke point ------------------------------------------------

    def _request(
        self,
        operation: str,
        method: str,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
        *,
        _retry_auth: bool = True,
    ) -> dict[str, Any]:
        """Every identAPI request. The token is obtained (and, if it has to
        be requested, metered as its own `oauth_token` row) before the
        request's own `provider_request` block opens, so a login failure is
        never filed under the operation. Inside the block: one attempt per
        row, a timeout or 5xx retried once, and the answer judged before the
        block closes so a refused answer is this request's own `ERROR`."""

        token = self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = f"{self._base_url}{path}"
        try:
            with provider_request(operation, is_timeout=is_timeout) as request:
                response = call_with_retry(
                    lambda: request.attempt(lambda: self._send(operation, method, url, params, headers))
                )
                return self._accept(operation, response)
        except _Unauthorized:
            if not _retry_auth:
                raise ProviderRejectedError(
                    f"{operation}: the identAPI refused the access token twice (HTTP 401) — "
                    "check the client id and the client secret of this connection."
                ) from None
            invalidate_tokens(self._connection.id)
            return self._request(operation, method, path, params, _retry_auth=False)

    def _send(
        self, operation: str, method: str, url: str, params: Mapping[str, str | int | bool] | None, headers: dict[str, str]
    ) -> httpx.Response:
        """The one place `self._http` is used. A transport failure or a 5xx
        is a `ProviderTransportError` (what `call_with_retry` retries); any
        other answer is returned for `_accept` to judge, unretried."""

        try:
            response = self._http.request(method, url, params=dict(params) if params else None, headers=headers)
        except Exception as exc:
            raise transport_error(f"{operation}: the request to the identAPI failed ({type(exc).__name__}).", exc) from exc
        if response.status_code >= 500:
            raise ProviderTransportError(f"{operation}: the identAPI answered HTTP {response.status_code}.")
        return response

    @staticmethod
    def _accept(operation: str, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status == 401:
            raise _Unauthorized(f"{operation}: HTTP 401")
        if status == 403:
            raise ProviderNotEntitledError(operation)
        if status in (400, 422):
            raise ProviderRequestError(f"{operation}: the identAPI refused the request (HTTP {status}){_json_api_detail(response)}.")
        if status == 404:
            # Not in the OpenAPI document's response list; read as "nothing
            # found" for a lookup, which is what json:api's `dataIsEmpty`
            # expresses on a 200.
            return {"data": [], "meta": {"totalCount": 0, "dataIsEmpty": True}}
        if status != 200:
            raise ProviderTransportError(f"{operation}: the identAPI answered HTTP {status}.")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderTransportError(f"{operation}: the identAPI answered with a body that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderTransportError(f"{operation}: the identAPI answered with a body that is not a json:api object.")
        return body

    # -- what is built today ----------------------------------------------------

    def fetch_code_captions(self, lang: str = "de") -> dict[str, Any]:
        """`GET /caption/code?lang=` — every coded vocabulary of the API in
        one language. Succeeds for any licence, which is what makes it the
        access check of *Test connection* (ADR-111 §3 makes it the code map's
        source in KAN-547)."""

        return self._request(OP_CAPTION_CODE, "GET", "/caption/code", {"lang": lang})

    # The six entitlement probes — one cheap documented call per licence
    # option (`services/entitlement_probes.py`). Each returns the json:api
    # body; the probe only cares whether it was refused with 403.

    def probe_ident_basis(self) -> dict[str, Any]:
        return self._request(OP_VEHICLECATALOG, "GET", f"/vehiclecatalog/autoi/{PROBE_FZ_KEY_CATALOG}")

    def probe_ausstattung(self) -> dict[str, Any]:
        return self._request(OP_VEHICLEOPTION_CATALOG, "GET", f"/vehicleoption/catalog/autoi/{PROBE_FZ_KEY_OPTIONS}")

    def probe_bewertung(self) -> dict[str, Any]:
        return self._request(
            OP_RESIDUALVALUE, "GET", f"/residualvalue/{PROBE_FZ_KEY_RESIDUAL}/{PROBE_MODEL_YEAR_RESIDUAL}"
        )

    def probe_vin(self) -> dict[str, Any]:
        return self._request(OP_VINREQUEST, "GET", f"/vinrequest/{PROBE_VIN}")

    def probe_pneu(self) -> dict[str, Any]:
        return self._request(
            OP_TYRESANDRIMS_TYPEAPPROVAL, "GET", f"/tyresandrims/typeapproval/{PROBE_TYPE_APPROVAL_TYRES}"
        )

    def probe_energielabel(self) -> dict[str, Any]:
        return self._request(
            OP_ENERGYLABEL_INFO, "GET", f"/energylabel/info/typeapproval/{PROBE_TYPE_APPROVAL_ENERGY}"
        )

    # -- ProviderAdapter methods other tickets build ------------------------------

    def get_system_watermark(self) -> SystemWatermark:
        _not_built("KAN-547", "The catalogue watermark")

    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData:
        _not_built("KAN-547", "Variant master data")

    def fetch_options(self, fz_key: str, *, model_year: int) -> list[VariantOptionData]:
        _not_built("KAN-211", "The option list")

    def fetch_colours(self, *, werkscode: str) -> list[VariantColourData]:
        _not_built("KAN-211", "The colour list")

    def fetch_tyre_specs(self, *, type_approval_number: str) -> list[VariantTyreSpecData]:
        _not_built("KAN-211", "The tyre dimensions")

    def fetch_images(self, fz_key: str) -> list[VariantImageData]:
        _not_built("KAN-211", "The image list")

    def fetch_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, valuation_date: dt.date, mileage: int
    ) -> ValuationResult:
        _not_built("KAN-552", "Valuation")

    def fetch_forecast(self, *, fz_key: str, model_year: int) -> ForecastResult:
        _not_built("KAN-552", "The residual-value forecast")

    def list_vehicle_kinds(self) -> list[VehicleKindData]:
        _not_built("KAN-548", "Catalogue browse")

    def list_brands(self, *, fz_art: str) -> list[BrandData]:
        _not_built("KAN-548", "Catalogue browse")

    def list_model_groups(
        self,
        *,
        fz_art: str | None = None,
        marken_nr: str | None = None,
        marke: str | None = None,
        mod_grp_key: int | None = None,
        mod_kurz_bez: str | None = None,
        nur_neue: bool = False,
        prod_von: int | None = None,
        prod_bis: int | None = None,
    ) -> list[ModelGroupData]:
        _not_built("KAN-548", "Catalogue browse")

    def list_model_groups_short(
        self,
        *,
        fz_art: str,
        marken_nr: str | None = None,
        marke: str | None = None,
        nur_neue: bool = False,
        page: int | None = None,
        per_page: int | None = None,
    ) -> list[ModelGroupShortData]:
        _not_built("KAN-548", "Catalogue browse")

    def search_vehicles(self, criteria: dict[str, str | int | Sequence[str | int]]) -> list[VariantMasterData]:
        _not_built("KAN-548", "The variant search")

    def fetch_vehicle_prices(self, fz_key: str, *, year: int | None = None) -> list[VehiclePriceData]:
        _not_built("KAN-547", "New prices per model year")

    def fetch_grouped_values(
        self,
        *,
        fz_art: str,
        marken_nr: str | None = None,
        marke: str | None = None,
        mod_kurz_bez: str | None = None,
        typ_sch_nr: str | None = None,
        gruppiert: str,
    ) -> list[str]:
        _not_built("KAN-548", "Facet values")

    def fetch_type_approvals(self, fz_key: str) -> list[str]:
        _not_built("KAN-547", "Type approvals of a variant")

    def lookup_plate(self, plate: str, *, fz_art: str | None = None) -> list[PlateInfoData]:
        _not_built("KAN-547", "Plate identification")

    def find_best_match(
        self,
        *,
        typ_sch_nr: str,
        neupreis: int,
        modell_bez: str | None = None,
        eurotax_code: str | None = None,
        getriebe: str | None = None,
        tueren: int | None = None,
    ) -> BestMatchResult:
        _not_built("KAN-547", "The best-match identification")

    def fetch_type_approval_data(
        self, typ_sch_nr: str, *, getriebe: str | None = None, gaenge: int | None = None
    ) -> TypeApprovalDataResult:
        _not_built("KAN-547", "Type-approval data")

    def fetch_option_package_contents(self, opt_key: int) -> list[OptionPackageContentData]:
        _not_built("KAN-211", "Option package contents")

    def fetch_option_exclusions(self, fz_key: str, *, year: int, opt_key: int) -> list[int]:
        _not_built("KAN-211", "Option exclusions")

    def fetch_option_conditions(self, fz_key: str, *, year: int, opt_key: int) -> list[OptionConditionData]:
        _not_built("KAN-211", "Option conditions")

    def fetch_codes(
        self, *, code_groups: list[str] | None = None, active_only: bool = False
    ) -> list[CodeMapEntryData]:
        _not_built("KAN-547", "The code map")


def _json_api_detail(response: httpx.Response) -> str:
    """`title` and `detail` of a json:api error body — they describe our
    request (an unknown identifier, a malformed filter), never a credential,
    and they are what a dealer manager needs to read in `last_error`."""

    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    parts = [str(body[k]) for k in ("title", "detail") if isinstance(body.get(k), str) and body[k].strip()]
    return f": {' — '.join(parts)}" if parts else ""


# -- the gateway's factory -----------------------------------------------------


_HttpClientFactory = Callable[[], httpx.Client]
# One HTTP client per process for the identAPI and one for the authorization
# server: connection pooling, no credential held (the Bearer token travels
# per request in a header). Replaced in tests by a client over
# `httpx.MockTransport` (`set_http_client_factory`).
_http_client_factory: _HttpClientFactory = build_http_client
_shared_http: httpx.Client | None = None


def set_http_client_factory(factory: _HttpClientFactory | None) -> None:
    """Test-only: the next adapter is built over `factory()`; `None` restores
    the real client."""

    global _http_client_factory, _shared_http
    _http_client_factory = factory or build_http_client
    _shared_http = None


def _http_client() -> httpx.Client:
    global _shared_http
    if _shared_http is None:
        _shared_http = _http_client_factory()
    return _shared_http


def enrolled_user_for(db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None) -> EnrolledUser | None:
    """The acting user as auto-i wants them named: through `platform.public`,
    the one door into the platform context. `None` when there is no actor
    (a job with no user) or the actor does not act in the connection's
    Betrieb — the adapter then refuses the call before it is sent."""

    # Imported here, not at module scope: `app.integration` imports this
    # adapter on load, and an eager import would make every import of the
    # integration package load `app.platform.public` too (the architecture
    # test that proves the submodule fallback in a fresh interpreter
    # noticed). The door stays the same: `platform.public`, nothing else.
    from app.platform.public import get_active_user_acting_in

    if actor_id is None or connection.tenant_id is None:
        return None
    user = get_active_user_acting_in(db, dealership_id=connection.tenant_id, user_id=actor_id)
    if user is None:
        return None
    return EnrolledUser(user_id=str(user.id), email=user.email, first_name=user.first_name, last_name=user.last_name)


def _build_ident_adapter(
    db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
) -> AutoIDatIdentAdapter:
    """The `_ADAPTER_FACTORIES["auto_i_dat"]` entry (services/gateway.py).
    Builds no network connection and resolves no secret: the first token
    request, inside the caller's `call_capability` block, does both."""

    http = _http_client()
    return AutoIDatIdentAdapter(
        db=db,
        connection=connection,
        http=http,
        oauth=AutoiOAuthClient(http),
        actor_id=actor_id,
        purpose=purpose,
        user=enrolled_user_for(db, connection, actor_id),
    )


def _register() -> None:
    # Imported lazily: services/gateway.py imports the adapters package's
    # mock directly, and nothing in adapters/ imports gateway.py at module
    # scope. Registered after the SOAP adapter's own `_register`, so the
    # identAPI adapter is what `auto_i_dat` resolves to (ADR-111); the SOAP
    # module stays importable until KAN-550 removes it.
    from app.integration.services.gateway import register_adapter_factory

    register_adapter_factory("auto_i_dat", _build_ident_adapter)


_register()
