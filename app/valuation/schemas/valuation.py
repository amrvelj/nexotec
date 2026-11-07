"""Valuation schemas (WP-8 PR-5)."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import ConfigDict, Field, field_validator

from app.core.schemas import CamelModel
from app.core.validators import NormalisedVinOrChassisNumber
from app.valuation.models.valuation import ValuationSource

# Q-11 (default validity) — confirmed live on the reference prototype's
# own "Bewertung erstellen" dialog: "GÜLTIG FÜR (TAGE)" defaults to 30.
# Product-confirmed via the UI, not independently re-derived by
# engineering — flagged per the build brief's own instruction.
DEFAULT_VALIDITY_DAYS = 30


class DeductionInput(CamelModel):
    label: str
    amount: Decimal


class ValuationCreate(CamelModel):
    """KAN-574 (Anto, 2026-11-07, option A): a valuation names its car. `vin`
    (or a pre-1981 chassis number), `vehicleMake` and `vehicleModel` are
    required — taken from the configuration when `configurationId` is sent
    (which must then carry a VIN), typed otherwise; a missing one is 422
    `valuation_names_no_car` with the missing `fields`. The fields stay
    optional in the schema because a configuration supplies them."""

    # Vehicle — the car need not be in the register (confirmed live), but it
    # is named (KAN-574, above). Providing `vin` resolves
    # or creates the real vehicle-mdm record in the same step (FR-V's own
    # "one step, not two"). Normalised and validated as Stock's VIN is
    # (KAN-331, FR-V-15): one spelling, one vehicle-mdm record. KAN-353: for a
    # car first registered before 1981, a chassis number (1-17 letters and
    # digits) instead — never a vehicle-mdm record (the service decides).
    vin: NormalisedVinOrChassisNumber | None = None
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    vehicle_trim: str | None = None
    vehicle_plate: str | None = None
    vehicle_first_registration: dt.date | None = None
    mileage: int | None = None
    # C-F (KAN-10, FR-C-14): the `record`-mode configuration the car was
    # captured in. With it, the vehicle fields above default from the
    # configuration and **no vehicle-mdm record is created** (ADR-070,
    # amending FR-V-17); without it, the pre-C-F behaviour is unchanged.
    configuration_id: uuid.UUID | None = None

    customer_id: uuid.UUID | None = None

    source: ValuationSource
    provider_value: Decimal | None = None
    final_offer: Decimal
    deductions: list[DeductionInput] = Field(default_factory=list)
    note: str | None = None

    valid_for_days: int = DEFAULT_VALIDITY_DAYS
    is_draft: bool = False

    # Set when this valuation replaces an existing one ("Neu bewerten").
    supersedes_valuation_id: uuid.UUID | None = None


class DeductionRead(CamelModel):
    label: str
    amount: Decimal


class ValuationRead(CamelModel):
    id: uuid.UUID
    valuation_number: str
    vehicle_id: uuid.UUID | None
    vehicle_make: str | None
    vehicle_model: str | None
    vehicle_trim: str | None
    vehicle_plate: str | None
    vehicle_vin: str | None
    vehicle_first_registration: dt.date | None
    mileage: int | None
    configuration_id: uuid.UUID | None = None
    configuration_label: str | None = None
    customer_id: uuid.UUID | None
    customer_label: str | None
    source: ValuationSource
    provider_value: Decimal | None
    final_offer: Decimal
    deductions: list[DeductionRead] = Field(default_factory=list)
    note: str | None
    valid_from: dt.date
    valid_until: dt.datetime
    is_draft: bool
    used_at: dt.datetime | None
    supersedes_valuation_id: uuid.UUID | None
    # Derived on read (services/valuation.py::derive_status), never stored:
    # "draft" | "valid" | "expired" | "used".
    status: str
    # KAN-115 — whether a signed contract of this dealership carries it as
    # its trade-in (read from Sales on every read, never stored). Only then
    # may it be marked used by hand: only a signed deal uses a valuation.
    has_signed_contract: bool
    version: int
    created_by: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime


class ValuationPage(CamelModel):
    items: list[ValuationRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool


REVERT_USE_REASON_MAX_LENGTH = 500


class ValuationRevertUse(CamelModel):
    """«Verwendung aufheben» (KAN-253): the reason is mandatory and is kept
    on the audit row."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=REVERT_USE_REASON_MAX_LENGTH)

    @field_validator("reason")
    @classmethod
    def _reason_stated(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Clearing a valuation's use needs a stated reason.")
        return value.strip()
