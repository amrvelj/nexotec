"""Catalogue browse and facet search (FR-C-01) — **live identAPI queries**
(ADR-112, KAN-548).

Under a single-lookup licence the catalogue can never be a mirror
(KAN-485), so the drill-down (vehicle kind → brand → model group →
variant) and the facet search run as provider queries on the Betrieb's own
connection, each one request for exactly the page or facet field the user
asked for — never a page the user did not ask for (ADR-112 (4),
`tests/architecture/test_no_bulk_catalogue_retrieval.py`,
`tests/architecture/test_catalogue_browse_is_a_bounded_live_query.py`).

Every variant a page returns is stored once as master data through the
catalogue's own upsert (`store_seen_variants`, ADR-075): a second browse of
the same page asks the provider again (no mirror), while a variant opened
from the results (`GET /catalogue/variants/{id}`) reads the stored row.

**States, never errors** (Anto, 2026-11-07: «nicht erreichbar» is a normal
answer, no 503): a Betrieb without a connection gets `not_configured` and
the screen offers the manual configuration; the provider's 403 is
`not_in_contract`; a failure or timeout is `unavailable`. Each answer is a
200 with the state on it.

**Provider codes never appear here.** The identAPI filters and facets on
its own codes; this module translates a canonical value code to the
provider's through `ProviderCodeMap` (the reverse of what
`catalogue_sync` resolves on the way in) and a facet value back through
`resolve_provider_code`, which files a mapping gap for a code it does not
know (FR-C-10).
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.integration.public import (
    CatalogueBrandData,
    CatalogueModelGroupData,
    ConnectionRef,
    ProviderGatewayError,
    ProviderNotEntitledError,
    VariantSearchPage,
    call_capability,
    get_entitlement,
)
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.provider import ProviderCodeMap
from app.vehicle.schemas.catalogue import CatalogueBrowseMode, CatalogueBrowseState
from app.vehicle.services.catalogue_sync import find_enabled_vehicle_data_connection, store_seen_variants
from app.vehicle.services.provider import resolve_provider_code

logger = logging.getLogger("app.vehicle.catalogue_browse")

# The identAPI licence option the catalogue queries sit behind (KAN-488).
_ENTITLEMENT = "ident_basis"
_CALL_LABEL = "catalogue_browse"

DEFAULT_VEHICLE_KIND = "passenger_car"

# API field (camelCase, as the frontend sends it) → the canonical reference
# list its values come from and the identAPI field it filters. Only fields
# the identAPI marks filterable (its OpenAPI document, 2026-11-03): `ps`,
# `newPriceCatalog` and the production years are not, so they are neither
# filters nor facets here; `vehicleKind` is the drill-down's scope.
@dataclass(frozen=True)
class CodedField:
    list_code: str
    provider_field: str


CODED_FIELDS: dict[str, CodedField] = {
    "fuelType": CodedField("fuel_type", "fuel"),
    "bodyStyle": CodedField("body_style", "body"),
    "drivetrain": CodedField("drivetrain", "drive"),
    "transmission": CodedField("transmission", "gearbox"),
    "vehicleClass": CodedField("vehicle_class", "category"),
    "emissionStandard": CodedField("emission_standard", "euronorm"),
}

# The facet fields the panel shows once a brand is chosen — one provider
# request each (ADR-112 (4)), so the list is short on purpose.
FACET_FIELDS: tuple[str, ...] = ("fuelType", "bodyStyle", "drivetrain", "transmission")

NUMERIC_FIELDS: dict[str, str] = {
    "kw": "kw",
    "displacementCcm": "displacement",
    "doors": "doors",
    "seats": "seats",
    "co2Gkm": "co2",
}

# API sort field → identAPI field. Sortable where the identAPI filters.
SORT_FIELDS: dict[str, str] = {
    "kw": "kw",
    "displacementCcm": "displacement",
    "co2Gkm": "co2",
    "doors": "doors",
    "seats": "seats",
}


@dataclass(frozen=True)
class VariantFilters:
    coded: dict[str, str] = field(default_factory=dict)  # apiField -> canonical value_code
    numeric_min: dict[str, Decimal] = field(default_factory=dict)  # apiField -> lower bound (inclusive)
    numeric_max: dict[str, Decimal] = field(default_factory=dict)  # apiField -> upper bound (inclusive)
    q: str | None = None  # free text over the short description


@dataclass(frozen=True)
class BrowseSort:
    api_field: str
    descending: bool


@dataclass(frozen=True)
class BrandsResult:
    state: CatalogueBrowseState
    items: list[CatalogueBrandData]


@dataclass(frozen=True)
class ModelGroupsResult:
    state: CatalogueBrowseState
    items: list[CatalogueModelGroupData]


@dataclass(frozen=True)
class VariantsResult:
    state: CatalogueBrowseState
    rows: list[ModelVariant]
    page: int
    next_page: int | None
    total: int | None


@dataclass(frozen=True)
class VariantFacets:
    state: CatalogueBrowseState
    coded: dict[str, list[str]]  # apiField -> canonical value codes offered


@dataclass(frozen=True)
class _Provider:
    """The Betrieb's connection, or the state that says why there is none to ask."""

    state: CatalogueBrowseState
    connection: ConnectionRef | None
    provider_code: str | None


def _provider(db: Session, *, tenant_id: uuid.UUID) -> _Provider:
    """No connection → `not_configured`; the `ident_basis` option not granted
    → `not_in_contract`; otherwise the connection to ask."""

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return _Provider(CatalogueBrowseState.NOT_CONFIGURED, None, None)
    connection, provider_code = found
    entitlement = get_entitlement(db, connection=connection, capability_code=_ENTITLEMENT)
    if entitlement is not None and not entitlement.granted:
        return _Provider(CatalogueBrowseState.NOT_IN_CONTRACT, None, provider_code)
    return _Provider(CatalogueBrowseState.OK, connection, provider_code)


def _call(db: Session, provider: _Provider, *, actor_id: uuid.UUID | None, purpose: str, call) -> tuple[CatalogueBrowseState, object | None]:
    """One identAPI operation inside the gateway block. **Everything that can
    refuse the user's input (a kind or a code the map does not know, an
    unscoped query) is decided before this** — a 422 raised inside the block
    would charge the breaker and write a call-log row for a request never
    sent (the gateway rule, `vehicle-gateway.md`). The provider's 403 is
    `not_in_contract`; a transport failure or timeout is `unavailable`."""

    assert provider.connection is not None
    try:
        with call_capability(db, connection=provider.connection, capability=_CALL_LABEL, actor_id=actor_id, purpose=purpose) as adapter:
            return CatalogueBrowseState.OK, call(adapter)
    except ProviderNotEntitledError:
        return CatalogueBrowseState.NOT_IN_CONTRACT, None
    except ProviderGatewayError:
        logger.warning("catalogue browse (%s) failed", purpose, exc_info=True)
        return CatalogueBrowseState.UNAVAILABLE, None


_MODEL_GROUP_KEY = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_BRAND_FORBIDDEN = re.compile(r"[/\\?#%\x00-\x1f]")


def _check_scope(*, brand: str | None, model_group_key: str | None) -> tuple[str | None, str | None]:
    """The drill-down's scope as the user sent it, checked before anything
    reaches the provider: a brand is a name (no path or query characters), a
    model-group key is the provider's key shape. Anything else is 422 — a
    «..», a «.» or a slash would otherwise be sent as part of the identAPI
    path, where the client collapses dot segments."""

    brand = brand.strip() if brand and brand.strip() else None
    model_group_key = model_group_key.strip() if model_group_key and model_group_key.strip() else None
    if brand is not None and (_BRAND_FORBIDDEN.search(brand) or not brand.strip(".")):
        raise UnprocessableEntityError(
            "Not a brand name.", details={"reason": "catalogue_scope_invalid", "invalid": {"brand": brand}}
        )
    if model_group_key is not None and not _MODEL_GROUP_KEY.match(model_group_key):
        raise UnprocessableEntityError(
            "Not a model-group key.", details={"reason": "catalogue_scope_invalid", "invalid": {"modelGroupKey": model_group_key}}
        )
    return brand, model_group_key


def _vehicle_kind_code(db: Session, *, provider_code: str, vehicle_kind: str) -> str:
    """The provider's code for a canonical vehicle kind — the reverse of the
    map `catalogue_sync` resolves on the way in. A kind the map does not know
    for this provider is 422 (the drill-down offers every active kind of the
    reference list; the provider answers for the ones its map names)."""

    row = db.scalar(
        select(ProviderCodeMap.provider_code)
        .where(
            ProviderCodeMap.provider == provider_code,
            ProviderCodeMap.code_group == "vehicle_kind",
            ProviderCodeMap.canonical_value_code == vehicle_kind,
        )
        .order_by(ProviderCodeMap.provider_code)
    )
    if row is None:
        raise UnprocessableEntityError(
            f"The provider knows no vehicle kind {vehicle_kind!r}.",
            details={"reason": "vehicle_kind_unknown_to_provider", "vehicleKind": vehicle_kind},
        )
    return row


def _provider_codes_for(db: Session, *, provider_code: str, kind_code: str, api_field: str, value_code: str) -> list[str]:
    """Every provider code a canonical value maps from (the seed maps diesel
    from two auto-i codes, say): a filter on the value matches each of them."""

    coded = CODED_FIELDS[api_field]
    rows = db.scalars(
        select(ProviderCodeMap.provider_code)
        .where(
            ProviderCodeMap.provider == provider_code,
            ProviderCodeMap.vehicle_kind == kind_code,
            ProviderCodeMap.code_group == coded.list_code,
            ProviderCodeMap.canonical_value_code == value_code,
        )
        .order_by(ProviderCodeMap.provider_code)
    ).all()
    if not rows:
        raise UnprocessableEntityError(
            f"Not a value the provider filters on: {api_field}={value_code!r}.",
            details={"reason": "catalogue_filter_value_unknown", "invalid": {api_field: value_code}},
        )
    return list(rows)


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _exfilters(db: Session, *, provider_code: str, kind_code: str, filters: VariantFilters) -> list[str]:
    """The user's predicates as identAPI `exfilter` expressions — one per
    predicate, each on a field the provider filters."""

    expressions: list[str] = []
    for api_field, value_code in filters.coded.items():
        if api_field not in CODED_FIELDS:
            continue
        raws = _provider_codes_for(db, provider_code=provider_code, kind_code=kind_code, api_field=api_field, value_code=value_code)
        provider_field = CODED_FIELDS[api_field].provider_field
        if len(raws) == 1:
            expressions.append(f"eq({provider_field},{_quote(raws[0])})")
        else:
            expressions.append(f"in({provider_field},{','.join(_quote(raw) for raw in raws)})")
    for api_field, lower in filters.numeric_min.items():
        if api_field in NUMERIC_FIELDS:
            expressions.append(f"ge({NUMERIC_FIELDS[api_field]},{_quote(_number(lower))})")
    for api_field, upper in filters.numeric_max.items():
        if api_field in NUMERIC_FIELDS:
            expressions.append(f"le({NUMERIC_FIELDS[api_field]},{_quote(_number(upper))})")
    if filters.q and filters.q.strip():
        expressions.append(f"has(descriptionShort,{_quote(filters.q.strip())})")
    return expressions


def _number(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _sort_expression(sort: list[BrowseSort]) -> str | None:
    parts = [f"{SORT_FIELDS[s.api_field]}{' desc' if s.descending else ''}" for s in sort if s.api_field in SORT_FIELDS]
    return ",".join(parts) or None


def _in_production(mode: CatalogueBrowseMode) -> bool:
    """PRD `NurNeue`: `build` lists what is in production, `record` everything.
    The identAPI takes it on the brand and model-group lists; its variant
    rows carry `productionEnd` unfilterable, so a page is not narrowed."""

    return mode == CatalogueBrowseMode.BUILD


def list_brands(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, vehicle_kind: str) -> BrandsResult:
    """`GET /vehiclecatalog/brand/{type}` — one request, every brand of the
    kind with its model names (not stored; ADR-112)."""

    provider = _provider(db, tenant_id=tenant_id)
    if provider.state is not CatalogueBrowseState.OK:
        return BrandsResult(state=provider.state, items=[])
    assert provider.provider_code is not None
    kind_code = _vehicle_kind_code(db, provider_code=provider.provider_code, vehicle_kind=vehicle_kind)
    state, value = _call(
        db, provider, actor_id=actor_id, purpose="catalogue_brands",
        call=lambda adapter: adapter.list_catalogue_brands(vehicle_kind_code=kind_code),
    )
    brands: list[CatalogueBrandData] = list(value) if isinstance(value, list) else []  # type: ignore[arg-type]
    return BrandsResult(state=state, items=sorted(brands, key=lambda b: b.name.lower()))


def list_model_groups(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, vehicle_kind: str, brand: str, mode: CatalogueBrowseMode
) -> ModelGroupsResult:
    """`GET /vehiclecatalog/modelgroup/{type}/{brand}` — one request."""

    checked_brand, _ = _check_scope(brand=brand, model_group_key=None)
    if checked_brand is None:
        raise UnprocessableEntityError("A brand is needed to list model groups.", details={"reason": "brand_required"})
    provider = _provider(db, tenant_id=tenant_id)
    if provider.state is not CatalogueBrowseState.OK:
        return ModelGroupsResult(state=provider.state, items=[])
    assert provider.provider_code is not None
    kind_code = _vehicle_kind_code(db, provider_code=provider.provider_code, vehicle_kind=vehicle_kind)
    state, value = _call(
        db, provider, actor_id=actor_id, purpose="catalogue_model_groups",
        call=lambda adapter: adapter.list_catalogue_model_groups(
            vehicle_kind_code=kind_code, brand=checked_brand, in_production=_in_production(mode) or None
        ),
    )
    groups: list[CatalogueModelGroupData] = list(value) if isinstance(value, list) else []  # type: ignore[arg-type]
    return ModelGroupsResult(state=state, items=groups)


def browse_variants(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    vehicle_kind: str,
    brand: str | None,
    model_group_key: str | None,
    filters: VariantFilters,
    sort: list[BrowseSort],
    page: int,
) -> VariantsResult:
    """One provider page (the identAPI's 10) for exactly the page asked,
    `total` from `includeCount`; every variant on it stored once. A query
    needs a brand or a model group — refused here, before anything is sent
    (ADR-112 (4)), as the adapter refuses it too."""

    brand, model_group_key = _check_scope(brand=brand, model_group_key=model_group_key)
    if brand is None and model_group_key is None:
        raise UnprocessableEntityError(
            "Choose a brand or a model group first: the catalogue is never browsed whole (ADR-112).",
            details={"reason": "catalogue_scope_required"},
        )
    if page < 1:
        raise UnprocessableEntityError("The page number starts at 1.", details={"reason": "page_invalid"})

    provider = _provider(db, tenant_id=tenant_id)
    if provider.state is not CatalogueBrowseState.OK:
        return VariantsResult(state=provider.state, rows=[], page=page, next_page=None, total=None)
    assert provider.provider_code is not None
    # The user's input is translated before the gateway block: a kind or a
    # value the map does not know is the user's 422, never a provider failure.
    kind_code = _vehicle_kind_code(db, provider_code=provider.provider_code, vehicle_kind=vehicle_kind)
    exfilters = _exfilters(db, provider_code=provider.provider_code, kind_code=kind_code, filters=filters)
    sort_expression = _sort_expression(sort)

    state, value = _call(
        db, provider, actor_id=actor_id, purpose="catalogue_variants",
        call=lambda adapter: adapter.search_catalogue_variants(
            vehicle_kind_code=kind_code, brand=brand, model_group_key=model_group_key,
            exfilters=exfilters, sort=sort_expression, page_number=page,
        ),
    )
    if state is not CatalogueBrowseState.OK:
        return VariantsResult(state=state, rows=[], page=page, next_page=None, total=None)
    assert isinstance(value, VariantSearchPage)
    rows = store_seen_variants(db, provider_code=provider.provider_code, masters=list(value.items))
    has_more = (value.total is not None and page * value.page_size < value.total) or (
        value.total is None and len(value.items) == value.page_size
    )
    return VariantsResult(
        state=CatalogueBrowseState.OK, rows=rows, page=page, next_page=page + 1 if has_more else None, total=value.total
    )


def _facet(
    db: Session, provider: _Provider, *, actor_id: uuid.UUID | None, kind_code: str, brand: str, model_group_key: str | None, api_field: str
) -> tuple[CatalogueBrowseState, list[str]]:
    """One facet field: one `fields=<field> distinct` request, its raw codes
    resolved to canonical values (an unknown code is a mapping gap, never
    offered)."""

    coded_field = CODED_FIELDS[api_field]
    state, raw_values = _call(
        db, provider, actor_id=actor_id, purpose=f"catalogue_facet_{api_field}",
        call=lambda adapter: adapter.facet_values(
            field=coded_field.provider_field, vehicle_kind_code=kind_code, brand=brand, model_group_key=model_group_key
        ),
    )
    if state is not CatalogueBrowseState.OK:
        return state, []
    assert provider.provider_code is not None
    values: list[str] = []
    for raw in list(raw_values) if isinstance(raw_values, list) else []:
        canonical = resolve_provider_code(
            db, provider=provider.provider_code, vehicle_kind=kind_code, code_group=coded_field.list_code, provider_code=raw
        )
        if canonical is not None and canonical.value_code not in values:
            values.append(canonical.value_code)
    db.commit()  # the gaps a facet value filed
    return CatalogueBrowseState.OK, values


def compute_facets(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    vehicle_kind: str,
    brand: str | None,
    model_group_key: str | None,
) -> VariantFacets:
    """The values the provider holds for each facet field in the drill-down
    scope — **one request per field of the fixed `FACET_FIELDS` tuple** (the
    bound is the tuple's length, written in code and pinned by the
    architecture test, never data), only once a brand is chosen: before that
    the panel shows the kind and the brand, and nothing is asked
    (ADR-112 (4)). The first field the provider refuses or fails ends the
    panel: the fields answered so far are kept, the state says why the rest
    are missing, and the breaker is charged once, not once per field."""

    brand, model_group_key = _check_scope(brand=brand, model_group_key=model_group_key)
    if brand is None:
        return VariantFacets(state=CatalogueBrowseState.OK, coded={})
    provider = _provider(db, tenant_id=tenant_id)
    if provider.state is not CatalogueBrowseState.OK:
        return VariantFacets(state=provider.state, coded={})
    assert provider.provider_code is not None
    kind_code = _vehicle_kind_code(db, provider_code=provider.provider_code, vehicle_kind=vehicle_kind)

    coded: dict[str, list[str]] = {}
    # The one loop the no-bulk guard allows by name: over the fixed field tuple, never over pages.
    for api_field in FACET_FIELDS:
        state, values = _facet(
            db, provider, actor_id=actor_id, kind_code=kind_code, brand=brand, model_group_key=model_group_key, api_field=api_field
        )
        if state is not CatalogueBrowseState.OK:
            return VariantFacets(state=state, coded=coded)
        coded[api_field] = values
    return VariantFacets(state=CatalogueBrowseState.OK, coded=coded)


def type_approval_numbers_for(variant: ModelVariant) -> list[str]:
    """De-duplicated, sorted Typenschein numbers off the variant's links."""

    numbers = {
        link.type_approval.type_approval_number
        for link in variant.type_approval_links
        if link.type_approval is not None
    }
    return sorted(numbers)
