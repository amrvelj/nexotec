"""The workshop order's API shapes (KAN-810). camelCase JSON; every decimal
in a request is a decimal string (`DecimalStr`, KAN-827)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.aftersales.models.service_order import (
    BillingCategory,
    JobAnswerState,
    LabourTimeUnit,
    PositionKind,
    PositionState,
    RateSource,
    ServiceOrderState,
)
from app.core.money import PriceEntry
from app.core.schemas import CamelModel
from app.core.validators import DecimalStr

# --- requests ---------------------------------------------------------------------------


class ServiceOrderCreate(CamelModel):
    """D2 «Neuer Auftrag». The invoice recipient defaults to the contact."""

    location_id: uuid.UUID
    contact_id: uuid.UUID
    invoice_recipient_contact_id: uuid.UUID | None = None
    driver_contact_id: uuid.UUID | None = None
    # Required by the rule «the car is identified» (ruling 36), which the service states.
    vehicle_id: uuid.UUID | None = None


class ServiceOrderPartiesUpdate(CamelModel):
    """Change the contact, the invoice recipient or the driver (table 3.1).
    A field left out stays; `driverContactId: null` removes the driver."""

    contact_id: uuid.UUID | None = None
    invoice_recipient_contact_id: uuid.UUID | None = None
    driver_contact_id: uuid.UUID | None = None


class ServiceOrderCancel(CamelModel):
    reason: str = Field(max_length=500)


class JobCreate(CamelModel):
    name: str = Field(max_length=200)
    billing_category: BillingCategory = BillingCategory.CUSTOMER


class JobUpdate(CamelModel):
    name: str = Field(max_length=200)


class PositionCreate(CamelModel):
    """D13 «Position»: labour at a fixed price, labour by time, or text.

    Labour names its category. By time, `time` is typed in the Betrieb's unit
    at the order's location (hours or work units); `timeUnit`, when sent, is
    the unit the screen showed, and a different unit in force is refused
    rather than read in the wrong one. A price left out is proposed from the
    Betrieb's labour rates (by time); a typed price leads, net or gross."""

    kind: PositionKind
    text: str = Field(max_length=1000)
    labour_category_id: uuid.UUID | None = None
    time: DecimalStr | None = None
    time_unit: LabourTimeUnit | None = None
    unit_price: DecimalStr | None = None
    price_entered: PriceEntry | None = None


class PositionUpdate(CamelModel):
    """Change a position. A field left out stays; `unitPrice: null` on labour
    by time takes the proposed rate again."""

    text: str | None = Field(default=None, max_length=1000)
    labour_category_id: uuid.UUID | None = None
    time: DecimalStr | None = None
    time_unit: LabourTimeUnit | None = None
    unit_price: DecimalStr | None = None
    price_entered: PriceEntry | None = None


# --- reads ------------------------------------------------------------------------------


class PartyRead(CamelModel):
    contact_id: uuid.UUID
    label: str


class UnitPriceRead(CamelModel):
    """The price as typed (`amount`, `entered`) and both figures as shown:
    the typed one as typed, the other to four decimals (decision 15)."""

    amount: Decimal
    entered: PriceEntry
    vat_rate: Decimal
    net: Decimal
    gross: Decimal


class LabourTimeRead(CamelModel):
    """The time as typed, in the unit it was typed in (check 47: «9 AW»), and in hours."""

    quantity: Decimal
    unit: LabourTimeUnit
    work_units_per_hour: Decimal | None
    hours: Decimal


class PositionRead(CamelModel):
    id: uuid.UUID
    job_id: uuid.UUID
    sort_index: int
    kind: PositionKind
    state: PositionState
    text: str
    labour_category_id: uuid.UUID | None
    labour_category_label: str | None
    time: LabourTimeRead | None
    # «Prüfung nötig» (section 3.6): copied from a catalogue package (KAN-818); read by KAN-815's check.
    check_needed: bool
    unit_price: UnitPriceRead | None
    # The line, net and gross, to the Rappen (ADR-212: both shown in one line).
    line_net: Decimal | None
    line_gross: Decimal | None
    rate_source: RateSource | None
    labour_rate_id: uuid.UUID | None
    rate_valid_from: dt.date | None
    priced_at: dt.datetime | None
    priced_by: uuid.UUID | None


class JobRead(CamelModel):
    id: uuid.UUID
    sort_index: int
    name: str
    billing_category: BillingCategory
    answer_state: JobAnswerState
    answered_at: dt.datetime | None
    answered_by: uuid.UUID | None
    positions: list[PositionRead]


class VatLineRead(CamelModel):
    rate: Decimal
    net: Decimal
    vat: Decimal


class PayerTotalRead(CamelModel):
    """One payer's total: the net sum, one VAT line per rate, the total, the
    cash rounding as its own line («Rundung») and the amount due."""

    payer: BillingCategory
    net: Decimal
    vat_lines: list[VatLineRead]
    vat: Decimal
    total: Decimal
    rounding: Decimal
    rounded_total: Decimal


class OrderTotalsRead(CamelModel):
    """Exit criterion 4: accepted, and still open (draft or waiting for the answer)."""

    accepted: list[PayerTotalRead]
    open: list[PayerTotalRead]


class CancellationRead(CamelModel):
    """Check 10: who cancelled, when and why."""

    cancelled_at: dt.datetime
    cancelled_by: uuid.UUID | None
    cancelled_by_label: str | None
    reason: str


class ServiceOrderSummary(CamelModel):
    """A row of the order list (D1)."""

    id: uuid.UUID
    version: int
    order_number: str
    state: ServiceOrderState
    order_date: dt.date
    location_id: uuid.UUID
    location_label: str
    contact: PartyRead
    vehicle_id: uuid.UUID
    vehicle_label: str
    waiting_for_answer: bool
    created_at: dt.datetime


class ServiceOrderRead(ServiceOrderSummary):
    invoice_recipient: PartyRead
    driver: PartyRead | None
    jobs: list[JobRead]
    totals: OrderTotalsRead
    cancellation: CancellationRead | None
    updated_at: dt.datetime


class ServiceOrderPage(CamelModel):
    items: list[ServiceOrderSummary]
    next_cursor: str | None
    total: int
    total_is_estimate: bool


class LabourRateProposalRead(CamelModel):
    """«Which rate applies here» for a labour category on this order (check 11):
    the invoice recipient's agreement first, else the category rate at the
    order's location on the order's day. `found: false` when there is none."""

    labour_category_id: uuid.UUID
    found: bool
    source: RateSource | None
    labour_rate_id: uuid.UUID | None
    valid_from: dt.date | None
    unit_price: UnitPriceRead | None
    time_unit: LabourTimeUnit
    work_units_per_hour: Decimal | None


class HistoryEntryRead(CamelModel):
    """One entry of the order's history: the platform audit trail."""

    id: uuid.UUID
    action: str
    actor_id: uuid.UUID | None
    actor_label: str | None
    before: dict | None
    after: dict | None
    reason: str | None
    created_at: dt.datetime


class HistoryRead(CamelModel):
    items: list[HistoryEntryRead]
