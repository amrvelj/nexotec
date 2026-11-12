"""VehicleMdm schemas (WP-5 PR-9). Identity fields (vin, stammnummer,
first_registration_date) are writable ONLY through VehicleMdmUpdate — no
other schema in this codebase (a future Stock/Inventory item schema
included) may ever carry them as writable fields, per ADR-045.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

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

    @model_validator(mode="after")
    def _required_fields_are_never_cleared(self) -> "VehicleMdmUpdate":
        """KAN-561 — `None` here means "not sent"; a `null` the caller sent
        for a field the record must always have (the VIN is mandatory in
        vehicle-mdm, a status always set) is a 422, never a NOT NULL
        violation that reached the database."""

        required = {"vin": "vin", "vehicle_status": "vehicleStatus"}
        sent = self.model_fields_set
        cleared = [api for name, api in required.items() if name in sent and getattr(self, name) is None]
        if cleared:
            raise ValueError(f"{', '.join(cleared)} cannot be cleared.")
        return self


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
    # KAN-161 (U-07): the grid footer's count — exact up to
    # count_exact_threshold, an estimate above it.
    total: int
    total_is_estimate: bool


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
    # KAN-140: the holder's name, resolved live inside the customer context
    # (customer.public) within the caller's group — never stored here, so
    # no three-column label. Optional: an additive contract change.
    display_name: str | None = None


class VehiclePlateRead(CamelModel):
    id: uuid.UUID
    plate: str
    canton: str
    valid_from: dt.date
    valid_to: dt.date | None
    is_interchangeable: bool
    plate_group_id: uuid.UUID | None
    # KAN-467: «beenden» takes If-Match on the assignment.
    version: int


class PlateVersionRefIn(CamelModel):
    """KAN-467 — one other car's assignment the user answered about, at the
    `version` the refusal showed (`details.holders[].version`): the If-Match
    of the row the answer changes."""

    plate_id: uuid.UUID
    version: int


class VehiclePlateCreate(CamelModel):
    """KAN-467 — «Kontrollschild erfassen». `plate` may carry its canton
    prefix and spaces (stored as «ZH 123456»); `canton` is one of the 26
    and decides, with the number, which plate it is (ZH 123456 ≠ BE 123456).

    When the plate is current on another car of the group the first POST is
    refused (409 `plate_current_on_other_vehicle`, the cars in
    `details.holders` with each assignment's `version`; past the plate-read
    limit `details.holdersWithheld` instead); the user's answer comes back on
    the same POST: `moveFrom` — exactly the listed assignments at their
    versions, which end the day before `validFrom` — or `wechselschildWith`,
    the one other car's assignment, to form a Wechselschild."""

    plate: str = Field(min_length=1, max_length=16)
    canton: str = Field(min_length=2, max_length=2)
    valid_from: dt.date
    valid_to: dt.date | None = None
    move_from: list[PlateVersionRefIn] = Field(default_factory=list, max_length=2)
    wechselschild_with: PlateVersionRefIn | None = None


class VehiclePlateEnd(CamelModel):
    """KAN-467 — «beenden»: the last day the plate was on the car."""

    valid_to: dt.date


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


class CatalogueOptionRead(CamelModel):
    id: uuid.UUID
    option_code: str
    description: str
    option_group: str | None
    price: Decimal | None
    is_included: bool
    is_package: bool
    equipment_features: list[str]
    # KAN-211 — the manufacturer's own code (identAPI `optionCode`); the
    # advisor searches the list by it and by the description.
    manufacturer_code: str | None = None


CatalogueDetailState = Literal["present", "notInContract", "unavailable", "notLoaded"]


class CatalogueDetailStateRead(CamelModel):
    """KAN-211 (ADR-123) — one detail's state for the calling Betrieb.
    `present`: loaded (possibly empty) · `notInContract`: the licence does
    not include it (the screen says so, FR-C-06) · `unavailable`: the
    provider could not be reached, asked again on the next open ·
    `notLoaded`: never asked (no connection, or no key at the provider).
    `note` says why a state is what it is where that matters: the failure's
    class name on `unavailable` / `notInContract`, `entitlement_absent` when
    the probe decided, and on `present` the provider's coherent "nothing" —
    `variant_unknown_at_provider`, `ProviderRequestError` (a 400/422) or
    `energy_label_ambiguous` — which looks like a clean empty load otherwise."""

    state: CatalogueDetailState
    loaded_at: dt.datetime | None = None
    note: str | None = None


class CatalogueDetailStatesRead(CamelModel):
    options: CatalogueDetailStateRead
    colours: CatalogueDetailStateRead
    tyre_specs: CatalogueDetailStateRead
    images: CatalogueDetailStateRead
    energy_label: CatalogueDetailStateRead


class CatalogueColourRead(CamelModel):
    colour_code: str
    description: str
    # KAN-75: null while the colour's provider code is an unresolved mapping
    # gap; the configurator offers such a colour in neither group (A2).
    colour_type: str | None
    price: Decimal | None


class CatalogueTyreSpecRead(CamelModel):
    axle: str | None  # KAN-75: null while the axle code is a mapping gap or absent
    size: str
    load_index: str | None
    speed_rating: str | None
    season: str | None
    remark: str | None
    # KAN-596 — additive: the rim the dimension is approved with, as delivered
    # (designation, width and diameter in inches, offset in mm) and whether
    # this is the factory fitment; null where the provider sends none.
    rims: str | None = None
    rim_width: Decimal | None = None
    rim_size: int | None = None
    rim_offset: int | None = None
    is_standard: bool | None = None


class CatalogueImageRead(CamelModel):
    image_key: str
    bild_typ: str
    bild_art: str
    sequence: int
    image_url: str | None


class CatalogueOptionRelationRead(CamelModel):
    from_option_id: uuid.UUID
    to_option_id: uuid.UUID
    to_option_code: str
    to_option_description: str
    relation_type: str
    price_in_combination: Decimal | None


class CatalogueSpecificationRead(CamelModel):
    """WP-6 PR-5 — the entitlement-degraded read surface over PR-4's own
    tenant-scoped catalogue mirror. `packagesAvailable=False` means every
    item in `options` already has `optionGroup=None` (flattened server-
    side, never a client-side filter); `imagesAvailable=False` means
    `images` is always empty and `dealerCanUploadImages` is the one
    marker PR-7's UI renders in its place (no upload endpoint exists
    behind it yet — see the WP-6 plan's own Open Items).
    """

    has_catalogue_match: bool
    has_provider_connection: bool
    packages_available: bool
    images_available: bool
    dealer_can_upload_images: bool
    options: list[CatalogueOptionRead]
    colours: list[CatalogueColourRead]
    tyre_specs: list[CatalogueTyreSpecRead]
    images: list[CatalogueImageRead]
    option_relations: list[CatalogueOptionRelationRead]
    # KAN-211 — additive: per detail, what the last load ended in.
    details: CatalogueDetailStatesRead


class VehicleSearchHit(VehicleMdmRead):
    """The vehicle an identifier resolved to (FR-V-06), plus its plate valid
    today — the "one line of disambiguating context" a global-search row
    carries (FR-UI-08, KAN-82). Only on the targeted hit, never on grid
    rows: the plate table is never listable (ADR-039).

    Reading it is a plate read like the Plates tab (KAN-231): audited, and
    past the caller's limit `current_plate` is None with
    `current_plate_withheld` True, so the screen can say why.
    """

    current_plate: str | None
    current_plate_withheld: bool = False


class VehicleSearchResult(CamelModel):
    """The ONE search box's response shape (FR-V-06/FR-V-16): the string
    either resolves as an identifier (above the grid) or filters it —
    never both, decided server-side from the string's own shape, never by
    which of two fields the user typed into.
    """

    resolved: VehicleSearchHit | None
    picker_candidates: list[VehiclePickerCandidate]
    filtered: VehicleMdmPage
