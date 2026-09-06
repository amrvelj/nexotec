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


class CatalogueModelGroupRead(CamelModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    name: str


class CatalogueModelGroupPage(CamelModel):
    items: list[CatalogueModelGroupRead]


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
    items: list[CatalogueVariantRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool
    # PRD "Degradation by entitlement": no provider contract → browse is
    # *hidden, not broken*. False ⇒ items/total are empty and the screen
    # shows a "connect auto-i-dat" state, never a broken grid.
    browse_available: bool


class CatalogueFacetValue(CamelModel):
    value_code: str
    count: int


class CatalogueNumericFacet(CamelModel):
    min: Decimal | None
    max: Decimal | None


class CatalogueFacetsRead(CamelModel):
    """Every value that actually exists in the current drill-down scope,
    with its row count — computed from the mirror, never the provider
    (`FzgWerteGruppiert` is a later pre-aggregation accelerator, C-0)."""

    browse_available: bool
    coded: dict[str, list[CatalogueFacetValue]]
    numeric: dict[str, CatalogueNumericFacet]
