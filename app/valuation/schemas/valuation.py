"""Valuation schemas (WP-8 PR-5)."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import ConfigDict, Field, field_validator

from app.core.schemas import CamelModel
from app.core.validators import NormalisedVinOrChassisNumber
from app.valuation.models.valuation import ResidualTableState, ValuationSource

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
    required — each defaulted from the configuration when `configurationId`
    is sent (a field sent wins), typed otherwise (a VIN typed beside a
    configuration that has none is stored, KAN-449 option A — open with Anto on
    KAN-574); a missing one is 422
    `valuation_names_no_car` with the missing `fields`. The fields stay
    optional in the schema because a configuration supplies them."""

    # Vehicle — the car need not be in the register (confirmed live), but it
    # is named (KAN-574, above). A `vin` links the vehicle-mdm record already
    # held for it and never creates one (KAN-189). Normalised and validated as Stock's VIN is
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

    # KAN-552: the provider figure comes from a stored quote, never typed.
    # `source` is derived: `auto_i_dat` when `providerQuoteId` is sent and
    # `providerValue` is absent or the quote's trade-in price; `manual`
    # otherwise (an advisor's own figure, the quote kept beside it). A sent
    # `source` is no longer needed; `auto_i_dat` without a quote is 422
    # `provider_quote_required`.
    source: ValuationSource | None = Field(
        default=None,
        # Marked in the OpenAPI document only: the service still reads it to
        # refuse `auto_i_dat` without a quote, and pydantic's own
        # `deprecated=` would warn on that read.
        json_schema_extra={"deprecated": True},
        description=(
            "Deprecated (KAN-552): derived from `providerQuoteId` and `providerValue`; a sent value is not stored. "
            "`auto_i_dat` without `providerQuoteId` is refused (422 provider_quote_required)."
        ),
    )
    provider_quote_id: uuid.UUID | None = None
    provider_value: Decimal | None = None
    final_offer: Decimal
    deductions: list[DeductionInput] = Field(default_factory=list)
    note: str | None = None

    valid_for_days: int = DEFAULT_VALIDITY_DAYS
    is_draft: bool = False

    # Set when this valuation replaces an existing one ("Neu bewerten").
    supersedes_valuation_id: uuid.UUID | None = None


class ProviderQuoteRequest(CamelModel):
    """KAN-552 — «Bewertung von auto-i-dat holen»: one valuation and one
    residual table for the configured car. The first registration and the
    mileage default from the configuration; sent, they win (what the dialog
    shows is what is valued)."""

    model_config = ConfigDict(extra="forbid")

    configuration_id: uuid.UUID
    first_registration: dt.date | None = None
    mileage: int | None = Field(default=None, ge=0, le=2_000_000)


class ProviderMessageRead(CamelModel):
    de: str | None
    fr: str | None
    it: str | None
    en: str | None


class ResidualValueRead(CamelModel):
    duration_months: int
    km_per_year: int
    total_percent: Decimal | None
    total_price: Decimal | None


class ProviderQuoteRead(CamelModel):
    id: uuid.UUID
    configuration_id: uuid.UUID
    fz_key: str
    model_year: int
    first_registration: dt.date
    mileage: int
    # «Wert auto-i-dat» — the trade-in (exchange) price, vehicle and options.
    exchange_price: Decimal
    purchase_price: Decimal | None
    sale_price: Decimal | None
    vehicle_exchange_price: Decimal | None
    options_exchange_price: Decimal | None
    kilometre_correction_exchange: Decimal | None
    kilometre_expected: int | None
    exchange_range_from: Decimal | None
    exchange_range_to: Decimal | None
    provider_manual: bool
    screen_messages: list[ProviderMessageRead]
    residual_table_state: ResidualTableState
    residual_table: list[ResidualValueRead]
    provider_request_ref: uuid.UUID
    fetched_at: dt.datetime


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
    # KAN-552: the quote the provider figure came from — with `manual`, the
    # advisor's override, shown beside it.
    provider_quote: ProviderQuoteRead | None = None
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
