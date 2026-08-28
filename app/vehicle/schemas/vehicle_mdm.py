"""VehicleMdm schemas (WP-5 PR-9). Identity fields (vin, stammnummer,
first_registration_date) are writable ONLY through VehicleMdmUpdate — no
other schema in this codebase (a future Stock/Inventory item schema
included) may ever carry them as writable fields, per ADR-045.
"""

import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.core.validators import Vin
from app.customer.public import VehiclePartyRole
from app.vehicle.models.vehicle_history import OdometerSource
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleStatus


class VehicleMdmCreate(CamelModel):
    vin: Vin
    catalogue_variant_id: uuid.UUID | None = None
    stammnummer: str | None = Field(default=None, max_length=9)
    type_approval_number: str | None = Field(default=None, max_length=6)
    first_registration_date: dt.date | None = None


class VehicleMdmUpdate(CamelModel):
    """PATCH body for the vehicle's own identity fields — the only place
    they're editable (ADR-045). A stock/inventory item, once that context
    exists, renders these read-only with a link through to here.
    """

    vin: Vin | None = None
    stammnummer: str | None = Field(default=None, max_length=9)
    type_approval_number: str | None = Field(default=None, max_length=6)
    first_registration_date: dt.date | None = None
    vehicle_status: VehicleStatus | None = None


class VehicleMdmRead(CamelModel):
    id: uuid.UUID
    vin: str
    vehicle_number: str
    stammnummer: str | None
    type_approval_number: str | None
    first_registration_date: dt.date | None
    catalogue_variant_id: uuid.UUID | None
    catalogue_match_status: CatalogueMatchStatus
    vehicle_status: VehicleStatus
    merged_into_vehicle_id: uuid.UUID | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class VehicleMdmCreateResult(CamelModel):
    """FR-V-15: entering a VIN that already exists is NOT a validation
    error — it is the same car. `created=False` means the caller's VIN
    already resolved to an existing record, returned here in full so the
    UI can offer to open it; `created=True` means a genuinely new
    VehicleMdm was made. Both cases return 200 with a real VehicleMdmRead
    — never a bare 409/422 with no record to act on.
    """

    created: bool
    vehicle: VehicleMdmRead


class VehicleMdmPage(CamelModel):
    items: list[VehicleMdmRead]
    next_cursor: str | None


class VehiclePickerCandidate(CamelModel):
    """A row in the Wechselschild/conflict picker (FR-V-06) — enough to
    recognise the vehicle, never the full record.
    """

    id: uuid.UUID
    vehicle_number: str
    vin: str
    plate: str | None
    plate_group_id: uuid.UUID | None
    is_conflict: bool


class VehicleAllocatePartyRequest(CamelModel):
    """FR-V-05: "Allocate to customer" — the vehicle-side entry point.
    Same allocate_vehicle_party call the customer-side dialog uses
    (Customers FR-19) — one dialog, not two that drift.
    """

    customer_id: uuid.UUID
    role: VehiclePartyRole


class VehiclePartyAllocationRead(CamelModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    customer_id: uuid.UUID
    role: VehiclePartyRole
    effective_from: dt.datetime
    effective_to: dt.datetime | None


class VehiclePlateRead(CamelModel):
    id: uuid.UUID
    plate: str
    canton: str
    valid_from: dt.date
    valid_to: dt.date | None
    is_interchangeable: bool
    plate_group_id: uuid.UUID | None


class VehicleOdometerReadingRead(CamelModel):
    id: uuid.UUID
    value: int
    reading_date: dt.date
    source: OdometerSource
    implausible: bool


class VehicleOdometerReadingCreate(CamelModel):
    value: int = Field(ge=0)
    reading_date: dt.date
    source: OdometerSource


class VehicleAccessoryRead(CamelModel):
    id: uuid.UUID
    accessory_type: str
    description: str | None
    valid_from: dt.date
    valid_to: dt.date | None


class VehicleAccessoryCreate(CamelModel):
    accessory_type: str = Field(max_length=64, min_length=1)
    description: str | None = None
    valid_from: dt.date


class VehicleSearchResult(CamelModel):
    """The ONE search box's response shape (FR-V-06/FR-V-16): the string
    either resolves as an identifier (above the grid) or filters it —
    never both, decided server-side from the string's own shape, never by
    which of two fields the user typed into.
    """

    resolved: VehicleMdmRead | None
    picker_candidates: list[VehiclePickerCandidate]
    filtered: VehicleMdmPage
