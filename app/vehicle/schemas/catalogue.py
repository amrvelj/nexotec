import datetime as dt
import enum
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.vehicle.schemas.spec_block import VehicleSpecBlockRead


class BrandCreate(CamelModel):
    code: str = Field(max_length=64, min_length=1)
    display_name: str = Field(max_length=120, min_length=1)


class BrandUpdate(CamelModel):
    display_name: str | None = Field(default=None, max_length=120, min_length=1)


class BrandRead(CamelModel):
    id: uuid.UUID
    code: str
    display_name: str
    version: int = 1
    created_at: dt.datetime
    updated_at: dt.datetime


class BrandPage(CamelModel):
    items: list[BrandRead]
    next_cursor: str | None


class MappingGapRead(CamelModel):
    id: uuid.UUID
    provider: str
    vehicle_kind: str
    code_group: str
    provider_code: str
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    occurrences: int
    resolved: bool
    resolved_at: dt.datetime | None
    resolved_value_code: str | None


class MappingGapPage(CamelModel):
    items: list[MappingGapRead]
    next_cursor: str | None


class MappingGapResolve(CamelModel):
    canonical_list_code: str = Field(max_length=64, min_length=1)
    canonical_value_code: str = Field(max_length=64, min_length=1)


# --- Catalogue browse & facet search (C-B / KAN-40, FR-C-01) ----------------


class CatalogueBrowseMode(str, enum.Enum):
    """`build` — configuring a new car / factory order: the production-year
    default is *currently in production* (PRD `NurNeue` on). `record` —
    identifying a used car: everything, in production or not (`NurNeue` off)."""

    BUILD = "build"
    RECORD = "record"


class CatalogueBrowseState(str, enum.Enum):
    """What a live catalogue answer is (ADR-112; Anto, 2026-11-07: a normal
    answer, never a 503). `ok` — the provider answered; `not_configured` —
    this Betrieb has no vehicle-data connection, the manual configuration is
    the path; `not_in_contract` — the provider refused the query (the
    `ident_basis` option is not licensed); `unavailable` — the provider
    could not be reached or failed."""

    OK = "ok"
    NOT_CONFIGURED = "notConfigured"
    NOT_IN_CONTRACT = "notInContract"
    UNAVAILABLE = "unavailable"


class CatalogueBrandRead(CamelModel):
    """One brand the provider lists for a vehicle kind (live, not stored)."""

    name: str
    brand_nr: str | None
    models: list[str]


class CatalogueBrandPage(CamelModel):
    items: list[CatalogueBrandRead]
    state: CatalogueBrowseState


class CatalogueModelGroupRead(CamelModel):
    """One model group as the provider lists it (live, not stored): `key` is
    the provider's model-group key, what `GET /catalogue/variants` takes as
    `modelGroupKey`; `productionYearTo` is null while in production."""

    key: str
    name: str
    model_search: str | None
    production_year_from: int | None
    production_year_to: int | None


class CatalogueModelGroupPage(CamelModel):
    items: list[CatalogueModelGroupRead]
    state: CatalogueBrowseState


class CatalogueVariantPrice(CamelModel):
    amount: Decimal
    year: int | None
    is_net: bool


class CatalogueVariantRead(CamelModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    brand_display_name: str
    model_group_id: uuid.UUID
    model_group_name: str
    variant_name: str
    model_year_from: int
    model_year_to: int | None
    in_production: bool
    # The five coded fields that predate the ADR-071 spec block and stay
    # declared on `ModelVariant` itself — canonical value_code strings.
    vehicle_kind: str | None
    fuel_type: str | None
    body_style: str | None
    drivetrain: str | None
    transmission: str | None
    type_approval_numbers: list[str]
    current_price: CatalogueVariantPrice | None
    spec: VehicleSpecBlockRead
    updated_at: dt.datetime


class CatalogueVariantPage(CamelModel):
    """One provider page (ADR-112): `nextCursor` is the next provider page
    number, `total` the provider's count (`includeCount`; null when it did not
    count). `state` says why a page is empty; `browseAvailable` is `state ==
    ok`, kept for the screens that read it before KAN-548."""

    items: list[CatalogueVariantRead]
    next_cursor: str | None
    total: int | None
    total_is_estimate: bool
    browse_available: bool
    state: CatalogueBrowseState


class CatalogueFacetValue(CamelModel):
    """A canonical value the provider holds in the drill-down scope. The
    identAPI's `distinct` carries no counts: `count` is null since KAN-548."""

    value_code: str
    count: int | None = None


class CatalogueNumericFacet(CamelModel):
    min: Decimal | None
    max: Decimal | None


class CatalogueFacetsRead(CamelModel):
    """Every value the provider holds in the current drill-down scope
    (`fields=<field> distinct`, one request per facet field, brand chosen
    first — ADR-112). The identAPI offers no min/max aggregates, so `numeric`
    is empty since KAN-548 and a number filter is typed freely."""

    browse_available: bool
    state: CatalogueBrowseState
    coded: dict[str, list[CatalogueFacetValue]]
    numeric: dict[str, CatalogueNumericFacet]
