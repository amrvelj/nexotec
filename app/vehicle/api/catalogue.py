"""Catalogue browse & facet search endpoints (C-B / KAN-40, FR-C-01) —
`/v1/catalogue/*`, the FR-C-17 read surface for the catalogue mirror.

Authenticated read; the catalogue rows themselves are global (same as
`/v1/vehicle-mdm` reads). `principal.tenant_id` is passed only so the
service can report `browseAvailable=false` for a tenant with no provider
contract (PRD "Browse ... hidden, not broken"). **No provider call is made
on any path here** — asserted by
`tests/architecture/test_catalogue_browse_makes_no_provider_call.py`.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.schemas.catalogue import (
    CatalogueBrowseMode,
    CatalogueFacetsRead,
    CatalogueFacetValue,
    CatalogueModelGroupPage,
    CatalogueModelGroupRead,
    CatalogueNumericFacet,
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
    CODED_FACET_COLUMNS,
    NUMERIC_FACET_COLUMNS,
    VariantFilters,
)

router = APIRouter(tags=["catalogue"])
settings = get_settings()

# U-02/U-03: only columns with a supporting index are offered as sortable —
# see alembic/versions/vehicle/<rev>_catalogue_browse_sort_indexes.py.
CATALOGUE_VARIANT_SORT_FIELDS: dict[str, object] = {
    "variantName": ModelVariant.name,
    "ps": ModelVariant.ps,
    "kw": ModelVariant.kw,
    "displacementCcm": ModelVariant.displacement_ccm,
    "basePrice": ModelVariant.base_price,
    "modelYearFrom": ModelVariant.model_year_from,
    "updatedAt": ModelVariant.updated_at,
}
_DEFAULT_SORT = [SortField(api_name="variantName", column=ModelVariant.name, direction="asc", nullable=False)]


def _catalogue_filters(
    fuelType: str | None = None,
    bodyStyle: str | None = None,
    drivetrain: str | None = None,
    transmission: str | None = None,
    vehicleKind: str | None = None,
    vehicleClass: str | None = None,
    emissionStandard: str | None = None,
    engineCycle: str | None = None,
    psMin: Decimal | None = None,
    psMax: Decimal | None = None,
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
    modelYearFromMin: Decimal | None = None,
    modelYearFromMax: Decimal | None = None,
    basePriceMin: Decimal | None = None,
    basePriceMax: Decimal | None = None,
    inProduction: bool | None = None,
    q: str | None = None,
) -> VariantFilters:
    coded = {
        "fuelType": fuelType, "bodyStyle": bodyStyle, "drivetrain": drivetrain,
        "transmission": transmission, "vehicleKind": vehicleKind, "vehicleClass": vehicleClass,
        "emissionStandard": emissionStandard, "engineCycle": engineCycle,
    }
    numeric_min = {
        "ps": psMin, "kw": kwMin, "displacementCcm": displacementCcmMin, "doors": doorsMin,
        "seats": seatsMin, "co2Gkm": co2GkmMin, "modelYearFrom": modelYearFromMin, "basePrice": basePriceMin,
    }
    numeric_max = {
        "ps": psMax, "kw": kwMax, "displacementCcm": displacementCcmMax, "doors": doorsMax,
        "seats": seatsMax, "co2Gkm": co2GkmMax, "modelYearFrom": modelYearFromMax, "basePrice": basePriceMax,
    }
    return VariantFilters(
        coded={k: v for k, v in coded.items() if v is not None and k in CODED_FACET_COLUMNS},
        numeric_min={k: v for k, v in numeric_min.items() if v is not None and k in NUMERIC_FACET_COLUMNS},
        numeric_max={k: v for k, v in numeric_max.items() if v is not None and k in NUMERIC_FACET_COLUMNS},
        in_production=inProduction,
        q=q,
    )


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


@router.get("/catalogue/model-groups", response_model=CatalogueModelGroupPage)
def list_model_groups(
    brand_id: uuid.UUID = Query(alias="brandId"),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    groups = catalogue_browse.list_model_groups(db, brand_id=brand_id)
    return CatalogueModelGroupPage(
        items=[CatalogueModelGroupRead(id=g.id, brand_id=g.brand_id, name=g.name) for g in groups]
    )


@router.get("/catalogue/variants", response_model=CatalogueVariantPage)
def browse_variants(
    brand_id: uuid.UUID | None = Query(default=None, alias="brandId"),
    model_group_id: uuid.UUID | None = Query(default=None, alias="modelGroup"),
    mode: CatalogueBrowseMode = CatalogueBrowseMode.BUILD,
    filters: VariantFilters = Depends(_catalogue_filters),
    sort: str | None = Query(default=None, description="e.g. 'ps:desc,variantName:asc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=CATALOGUE_VARIANT_SORT_FIELDS) or _DEFAULT_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate, browse_available = catalogue_browse.browse_variants(
        db,
        tenant_id=principal.tenant_id,
        brand_id=brand_id,
        model_group_id=model_group_id,
        filters=filters,
        mode=mode,
        params=params,
    )
    return CatalogueVariantPage(
        items=[_variant_read(v) for v in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
        browse_available=browse_available,
    )


@router.get("/catalogue/facets", response_model=CatalogueFacetsRead)
def catalogue_facets(
    brand_id: uuid.UUID | None = Query(default=None, alias="brandId"),
    model_group_id: uuid.UUID | None = Query(default=None, alias="modelGroup"),
    mode: CatalogueBrowseMode = CatalogueBrowseMode.BUILD,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    facets = catalogue_browse.compute_facets(
        db, tenant_id=principal.tenant_id, brand_id=brand_id, model_group_id=model_group_id, mode=mode
    )
    return CatalogueFacetsRead(
        browse_available=facets.browse_available,
        coded={
            field: [CatalogueFacetValue(value_code=vc, count=n) for vc, n in values]
            for field, values in facets.coded.items()
        },
        numeric={
            field: CatalogueNumericFacet(min=low, max=high) for field, (low, high) in facets.numeric.items()
        },
    )


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
