"""Catalogue browse & facet search endpoints (FR-C-01) — `/v1/catalogue/*`,
**live identAPI queries** since KAN-548 (ADR-112).

Authenticated read on the Betrieb's own connection: the browser asks for a
vehicle kind, a brand, a model group and a page, and the vehicle API asks
the provider for exactly that (two hops, rule 11; the gateway runs in
process). Every variant a page returns is stored once as master data
(ADR-075), so `GET /catalogue/variants/{id}` and the configurator read a
stored row. A Betrieb without a connection, a refused query and a provider
outage are **states** on a 200 answer (`notConfigured`, `notInContract`,
`unavailable`), never a 500 or a 503 (Anto, 2026-11-07).

Bounded by `tests/architecture/test_catalogue_browse_is_a_bounded_live_query.py`
(one provider request per page, one per facet field) and
`tests/architecture/test_no_bulk_catalogue_retrieval.py` (no code path walks
the provider's pages).
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.db import get_db
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.schemas.catalogue import (
    CatalogueBrandPage,
    CatalogueBrandRead,
    CatalogueBrowseMode,
    CatalogueBrowseState,
    CatalogueFacetsRead,
    CatalogueFacetValue,
    CatalogueModelGroupPage,
    CatalogueModelGroupRead,
    CatalogueVariantPage,
    CatalogueVariantPrice,
    CatalogueVariantRead,
)
from app.vehicle.schemas.spec_block import VehicleSpecBlockRead
from app.vehicle.schemas.vehicle_mdm import (
    CatalogueColourRead,
    CatalogueImageRead,
    CatalogueOptionRead,
    CatalogueOptionRelationRead,
    CatalogueSpecificationRead,
    CatalogueTyreSpecRead,
)
from app.vehicle.services import catalogue_browse, catalogue_entitlements
from app.vehicle.services.catalogue_browse import (
    CODED_FIELDS,
    DEFAULT_VEHICLE_KIND,
    NUMERIC_FIELDS,
    SORT_FIELDS,
    BrowseSort,
    VariantFilters,
)

router = APIRouter(tags=["catalogue"])


def _catalogue_filters(
    fuelType: str | None = None,
    bodyStyle: str | None = None,
    drivetrain: str | None = None,
    transmission: str | None = None,
    vehicleClass: str | None = None,
    emissionStandard: str | None = None,
    kwMin: Decimal | None = None,
    kwMax: Decimal | None = None,
    displacementCcmMin: Decimal | None = None,
    displacementCcmMax: Decimal | None = None,
    doorsMin: Decimal | None = None,
    doorsMax: Decimal | None = None,
    seatsMin: Decimal | None = None,
    seatsMax: Decimal | None = None,
    co2GkmMin: Decimal | None = None,
    co2GkmMax: Decimal | None = None,
    q: str | None = None,
) -> VariantFilters:
    """The filters the identAPI applies exactly (its filterable fields): one
    value per coded field, inclusive `…Min`/`…Max` per number. `ps`, the list
    price and the production years are not filterable there and are not
    offered (KAN-548); «ist nicht» (KAN-476) went with the mirror."""

    coded = {
        "fuelType": fuelType, "bodyStyle": bodyStyle, "drivetrain": drivetrain, "transmission": transmission,
        "vehicleClass": vehicleClass, "emissionStandard": emissionStandard,
    }
    numeric_min = {"kw": kwMin, "displacementCcm": displacementCcmMin, "doors": doorsMin, "seats": seatsMin, "co2Gkm": co2GkmMin}
    numeric_max = {"kw": kwMax, "displacementCcm": displacementCcmMax, "doors": doorsMax, "seats": seatsMax, "co2Gkm": co2GkmMax}
    return VariantFilters(
        coded={k: v for k, v in coded.items() if v is not None and k in CODED_FIELDS},
        numeric_min={k: v for k, v in numeric_min.items() if v is not None and k in NUMERIC_FIELDS},
        numeric_max={k: v for k, v in numeric_max.items() if v is not None and k in NUMERIC_FIELDS},
        q=q,
    )


def _parse_sort(sort: str | None) -> list[BrowseSort]:
    """`kw:desc,doors:asc` → the provider's sort; an unknown field is 422,
    like every grid (the sortable fields are the ones the identAPI filters)."""

    if not sort:
        return []
    parsed: list[BrowseSort] = []
    for part in sort.split(","):
        name, _, direction = part.strip().partition(":")
        if name not in SORT_FIELDS or direction not in ("", "asc", "desc"):
            raise UnprocessableEntityError(
                f"Unknown sort field {part.strip()!r}; sortable: {', '.join(SORT_FIELDS)}.",
                details={"reason": "sort_field_unknown", "field": name},
            )
        parsed.append(BrowseSort(api_field=name, descending=direction == "desc"))
    return parsed


def _page(cursor: str | None) -> int:
    """`cursor` is the provider page number since KAN-548 (the first page
    when absent) — the name the grid's cursor paging already sends."""

    if cursor is None or cursor == "":
        return 1
    if not cursor.isdigit() or int(cursor) < 1:
        raise UnprocessableEntityError("cursor must be a page number from 1.", details={"reason": "cursor_invalid"})
    return int(cursor)


def _variant_read(variant: ModelVariant) -> CatalogueVariantRead:
    group = variant.model_group
    price = None
    if variant.base_price is not None:
        price = CatalogueVariantPrice(
            amount=variant.base_price, year=variant.base_price_year, is_net=bool(variant.price_is_net)
        )
    return CatalogueVariantRead(
        id=variant.id,
        brand_id=group.brand_id,
        brand_display_name=group.brand.display_name,
        model_group_id=group.id,
        model_group_name=group.name,
        variant_name=variant.name,
        model_year_from=variant.model_year_from,
        model_year_to=variant.model_year_to,
        in_production=variant.model_year_to is None,
        vehicle_kind=variant.vehicle_kind,
        fuel_type=variant.fuel_type,
        body_style=variant.body_style,
        drivetrain=variant.drivetrain,
        transmission=variant.transmission,
        type_approval_numbers=catalogue_browse.type_approval_numbers_for(variant),
        current_price=price,
        spec=VehicleSpecBlockRead.model_validate(variant, from_attributes=True),
        updated_at=variant.updated_at,
    )


@router.get("/catalogue/brands", response_model=CatalogueBrandPage)
def list_brands(
    vehicle_kind: str = Query(default=DEFAULT_VEHICLE_KIND, alias="vehicleKind", max_length=64),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Every brand the provider lists for a vehicle kind, with its model
    names — live, one request, not stored (ADR-112)."""

    result = catalogue_browse.list_brands(
        db, tenant_id=principal.tenant_id, actor_id=principal.user_id, vehicle_kind=vehicle_kind
    )
    return CatalogueBrandPage(
        items=[CatalogueBrandRead(name=b.name, brand_nr=b.brand_nr, models=list(b.models)) for b in result.items],
        state=result.state,
    )


@router.get("/catalogue/model-groups", response_model=CatalogueModelGroupPage)
def list_model_groups(
    brand: str = Query(min_length=1, max_length=120),
    vehicle_kind: str = Query(default=DEFAULT_VEHICLE_KIND, alias="vehicleKind", max_length=64),
    mode: CatalogueBrowseMode = CatalogueBrowseMode.BUILD,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The provider's model groups of a brand — live, one request. In `build`
    mode only the groups in production (PRD `NurNeue`)."""

    result = catalogue_browse.list_model_groups(
        db, tenant_id=principal.tenant_id, actor_id=principal.user_id, vehicle_kind=vehicle_kind, brand=brand, mode=mode
    )
    return CatalogueModelGroupPage(
        items=[
            CatalogueModelGroupRead(
                key=g.model_group_key, name=g.description, model_search=g.model_search,
                production_year_from=g.production_year_from, production_year_to=g.production_year_to,
            )
            for g in result.items
        ],
        state=result.state,
    )


@router.get("/catalogue/variants", response_model=CatalogueVariantPage)
def browse_variants(
    vehicle_kind: str = Query(default=DEFAULT_VEHICLE_KIND, alias="vehicleKind", max_length=64),
    brand: str | None = Query(default=None, max_length=120),
    model_group_key: str | None = Query(default=None, alias="modelGroupKey", max_length=64),
    filters: VariantFilters = Depends(_catalogue_filters),
    sort: str | None = Query(default=None, description="e.g. 'kw:desc,doors:asc'"),
    cursor: str | None = Query(default=None, description="the provider page number; absent = page 1"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """One provider page of variants for the drill-down and the filters —
    exactly the page asked (ADR-112 (4)); every variant on it stored once.
    The production-year default of the mode (PRD `NurNeue`) applies to the
    model-group list: the identAPI's variant rows are not filterable on
    `productionEnd`, so a page is never narrowed by it here."""

    result = catalogue_browse.browse_variants(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        vehicle_kind=vehicle_kind,
        brand=brand,
        model_group_key=model_group_key,
        filters=filters,
        sort=_parse_sort(sort),
        page=_page(cursor),
    )
    return CatalogueVariantPage(
        items=[_variant_read(v) for v in result.rows],
        next_cursor=str(result.next_page) if result.next_page is not None else None,
        total=result.total,
        total_is_estimate=False,
        browse_available=result.state is CatalogueBrowseState.OK,
        state=result.state,
    )


@router.get("/catalogue/facets", response_model=CatalogueFacetsRead)
def catalogue_facets(
    vehicle_kind: str = Query(default=DEFAULT_VEHICLE_KIND, alias="vehicleKind", max_length=64),
    brand: str | None = Query(default=None, max_length=120),
    model_group_key: str | None = Query(default=None, alias="modelGroupKey", max_length=64),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The values the provider holds per facet field in the drill-down scope
    — one request per field, only once a brand is chosen (before that: no
    call, nothing offered)."""

    facets = catalogue_browse.compute_facets(
        db, tenant_id=principal.tenant_id, actor_id=principal.user_id, vehicle_kind=vehicle_kind, brand=brand,
        model_group_key=model_group_key,
    )
    return CatalogueFacetsRead(
        browse_available=facets.state is CatalogueBrowseState.OK,
        state=facets.state,
        coded={api_field: [CatalogueFacetValue(value_code=code) for code in codes] for api_field, codes in facets.coded.items()},
        numeric={},
    )


@router.get("/catalogue/variants/{model_variant_id}", response_model=CatalogueVariantRead)
def get_variant(
    model_variant_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """One variant, the same shape a browse row has (KAN-42, C-D): the
    identification picker names variants by id, and the configurator starts
    from the full row exactly as it does from a browse selection. A mirror
    read — no provider call."""

    variant = db.get(ModelVariant, model_variant_id)
    if variant is None:
        raise NotFoundError(f"Catalogue variant {model_variant_id} was not found.")
    return _variant_read(variant)


@router.get("/catalogue/variants/{model_variant_id}/specification", response_model=CatalogueSpecificationRead)
def get_variant_specification(
    model_variant_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The Configurator's own read of PR-4's tenant-scoped catalogue
    mirror (KAN-43, C-E) — options/colours/tyres/images/relations for a
    variant the advisor is building or has matched a record to.

    Keyed by `model_variant_id` directly, never by a `vehicle_id`: a
    configuration attaches to a catalogue variant (ADR-070 — never
    `vehicle_mdm`), so `/vehicle-mdm/{id}/catalogue-specification`
    (WP-6 PR-5, still used by the vehicle 360 screen) does not apply here.
    Both routes share the same service function and the same
    entitlement-degradation posture.
    """

    if db.get(ModelVariant, model_variant_id) is None:
        raise NotFoundError(f"Catalogue variant {model_variant_id} was not found.")

    result = catalogue_entitlements.get_catalogue_specification(
        db, tenant_id=principal.tenant_id, model_variant_id=model_variant_id
    )
    return CatalogueSpecificationRead(
        has_catalogue_match=result.has_catalogue_match,
        has_provider_connection=result.has_provider_connection,
        packages_available=result.packages_available,
        images_available=result.images_available,
        dealer_can_upload_images=result.dealer_can_upload_images,
        options=[CatalogueOptionRead.model_validate(o, from_attributes=True) for o in result.options],
        colours=[CatalogueColourRead.model_validate(c, from_attributes=True) for c in result.colours],
        tyre_specs=[CatalogueTyreSpecRead.model_validate(t, from_attributes=True) for t in result.tyre_specs],
        images=[CatalogueImageRead.model_validate(i, from_attributes=True) for i in result.images],
        option_relations=[
            CatalogueOptionRelationRead.model_validate(r, from_attributes=True) for r in result.option_relations
        ],
    )
