import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.vehicle.models.configuration import (
    ConfigurationMatchMethod,
    ConfigurationMatchStatus,
    ConfigurationMode,
    ConfigurationSource,
)
from app.vehicle.schemas.spec_block import VehicleSpecBlockInput, VehicleSpecBlockRead


class ConfigurationOptionInput(CamelModel):
    description: str = Field(min_length=1, max_length=2000)
    variant_option_id: uuid.UUID | None = None
    option_code: str | None = Field(default=None, max_length=64)
    option_group: str | None = Field(default=None, max_length=64)
    price: Decimal | None = None
    is_included: bool = False
    is_package: bool = False
    selected: bool = True
    equipment_features: list[str] = Field(default_factory=list)


class ConfigurationOptionRead(CamelModel):
    id: uuid.UUID
    sequence: int
    variant_option_id: uuid.UUID | None
    option_code: str | None
    description: str
    option_group: str | None
    price: Decimal | None
    is_included: bool
    is_package: bool
    selected: bool
    equipment_features: list[str]


# The observed-at-capture fields and colours a host may set on create/update.
class _ConfigurationObserved(CamelModel):
    vin: str | None = Field(default=None, max_length=17)
    stammnummer: str | None = Field(default=None, max_length=9)
    type_approval_number: str | None = Field(default=None, max_length=6)
    first_registration_date: dt.date | None = None
    licence_plate: str | None = Field(default=None, max_length=16)
    mileage_km: int | None = Field(default=None, ge=0)
    exterior_colour: str | None = Field(default=None, max_length=120)
    interior_colour: str | None = Field(default=None, max_length=120)
    exterior_colour_surcharge: Decimal | None = None
    interior_colour_surcharge: Decimal | None = None
    notes: str | None = None
    # The five coded spec fields (canonical value_code strings) — declared
    # directly on the carrier, not inside the `spec` block payload, exactly
    # like `CatalogueVariantRead`.
    vehicle_kind: str | None = Field(default=None, max_length=64)
    fuel_type: str | None = Field(default=None, max_length=64)
    body_style: str | None = Field(default=None, max_length=64)
    drivetrain: str | None = Field(default=None, max_length=64)
    transmission: str | None = Field(default=None, max_length=64)


class ConfigurationCreate(_ConfigurationObserved):
    source: ConfigurationSource
    mode: ConfigurationMode
    match_method: ConfigurationMatchMethod
    # provider path: the variant whose spec block is copied at capture
    catalogue_variant_id: uuid.UUID | None = None
    # manual path: the spec block, field by field
    spec: VehicleSpecBlockInput | None = None
    brand_display_name: str | None = Field(default=None, max_length=120)
    model_group_name: str | None = Field(default=None, max_length=120)
    variant_name: str | None = Field(default=None, max_length=160)


class ConfigurationUpdate(_ConfigurationObserved):
    mode: ConfigurationMode | None = None
    catalogue_match_status: ConfigurationMatchStatus | None = None
    match_method: ConfigurationMatchMethod | None = None
    catalogue_variant_id: uuid.UUID | None = None
    spec: VehicleSpecBlockInput | None = None
    brand_display_name: str | None = Field(default=None, max_length=120)
    model_group_name: str | None = Field(default=None, max_length=160)
    variant_name: str | None = Field(default=None, max_length=160)


class ConfigurationOptionsReplace(CamelModel):
    options: list[ConfigurationOptionInput]


class ConfigurationRead(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source: ConfigurationSource
    mode: ConfigurationMode
    catalogue_match_status: ConfigurationMatchStatus
    match_method: ConfigurationMatchMethod
    catalogue_variant_id: uuid.UUID | None
    catalogue_variant_label: str | None
    vehicle_id: uuid.UUID | None
    vehicle_label: str | None
    vin: str | None
    stammnummer: str | None
    type_approval_number: str | None
    first_registration_date: dt.date | None
    licence_plate: str | None
    mileage_km: int | None
    brand_display_name: str | None
    model_group_name: str | None
    variant_name: str | None
    vehicle_kind: str | None
    fuel_type: str | None
    body_style: str | None
    drivetrain: str | None
    transmission: str | None
    exterior_colour: str | None
    interior_colour: str | None
    exterior_colour_surcharge: Decimal | None
    interior_colour_surcharge: Decimal | None
    # Populated by the API layer from the configuration's own flat spec-block
    # columns — see `configuration.py::_read`.
    spec: VehicleSpecBlockRead
    overridden_fields: list[str]
    options: list[ConfigurationOptionRead]
    notes: str | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
