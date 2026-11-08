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
400    ``ProviderRequestError`` with the json:api ``title``
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

**Built here** (KAN-488, KAN-547, KAN-548): ``fetch_code_captions`` /
``fetch_captions`` (the access check of *Test connection*, and the code
vocabulary in four languages), the six entitlement probes
(``services/entitlement_probes.py``), identification by Stammnummer, plate,
Typenschein and Werkscode (Vehicle Build / Vehicle Catalog), one variant by
FzKey, the Typenschein + Neupreis match (Vehicle Match), and the catalogue
browse operations (brands, model groups, one page of variants, facet values).
Every body is read by ``identapi_parse.py``; no code is interpreted here.
**Not built yet**: options, colours, tyres and images (KAN-211), valuation
and forecast (KAN-552), the VIN rung (KAN-549). Those ``ProviderAdapter``
methods raise ``ProviderOperationNotBuiltError`` (a
``ProviderConfigurationError``: nothing sent, nothing logged, the breaker
untouched) naming their ticket — an explicit "not built", never a silent
empty answer. The SOAP-era list methods the Protocol still carries until
KAN-550 (``list_vehicle_kinds``, ``list_brands``, …) are not built on this
adapter either: the identAPI-shaped ``list_catalogue_*`` / ``search_catalogue_variants``
replace them.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import Any, NoReturn
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.integration.adapters import identapi_parse as parse
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
    BuiltVehicleData,
    CaptionData,
    CatalogueBrandData,
    CatalogueModelGroupData,
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
    VariantSearchPage,
    VariantTyreSpecData,
    VehicleKindData,
    VehiclePriceData,
)
from app.integration.errors import (
    ProviderConfigurationError,
    ProviderNotEntitledError,
    ProviderOperationNotBuiltError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTransportError,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.metering import provider_request
from app.integration.services.resilience import call_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ident.autoi.ch"
CLIENT_ID_CONFIG_KEY = "clientId"

# Operation names as they appear in `integration_call_log.capability` — the
# identAPI path with its slashes folded, so a dealer's usage view reads like
# auto-i's own statistics (KAN-486).
OP_CAPTION_CODE = "caption_code"
OP_VEHICLECATALOG = "vehiclecatalog"
OP_VEHICLEBUILD = "vehiclebuild"
OP_VEHICLEMATCH = "vehiclematch"
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

# The identAPI's page size — fixed by the provider ("Max Page size is 10").
# Every search asks for exactly one page of it (ADR-112 (4)).
PROVIDER_PAGE_SIZE = 10

# json:api query parameters as a list of pairs: `exfilter` repeats.
_Params = Sequence[tuple[str, str]]


class _Unauthorized(ProviderRejectedError):
    """Internal: the identAPI answered 401 — `_request` drops the token and
    tries once more; only the second 401 leaves this module."""


def _not_built(ticket: str, what: str) -> NoReturn:
    # A `ProviderConfigurationError`: nothing was sent, so the gateway writes
    # no row and charges no breaker (a caller probing every rung of
    # identification must not take the connection offline).
    raise ProviderOperationNotBuiltError(f"{what} is not available on the identAPI adapter until {ticket} is built.")


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
        no_user_reason: str | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._db = db
        self._connection = connection
        self._http = http
        self._oauth = oauth
        self._actor_id = actor_id
        self._purpose = purpose
        self._user = user
        self._no_user_reason = no_user_reason
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
            raise ProviderConfigurationError(self._no_user_reason or NO_ACTOR_REASON)
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
        params: Mapping[str, str | int | bool] | _Params | None = None,
        *,
        empty_on_404: bool = False,
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
                return self._accept(operation, response, empty_on_404=empty_on_404)
        except _Unauthorized:
            if not _retry_auth:
                raise ProviderRejectedError(
                    f"{operation}: the identAPI refused the access token twice (HTTP 401) — "
                    "check the client id and the client secret of this connection."
                ) from None
            invalidate_tokens(self._connection.id, self._user.user_id if self._user else None)
            return self._request(operation, method, path, params, empty_on_404=empty_on_404, _retry_auth=False)

    def _send(
        self,
        operation: str,
        method: str,
        url: str,
        params: Mapping[str, str | int | bool] | _Params | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        """The one place `self._http` is used. A transport failure or a 5xx
        is a `ProviderTransportError` (what `call_with_retry` retries); any
        other answer is returned for `_accept` to judge, unretried."""

        try:
            query: list[tuple[str, str | int | float | bool | None]] | None = None
            if params:
                pairs = params.items() if isinstance(params, Mapping) else params
                query = [(key, str(value)) for key, value in pairs]
            response = self._http.request(method, url, params=query, headers=headers)
        except Exception as exc:
            raise transport_error(f"{operation}: the request to the identAPI failed ({type(exc).__name__}).", exc) from exc
        if response.status_code >= 500:
            raise ProviderTransportError(f"{operation}: the identAPI answered HTTP {response.status_code}.")
        return response

    @staticmethod
    def _accept(operation: str, response: httpx.Response, *, empty_on_404: bool) -> dict[str, Any]:
        status = response.status_code
        if status == 401:
            raise _Unauthorized(f"{operation}: HTTP 401")
        if status == 403:
            raise ProviderNotEntitledError(operation)
        if status in (400, 422):
            raise ProviderRequestError(f"{operation}: the identAPI refused the request (HTTP {status}){_json_api_title(response)}.")
        if status == 404 and empty_on_404:
            # Not in the OpenAPI document's response list for any endpoint —
            # an inference: read as "nothing found" for a lookup (the probes),
            # which is what json:api's `dataIsEmpty` expresses on a 200. The
            # access check never asks for it: a 404 on `/caption/code` is a
            # retired path, not a connected account (KAN-488 review F5).
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
        return self._request(OP_VEHICLECATALOG, "GET", f"/vehiclecatalog/autoi/{PROBE_FZ_KEY_CATALOG}", empty_on_404=True)

    def probe_ausstattung(self) -> dict[str, Any]:
        return self._request(OP_VEHICLEOPTION_CATALOG, "GET", f"/vehicleoption/catalog/autoi/{PROBE_FZ_KEY_OPTIONS}", empty_on_404=True)

    def probe_bewertung(self) -> dict[str, Any]:
        return self._request(OP_RESIDUALVALUE, "GET", f"/residualvalue/{PROBE_FZ_KEY_RESIDUAL}/{PROBE_MODEL_YEAR_RESIDUAL}", empty_on_404=True)

    def probe_vin(self) -> dict[str, Any]:
        return self._request(OP_VINREQUEST, "GET", f"/vinrequest/{PROBE_VIN}", empty_on_404=True)

    def probe_pneu(self) -> dict[str, Any]:
        return self._request(OP_TYRESANDRIMS_TYPEAPPROVAL, "GET", f"/tyresandrims/typeapproval/{PROBE_TYPE_APPROVAL_TYRES}", empty_on_404=True)

    def probe_energielabel(self) -> dict[str, Any]:
        return self._request(OP_ENERGYLABEL_INFO, "GET", f"/energylabel/info/typeapproval/{PROBE_TYPE_APPROVAL_ENERGY}", empty_on_404=True)

    # -- identification (KAN-547) --------------------------------------------------

    def fetch_captions(self, lang: str) -> list[CaptionData]:
        return parse.parse_captions(self.fetch_code_captions(lang))

    def _vehicle_build(self, identifier_type: str, identifier: str) -> list[BuiltVehicleData]:
        """`GET /vehiclebuild/{type}/{id}?needAutoiKeyIfExists`: the registered
        vehicle(s) and, where the provider resolved one, the catalogue variant
        each belongs to — one answer, one row in the call log. A 404 and an
        empty `data` both read as "nothing registered under this identifier"."""

        body = self._request(
            OP_VEHICLEBUILD,
            "GET",
            f"/vehiclebuild/{identifier_type}/{identifier}",
            [("needAutoiKeyIfExists", "true")],
            empty_on_404=True,
        )
        return parse.parse_built_vehicles(body)

    def identify_by_vrn(self, stammnummer: str) -> list[BuiltVehicleData]:
        return self._vehicle_build("vrn", stammnummer)

    def identify_by_plate(self, plate: str) -> list[BuiltVehicleData]:
        return self._vehicle_build("licenseplate", plate)

    def _vehicle_catalog(self, identifier_type: str, identifier: str) -> list[VariantMasterData]:
        """`GET /vehiclecatalog/{type}/{id}` — the first provider page (10)
        of catalogue variants; no further page is ever fetched (ADR-112 (4))."""

        body = self._request(
            OP_VEHICLECATALOG,
            "GET",
            f"/vehiclecatalog/{identifier_type}/{identifier}",
            [("page[size]", str(PROVIDER_PAGE_SIZE)), ("page[number]", "1")],
            empty_on_404=True,
        )
        return parse.parse_variants(body)

    def find_variants_by_type_approval(self, type_approval_number: str) -> list[VariantMasterData]:
        return self._vehicle_catalog("typeapproval", type_approval_number)

    def find_variants_by_werkscode(self, werkscode: str) -> list[VariantMasterData]:
        return self._vehicle_catalog("factorycode", werkscode)

    def fetch_variant(self, fz_key: str) -> VariantMasterData | None:
        return next((v for v in self._vehicle_catalog("autoi", fz_key) if v.fz_key == fz_key), None)

    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData:
        """The Protocol's single-variant read (`catalogue_sync.sync_catalogue_variant`,
        KAN-211): `fetch_variant`, with a missing key raised as the sync expects."""

        variant = self.fetch_variant(fz_key)
        if variant is None:
            raise KeyError(f"The identAPI knows no catalogue variant with FzKey {fz_key!r}.")
        return variant

    def fetch_vehicle_prices(self, fz_key: str, *, year: int | None = None) -> list[VehiclePriceData]:
        """The list price per model year travels with the variant
        (`catalogPrices`): one request, filtered locally."""

        prices = self.fetch_vehicle_master_data(fz_key).catalogue_prices
        return [p for p in prices if year is None or p.year == year]

    def fetch_type_approvals(self, fz_key: str) -> list[str]:
        return list(self.fetch_vehicle_master_data(fz_key).type_approval_numbers)

    def lookup_plate(self, plate: str, *, fz_art: str | None = None) -> list[PlateInfoData]:
        """The SOAP-shaped plate answer, served from `identify_by_plate`: one
        record per built vehicle that carries a Typenschein and a Stammnummer
        (what the plate-lookup cache stores). The identification waterfall
        reads `identify_by_plate` itself since KAN-547 PR 2; this shim stays
        for the `VehicleDataAdapter` contract (the SOAP adapter's callers)."""

        records: list[PlateInfoData] = []
        for vehicle in self.identify_by_plate(plate):
            if vehicle.type_approval_number is None or vehicle.stammnummer is None:
                # The spec says `vrn` is "only a part of the response, depends on user group
                # definition": a record this account may not see whole cannot be cached as
                # plate → Typenschein → Stammnummer and is left out, said so in the log.
                logger.warning(
                    "plate answer without Typenschein or Stammnummer left out of the SOAP-shaped records "
                    "(fz_key=%s, type_approval=%s)", vehicle.fz_key, vehicle.type_approval_number,
                )
                continue
            records.append(
                PlateInfoData(
                    vehicle_kind_code=vehicle.vehicle_kind_code,
                    brand_name=vehicle.brand_name,
                    model_description=vehicle.model_description,
                    production_from=vehicle.production_from,
                    production_to=vehicle.production_to,
                    type_approval_number=vehicle.type_approval_number,
                    first_registration_date=vehicle.first_registration_date,
                    stammnummer=vehicle.stammnummer,
                )
            )
        return records

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
        """`GET /vehiclematch/typeapproval/{nr}/{newPriceCatalog}` (FR-C-02
        step 5). The provider's `matchCode` is 1 for an exact match and 2 for
        the best of several — the two codes the configuration records; a
        match the provider could not make (`None`, or no vehicle in the
        answer) is raised as `KeyError`, which the waterfall reads as "no
        best match", exactly as the mock does."""

        params: list[tuple[str, str]] = []
        if modell_bez:
            params.append(("version", modell_bez))
        if eurotax_code:
            params.append(("eurotaxCode", eurotax_code))
        if getriebe:
            params.append(("gearbox", getriebe))
        if tueren is not None:
            params.append(("doors", str(tueren)))
        body = self._request(
            OP_VEHICLEMATCH, "GET", f"/vehiclematch/typeapproval/{typ_sch_nr}/{int(neupreis)}", params, empty_on_404=True
        )
        items = parse.data_items(body)
        match = items[0] if items else None
        matched = match.get("vehicle") if match else None
        vehicle = parse.parse_variant(matched) if isinstance(matched, dict) else None
        code = match.get("matchCode") if match else None
        if vehicle is None or not isinstance(code, int) or isinstance(code, bool) or code not in (1, 2):
            raise KeyError(f"The identAPI found no match for Typenschein {typ_sch_nr!r} at {int(neupreis)}.")
        return BestMatchResult(vehicle=vehicle, match_code=code)

    # -- catalogue browse (KAN-548) -------------------------------------------------

    @staticmethod
    def _vehicle_type_segment(vehicle_kind_code: str) -> str:
        segment = parse.VEHICLE_TYPE_SEGMENTS.get(vehicle_kind_code)
        if segment is None:
            raise ProviderRequestError(
                f"{vehicle_kind_code!r} is not an identAPI vehicle type (01 passenger, 02 utility, 03 moto)."
            )
        return segment

    def list_catalogue_brands(self, *, vehicle_kind_code: str) -> list[CatalogueBrandData]:
        segment = self._vehicle_type_segment(vehicle_kind_code)
        return parse.parse_brands(self._request(OP_VEHICLECATALOG, "GET", f"/vehiclecatalog/brand/{segment}"))

    def list_catalogue_model_groups(
        self, *, vehicle_kind_code: str, brand: str, in_production: bool | None = None
    ) -> list[CatalogueModelGroupData]:
        segment = self._vehicle_type_segment(vehicle_kind_code)
        params: list[tuple[str, str]] = []
        if in_production:
            params.append(("onlyInProduction", "true"))
        # A brand is a path segment the user typed or picked: percent-encoded, so
        # «../..» can never reach another identAPI path on the dealer's credentials.
        return parse.parse_model_groups(
            self._request(OP_VEHICLECATALOG, "GET", f"/vehiclecatalog/modelgroup/{segment}/{quote(brand, safe='')}", params)
        )

    def _scoped_catalog_query(
        self, *, vehicle_kind_code: str, brand: str | None, model_group_key: str | None
    ) -> tuple[str, list[tuple[str, str]]]:
        """The path and the scope parameters of a catalogue query. **Never
        unscoped**: a query with neither a brand nor a model group would walk
        the catalogue, which the licence forbids (ADR-112 (4)) — refused here,
        before anything is sent, as a request error."""

        if model_group_key:
            return f"/vehiclecatalog/modelgroup/{quote(model_group_key, safe='')}", [("filter[type]", vehicle_kind_code)]
        if brand:
            return "/vehiclecatalog", [("filter[type]", vehicle_kind_code), ("filter[brand]", brand)]
        raise ProviderRequestError(
            "A catalogue query needs a brand or a model group: the identAPI is never asked for its whole catalogue "
            "(ADR-112 (4), KAN-485)."
        )

    def search_catalogue_variants(
        self,
        *,
        vehicle_kind_code: str,
        brand: str | None = None,
        model_group_key: str | None = None,
        exfilters: Sequence[str] = (),
        sort: str | None = None,
        page_number: int = 1,
    ) -> VariantSearchPage:
        if page_number < 1:
            raise ProviderRequestError("page_number starts at 1.")
        path, params = self._scoped_catalog_query(
            vehicle_kind_code=vehicle_kind_code, brand=brand, model_group_key=model_group_key
        )
        params.extend(("exfilter", expression) for expression in exfilters if expression)
        if sort:
            params.append(("sort", sort))
        params.extend(
            [("includeCount", "true"), ("page[size]", str(PROVIDER_PAGE_SIZE)), ("page[number]", str(page_number))]
        )
        body = self._request(OP_VEHICLECATALOG, "GET", path, params, empty_on_404=True)
        return VariantSearchPage(
            items=parse.parse_variants(body),
            page_number=page_number,
            page_size=PROVIDER_PAGE_SIZE,
            total=parse.total_count(body),
        )

    def facet_values(
        self, *, field: str, vehicle_kind_code: str, brand: str, model_group_key: str | None = None
    ) -> list[str]:
        if not brand:
            raise ProviderRequestError("Facet values are read for a brand at least (ADR-112 (4)).")
        path, params = self._scoped_catalog_query(
            vehicle_kind_code=vehicle_kind_code, brand=brand, model_group_key=model_group_key
        )
        params.append(("fields", f"{field} distinct"))
        body = self._request(OP_VEHICLECATALOG, "GET", path, params, empty_on_404=True)
        return parse.parse_distinct_values(body, field)

    # -- ProviderAdapter methods other tickets build ------------------------------

    def get_system_watermark(self) -> SystemWatermark:
        # The identAPI has no `System` watermark; its access check is the
        # caption call (`entitlement_probes.access_check`).
        _not_built("KAN-550", "The catalogue watermark")

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

    # The SOAP-shaped browse methods: superseded by `list_catalogue_brands`,
    # `list_catalogue_model_groups`, `search_catalogue_variants` and
    # `facet_values`; they leave the Protocol with KAN-550.

    def list_vehicle_kinds(self) -> list[VehicleKindData]:
        _not_built("KAN-550", "The SOAP-shaped vehicle-kind list")

    def list_brands(self, *, fz_art: str) -> list[BrandData]:
        _not_built("KAN-550", "The SOAP-shaped brand list")

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
        _not_built("KAN-550", "The SOAP-shaped model-group list")

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
        _not_built("KAN-550", "The SOAP-shaped short model-group list")

    def search_vehicles(self, criteria: dict[str, str | int | Sequence[str | int]]) -> list[VariantMasterData]:
        _not_built("KAN-550", "The SOAP-shaped variant search")

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
        _not_built("KAN-550", "The SOAP-shaped grouped values")

    def fetch_type_approval_data(
        self, typ_sch_nr: str, *, getriebe: str | None = None, gaenge: int | None = None
    ) -> TypeApprovalDataResult:
        _not_built("KAN-550", "Type-approval data")

    def fetch_option_package_contents(self, opt_key: int) -> list[OptionPackageContentData]:
        _not_built("KAN-211", "Option package contents")

    def fetch_option_exclusions(self, fz_key: str, *, year: int, opt_key: int) -> list[int]:
        _not_built("KAN-211", "Option exclusions")

    def fetch_option_conditions(self, fz_key: str, *, year: int, opt_key: int) -> list[OptionConditionData]:
        _not_built("KAN-211", "Option conditions")

    def fetch_codes(
        self, *, code_groups: list[str] | None = None, active_only: bool = False
    ) -> list[CodeMapEntryData]:
        # Superseded: the identAPI's vocabulary is `fetch_captions` (ADR-111 (3)).
        _not_built("KAN-550", "The CodeGrpNr code list")


def _json_api_title(response: httpx.Response) -> str:
    """The json:api error body's `title` — a short, bounded summary of the
    problem ("Bad Request", "Unprocessable") — for `last_error`. Its free-text
    `detail` is left out, as the token endpoint's `error_description` is: a
    server's prose is never persisted into a message every dealer manager can
    read (`errors.py`'s rule; KAN-488 review F6)."""

    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    title = body.get("title")
    return f": {title.strip()[:80]}" if isinstance(title, str) and title.strip() else ""


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


# The three ways a call can have nobody to enrol, each its own sentence in
# `last_error` (the dealer manager, or platform staff, reads it).
NO_ACTOR_REASON = (
    "identAPI calls need a signed-in user: auto-i enrols each user by name and e-mail, and this call has none."
)
NO_TENANT_REASON = (
    "This connection belongs to no Betrieb, so there is no user auto-i could enrol — "
    "the identAPI service account is configured on a Betrieb's connection."
)
NOT_ACTING_IN_BETRIEB_REASON = (
    "The signed-in user is not a member of this connection's Betrieb, so auto-i cannot enrol them for it — "
    "test the connection as a member of that Betrieb (platform staff included)."
)


def enrolled_user_for(
    db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None
) -> tuple[EnrolledUser | None, str | None]:
    """The acting user as auto-i wants them named — through `platform.public`,
    the one door into the platform context — or `None` with the reason the
    adapter raises before anything is sent: no actor (a job with no user), a
    connection without a Betrieb, or an actor who does not act in it (platform
    staff testing a dealer's connection, KAN-488 review finding 9)."""

    # Imported here, not at module scope: `app.integration` imports this
    # adapter on load, and an eager import would make every import of the
    # integration package load `app.platform.public` too (the architecture
    # test that proves the submodule fallback in a fresh interpreter
    # noticed). The door stays the same: `platform.public`, nothing else.
    from app.platform.public import get_active_user_acting_in

    if actor_id is None:
        return None, NO_ACTOR_REASON
    if connection.tenant_id is None:
        return None, NO_TENANT_REASON
    user = get_active_user_acting_in(db, dealership_id=connection.tenant_id, user_id=actor_id)
    if user is None:
        return None, NOT_ACTING_IN_BETRIEB_REASON
    return (
        EnrolledUser(user_id=str(user.id), email=user.email, first_name=user.first_name, last_name=user.last_name),
        None,
    )


def _build_ident_adapter(
    db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
) -> AutoIDatIdentAdapter:
    """The `_ADAPTER_FACTORIES["auto_i_dat"]` entry (services/gateway.py).
    Builds no network connection and resolves no secret: the first token
    request, inside the caller's `call_capability` block, does both."""

    http = _http_client()
    user, no_user_reason = enrolled_user_for(db, connection, actor_id)
    return AutoIDatIdentAdapter(
        db=db,
        connection=connection,
        http=http,
        oauth=AutoiOAuthClient(http),
        actor_id=actor_id,
        purpose=purpose,
        user=user,
        no_user_reason=no_user_reason,
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
