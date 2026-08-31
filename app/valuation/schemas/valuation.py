"""Valuation schemas (WP-8 PR-5)."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
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
    # Vehicle — all optional; a valuation may be created with no vehicle
    # in the register at all (confirmed live). Providing `vin` resolves
    # or creates the real vehicle-mdm record in the same step (FR-V's own
    # "one step, not two").
    vin: str | None = None
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    vehicle_trim: str | None = None
    vehicle_plate: str | None = None
    vehicle_first_registration: dt.date | None = None
    mileage: int | None = None

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
    version: int
    created_by: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime


class ValuationPage(CamelModel):
    items: list[ValuationRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool
