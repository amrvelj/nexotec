import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.core.validators import NormalisedVin
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


_CODED_FIELD_RULE = (
    "An active value of the reference list of the same name. Required when `source` is `manual` "
    "(422 `coded_fields_required`), and never cleared on such a configuration (422 `coded_field_required`); "
    "on a `provider` configuration it comes from the catalogue and a change is refused (422 `coded_field_locked`)."
)


# The observed-at-capture fields and colours a host may set on create/update.
class _ConfigurationObserved(CamelModel):
    # KAN-338: normalised and validated as Stock's VIN is (FR-V-15) — the
    # vehicle link follows the VIN (KAN-326), so a lower-cased spelling of a
    # known car's VIN must find that car, never unlink it.
    vin: NormalisedVin | None = None
    stammnummer: str | None = Field(default=None, max_length=9)
    type_approval_number: str | None = Field(default=None, max_length=6)
    first_registration_date: dt.date | None = None
    licence_plate: str | None = Field(default=None, max_length=16)
    mileage_km: int | None = Field(default=None, ge=0)
    exterior_colour: str | None = Field(default=None, max_length=120)
    interior_colour: str | None = Field(default=None, max_length=120)
    exterior_colour_surcharge: Decimal | None = None
    interior_colour_surcharge: Decimal | None = None
    wheels: str | None = Field(default=None, max_length=120)
    wheels_surcharge: Decimal | None = None
    notes: str | None = None
    # The five coded spec fields (canonical value_code strings) — declared
    # directly on the carrier, not inside the `spec` block payload, exactly
    # like `CatalogueVariantRead`.
    # KAN-247 (Anto, 2026-10-12): required on a manual configuration's create,
    # never clearable there; locked on a provider configuration. Enforced in
    # the service (the rule depends on `source`), stated here for the contract.
    vehicle_kind: str | None = Field(default=None, max_length=64, description=_CODED_FIELD_RULE)
    fuel_type: str | None = Field(default=None, max_length=64, description=_CODED_FIELD_RULE)
    body_style: str | None = Field(default=None, max_length=64, description=_CODED_FIELD_RULE)
    drivetrain: str | None = Field(default=None, max_length=64, description=_CODED_FIELD_RULE)
    transmission: str | None = Field(default=None, max_length=64, description=_CODED_FIELD_RULE)


class ConfigurationCreate(_ConfigurationObserved):
    source: ConfigurationSource
    mode: ConfigurationMode
    match_method: ConfigurationMatchMethod
    # provider path: the variant whose spec block is copied at capture
    catalogue_variant_id: uuid.UUID | None = None
    # KAN-42 (C-D): set only when the advisor confirmed a FahrzeugeMatch
    # proposal for `catalogue_variant_id` — the MatchCode it carried. 2
    # records `best_match_confirmed`; 1 records `matched`.
    confirmed_best_match_code: int | None = Field(default=None, ge=1, le=2)
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
    wheels: str | None
    wheels_surcharge: Decimal | None
    # Populated by the API layer from the configuration's own flat spec-block
    # columns — see `configuration.py::_read`.
    spec: VehicleSpecBlockRead
    overridden_fields: list[str]
    options: list[ConfigurationOptionRead]
    notes: str | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ResyncFieldRead(CamelModel):
    """FR-C-16 — one field on which the catalogue now disagrees. Values are
    rendered as strings or numbers exactly as stored."""

    field: str
    current: str | int | float | bool | None
    catalogue: str | int | float | bool | None
    overridden: bool


class ResyncRequest(CamelModel):
    fields: list[str] = Field(min_length=1)
