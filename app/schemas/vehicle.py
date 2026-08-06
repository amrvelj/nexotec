import datetime as dt
import uuid

from pydantic import Field, model_validator

from app.models.vehicle import CustodyEventType, RegistrationStatus, VehicleCondition, VehicleStatus
from app.schemas.base import CamelModel
from app.schemas.validators import CantonCode, Vin

_MIN_MODEL_YEAR = 1981  # 17-char VIN standard start; pre-1981 vehicles descoped (Swiss addendum #13)
_MAX_MODEL_YEAR = 2100
_TERMINAL_STATUSES = {VehicleStatus.TOTALED, VehicleStatus.SCRAPPED}


class VehicleCreate(CamelModel):
    """`status` defaults to `in_transit` if omitted, matching the natural
    start of the spec's lifecycle. Rejecting a caller-supplied terminal
    status (totaled/scrapped) at creation — nonsensical to create an
    already-dead record.

    No `current_custodian_partner_id` field: creating a Vehicle always
    establishes the creating dealer as the first custodian via an implicit
    `acquired` custody event (services/vehicle.py) — an assumption filling
    the original spec's open question 8 ("who can create a Vehicle before
    it has a custodian?"), not something stated explicitly. Flagged in the PR.
    """

    vin: Vin
    make: str = Field(max_length=100, min_length=1)
    model: str = Field(max_length=100, min_length=1)
    model_year: int = Field(ge=_MIN_MODEL_YEAR, le=_MAX_MODEL_YEAR)
    trim: str | None = Field(default=None, max_length=100)
    engine: str | None = Field(default=None, max_length=100)
    condition: VehicleCondition
    vehicle_type: str | None = Field(default=None, max_length=64)
    fuel_type: str | None = Field(default=None, max_length=64)
    body_style: str | None = Field(default=None, max_length=64)
    drivetrain: str | None = Field(default=None, max_length=64)
    transmission: str | None = Field(default=None, max_length=64)
    exterior_color: str | None = Field(default=None, max_length=64)
    interior_color: str | None = Field(default=None, max_length=64)
    energy_efficiency_category: str | None = Field(default=None, max_length=4)
    co2_emissions_gkm: int | None = Field(default=None, ge=0)
    odometer: int | None = Field(default=None, ge=0)
    registration_status: RegistrationStatus = RegistrationStatus.UNREGISTERED
    registration_canton: CantonCode | None = None
    status: VehicleStatus = VehicleStatus.IN_TRANSIT

    @model_validator(mode="after")
    def _reject_terminal_status_at_creation(self) -> "VehicleCreate":
        if self.status in _TERMINAL_STATUSES:
            raise ValueError(f"A new vehicle cannot be created with status '{self.status.value}'.")
        return self


class VehicleUpdate(CamelModel):
    """`status` changes are custodian-gated at the service layer (only the
    current custodian or platform_admin — see services/vehicle.py); every
    other field here is a shared-catalog spec correction, open to any
    dealer_admin/inventory user. `current_custodian_partner_id` is still
    never settable here — only custody-events mutate it.
    """

    make: str | None = Field(default=None, max_length=100, min_length=1)
    model: str | None = Field(default=None, max_length=100, min_length=1)
    model_year: int | None = Field(default=None, ge=_MIN_MODEL_YEAR, le=_MAX_MODEL_YEAR)
    trim: str | None = Field(default=None, max_length=100)
    engine: str | None = Field(default=None, max_length=100)
    condition: VehicleCondition | None = None
    vehicle_type: str | None = Field(default=None, max_length=64)
    fuel_type: str | None = Field(default=None, max_length=64)
    body_style: str | None = Field(default=None, max_length=64)
    drivetrain: str | None = Field(default=None, max_length=64)
    transmission: str | None = Field(default=None, max_length=64)
    exterior_color: str | None = Field(default=None, max_length=64)
    interior_color: str | None = Field(default=None, max_length=64)
    energy_efficiency_category: str | None = Field(default=None, max_length=4)
    co2_emissions_gkm: int | None = Field(default=None, ge=0)
    odometer: int | None = Field(default=None, ge=0)
    registration_status: RegistrationStatus | None = None
    registration_canton: CantonCode | None = None
    status: VehicleStatus | None = None


class VehicleRead(CamelModel):
    """`status` and `current_custodian_partner_id` are typed nullable here
    even though the DB columns are not — they're redacted to null in the
    response unless the requester is the current custodian or platform_admin
    (Swiss addendum Round 3 visibility rule). See services/vehicle.py's
    serialization helper for where that redaction actually happens; this
    schema only describes the shape.
    """

    id: uuid.UUID
    vin: str
    make: str
    model: str
    model_year: int
    trim: str | None
    engine: str | None
    condition: VehicleCondition
    vehicle_type: str | None
    fuel_type: str | None
    body_style: str | None
    drivetrain: str | None
    transmission: str | None
    exterior_color: str | None
    interior_color: str | None
    energy_efficiency_category: str | None
    co2_emissions_gkm: int | None
    odometer: int | None
    registration_status: RegistrationStatus
    registration_canton: str | None
    status: VehicleStatus | None
    current_custodian_partner_id: uuid.UUID | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class VehiclePage(CamelModel):
    items: list[VehicleRead]
    next_cursor: str | None


class CustodyEventCreate(CamelModel):
    """`partner_id` defaults to the caller's own tenant and must equal it
    unless the caller is platform_admin — a dealer can't claim or relinquish
    custody on another dealer's behalf (see services/vehicle.py).
    """

    event_type: CustodyEventType
    partner_id: uuid.UUID | None = None
    event_date: dt.datetime | None = None
    transaction_id: uuid.UUID | None = None


class CustodyEventRead(CamelModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    partner_id: uuid.UUID
    event_type: CustodyEventType
    event_date: dt.datetime
    transaction_id: uuid.UUID | None
    created_at: dt.datetime
    created_by: uuid.UUID | None


class CustodyEventPage(CamelModel):
    items: list[CustodyEventRead]
    next_cursor: str | None
