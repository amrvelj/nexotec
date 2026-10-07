"""Wire shapes of the FR-C-02 identification waterfall (C-D / KAN-42)."""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.vehicle.models.configuration import ConfigurationMatchMethod
from app.vehicle.services.identification import (
    IdentificationNote,
    IdentificationOutcome,
    IdentifierKind,
    NewPriceSource,
)


class VariantCandidateRead(CamelModel):
    catalogue_variant_id: uuid.UUID
    brand_display_name: str | None
    model_group_name: str | None
    variant_name: str
    model_year_from: int
    model_year_to: int | None
    ps: int | None
    kw: int | None
    base_price: Decimal | None
    base_price_year: int | None
    new_price_for_year: Decimal | None


class PlateRecordRead(CamelModel):
    vehicle_kind_code: str
    brand_name: str
    model_description: str
    production_from: int | None
    production_to: int | None
    type_approval_number: str
    first_registration_date: dt.date | None
    stammnummer: str


class ExistingVehicleRead(CamelModel):
    vehicle_id: uuid.UUID
    vehicle_number: str
    vin: str
    catalogue_variant_id: uuid.UUID | None
    reusable_configuration_id: uuid.UUID | None


class ObservedIdentityRead(CamelModel):
    vin: str | None
    licence_plate: str | None
    stammnummer: str | None
    type_approval_number: str | None
    first_registration_date: dt.date | None
    werkscode: str | None


class IdentificationRead(CamelModel):
    kind: IdentifierKind
    match_method: ConfigurationMatchMethod
    outcome: IdentificationOutcome
    observed: ObservedIdentityRead
    existing_vehicle: ExistingVehicleRead | None
    variants: list[VariantCandidateRead]
    plate_records: list[PlateRecordRead]
    plate_records_interchangeable: bool
    plate_records_conflict: bool
    best_match_available: bool
    notes: list[IdentificationNote]


class BestMatchFieldRead(CamelModel):
    field: str
    entered: str | None
    matched: str | None
    agrees: bool


class BestMatchProposalRead(CamelModel):
    candidate: VariantCandidateRead
    match_code: int
    new_price: Decimal
    new_price_source: NewPriceSource
    fields: list[BestMatchFieldRead]
    requires_confirmation: bool
