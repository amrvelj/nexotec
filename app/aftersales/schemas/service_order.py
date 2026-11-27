"""The workshop order's API shapes (KAN-810). camelCase JSON; every decimal
in a request is a decimal string (`DecimalStr`, KAN-827)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.aftersales.models.service_order import (
    AnswerChannel,
    AnswerResult,
    BillingCategory,
    JobAnswerState,
    LabourTimeUnit,
    PositionKind,
    PositionState,
    RateSource,
    ServiceOrderState,
)
from app.aftersales.models.service_stay import ArrivalChannel, WorkshopStatus
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
    # KAN-811, check 6: confirmed while somebody is clocked on an accepted job.
    confirm_while_clocked: bool = False


class PositionUpdate(CamelModel):
    """Change a position. A field left out stays; `unitPrice: null` on labour
    by time takes the proposed rate again."""

    text: str | None = Field(default=None, max_length=1000)
    labour_category_id: uuid.UUID | None = None
    time: DecimalStr | None = None
    time_unit: LabourTimeUnit | None = None
    unit_price: DecimalStr | None = None
    price_entered: PriceEntry | None = None
    # KAN-811, check 6: confirmed while somebody is clocked on an accepted job.
    confirm_while_clocked: bool = False


class JobAnswerEntry(CamelModel):
    """One job's answer in D8. `note` is required for «declined»; `hoursBilled` is
    asked only when an accepted job with clocked time is declined (ruling 48)."""

    job_id: uuid.UUID
    result: AnswerResult
    note: str | None = Field(default=None, max_length=1000)
    hours_billed: bool | None = None


class ServiceOrderAnswersCreate(CamelModel):
    """D8 «Antwort erfassen» (KAN-811): how the answer came, and one entry per job answered."""

    channel: AnswerChannel
    jobs: list[JobAnswerEntry] = Field(max_length=200)


class ArrivalCreate(CamelModel):
    """D14 «Fahrzeug ist da» (KAN-813): the mileage (required, a whole number from 0),
    the key tag, the parking place and a remark."""

    mileage: int
    key_tag: str | None = Field(default=None, max_length=50)
    parking_place: str | None = Field(default=None, max_length=100)
    remark: str | None = Field(default=None, max_length=1000)


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
    # Who entered the price (section 3.2, check 3): the user's name, or null when unknown.
    priced_by_label: str | None = None


class AmountRead(CamelModel):
    """A job's amount, net and incl. VAT, to the Rappen."""

    net: Decimal
    gross: Decimal


class JobAnswerRead(CamelModel):
    """One recorded answer (KAN-811): who, how and when, and the amount said yes to
    (null when the job had no price yet, ruling 167)."""

    id: uuid.UUID
    result: AnswerResult
    channel: AnswerChannel
    note: str | None
    amount: AmountRead | None
    hours_billed: bool | None
    recorded_at: dt.datetime
    recorded_by: uuid.UUID | None
    recorded_by_label: str | None


class JobRead(CamelModel):
    id: uuid.UUID
    sort_index: int
    name: str
    billing_category: BillingCategory
    answer_state: JobAnswerState
    answered_at: dt.datetime | None
    answered_by: uuid.UUID | None
    positions: list[PositionRead]
    # KAN-811. The job's amount now (null while nothing on it has a price).
    amount: AmountRead | None = None
    # «Accepted before: CHF x» (ruling 30): set while the job waits again after its amount changed.
    accepted_before: AmountRead | None = None
    # Every answer of the job, oldest first.
    answers: list[JobAnswerRead] = Field(default_factory=list)
    # «Stopped» (ruling 48): the customer stopped the job after work started; `hoursBilled` is the choice.
    stopped: bool = False
    hours_billed: bool | None = None
    # Time was clocked on the job: declining it asks «hours billed or not» (check 8).
    time_clocked: bool = False
    # «Arbeit ohne Antwort» (ruling 49): waits for the answer while somebody is clocked on it.
    work_without_answer: bool = False


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
    # KAN-813, criterion 5: worked out, read from its projection; no endpoint sets it.
    workshop_status: WorkshopStatus = WorkshopStatus.PLANNED


class StayOrderRead(CamelModel):
    id: uuid.UUID
    order_number: str


class StayRead(CamelModel):
    """The stay of the car the order belongs to (KAN-813): the reception tab's arrival —
    who, when, how — and what was taken in."""

    id: uuid.UUID
    arrived_at: dt.datetime
    arrived_by: uuid.UUID | None
    arrived_by_label: str | None
    arrival_channel: ArrivalChannel
    arrival_mileage: int
    # The car's last reading at the arrival; a lower mileage is taken and flagged (check 22).
    previous_mileage: int | None
    mileage_below_previous: bool
    key_tag: str | None
    parking_place: str | None
    remark: str | None
    departed_at: dt.datetime | None
    # Every order the stay holds (check 23: a second order joins it).
    orders: list[StayOrderRead]


class ServiceOrderRead(ServiceOrderSummary):
    invoice_recipient: PartyRead
    driver: PartyRead | None
    jobs: list[JobRead]
    totals: OrderTotalsRead
    cancellation: CancellationRead | None
    # «Arbeit ohne Antwort» on the order (section 3.9): a job of it has work without an answer.
    work_without_answer: bool = False
    # KAN-813: the car's stay (running, else the last), and the car's mileage now (vehicle's).
    stay: StayRead | None = None
    vehicle_mileage: int | None = None
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
