"""The workshop order (KAN-810, AS1-07; Aftersales build slice 1, sections
3.1 to 3.3; ADR-212, ADR-215).

Three tables, all Aftersales' own:

- `service_order` — the order for a customer's car: its number from the
  Betrieb's `service_order` range (ADR-109), its state (table 3.1), the
  location and day it is priced at, and the people and the car it is for.
  The contact (the customer), the invoice recipient and the driver are
  contacts, which `contact` owns (group-scoped, ADR-014); the car is a
  vehicle-mdm record, which `vehicle` owns; the location is platform's. Each
  is the three-column pattern (rule 2): a plain GUID with its owner, a label
  and when the label was read — never a foreign key.
- `service_order_job` — a job on the order: a name, the billing category
  (one value in this slice, section 10: a field from the first day, later
  slices add values) and the customer's answer (table 3.2; recorded with
  KAN-811).
- `service_order_position` — what a job holds (table 3.3): labour at a
  fixed price, labour by time, or text.

**The order is the aggregate** (KAN-810): it alone carries a `version`.
Every change to the order, a job or a position moves it and takes
`If-Match` with it, so two advisors editing one order never overwrite each
other silently. Jobs and positions are unversioned child rows.

**A price is kept as typed** (decision 15, ADR-215): `unit_price` with
`price_entered` (net or gross) and the VAT rate it was typed under; the
other figure follows from `app.core.money.Price`, never stored. A workshop
order may show net and gross (ADR-212); there is no `vatTreatment`.

**Labour time** (ruling 183): typed in the Betrieb's unit at the order's
location — hours, or work units with the work units per hour in force — and
kept with that unit and that number, so a later change of the setting
changes no position (check 48). The amount is always hours × the hourly
rate (`app.core.money.labour_hours`).

**The rate keeps its source** (check 11): a labour price proposed from the
Betrieb's labour rates names whether it came from an agreement with the
invoice recipient or from the category rate, and the platform row it came
from; a typed price says so.

No workshop status, reception field or clock lives here (ADR-213): those
are the stay's and the clock's, worked out on read (section 3.9).
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, false, select
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.aftersales.models.service_stay import ServiceOrderWorkshopStatus
from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.money import PriceEntry
from app.core.text_collation import IDENTIFIER_TEXT
from app.core.types import GUID, UTCDateTime
from app.db import Base

# A unit price carries up to four decimals (decision 23); a VAT rate is a percentage.
UNIT_PRICE_TYPE = Numeric(12, 4)
VAT_RATE_TYPE = Numeric(5, 2)
# Labour time is typed with two decimals, in hours or in work units (ruling 183).
TIME_TYPE = Numeric(9, 2)
WORK_UNITS_PER_HOUR_TYPE = Numeric(7, 2)

LABEL_LENGTH = 200


class ServiceOrderState(str, enum.Enum):
    """Table 3.1. «Offered», «lost» and «closed» exist in Part A and are not
    reached in this slice; they join with the slices that reach them."""

    DRAFT = "draft"
    ACCEPTED = "accepted"
    READY_FOR_BILLING = "ready_for_billing"
    CANCELLED = "cancelled"


class JobAnswerState(str, enum.Enum):
    """Table 3.2: the customer's answer on a job. Recorded with KAN-811;
    «stopped» is a mark an accepted or declined job carries (KAN-811)."""

    DRAFT = "draft"
    WAITING = "waiting"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class AnswerResult(str, enum.Enum):
    """What the customer said to a job (table 3.2; KAN-811)."""

    ACCEPTED = "accepted"
    DECLINED = "declined"


class AnswerChannel(str, enum.Enum):
    """How the answer came (table 3.2): at the desk, by phone, by a message."""

    DESK = "desk"
    PHONE = "phone"
    MESSAGE = "message"


class BillingCategory(str, enum.Enum):
    """Who pays a job (section 10). One value in this slice; warranty,
    goodwill, free service and internal come with the slice «payers»."""

    CUSTOMER = "customer"


class PositionKind(str, enum.Enum):
    LABOUR_FIXED = "labour_fixed"
    LABOUR_TIME = "labour_time"
    TEXT = "text"


class PositionState(str, enum.Enum):
    """Table 3.3. «Done» comes with the technician's clock (KAN-814)."""

    OPEN = "open"


class LabourTimeUnit(str, enum.Enum):
    """The unit a labour time was typed in (ruling 183), kept per position."""

    HOURS = "hours"
    WORK_UNITS = "work_units"


class RateSource(str, enum.Enum):
    """Where a labour position's price came from (check 11)."""

    # A fixed rate agreed with the invoice recipient (platform's labour_rate_agreement).
    AGREEMENT = "agreement"
    # The category's rate at the order's location on the order's day (platform's labour_rate).
    CATEGORY_RATE = "category_rate"
    # Typed by the advisor.
    TYPED = "typed"


class ServiceOrder(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "service_order"
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_number", name="uq_service_order_tenant_id_order_number"),
        Index("ix_service_order_tenant_id_created_at", "tenant_id", "created_at"),
    )

    order_number: Mapped[str] = mapped_column(String(16), nullable=False, info=IDENTIFIER_TEXT)
    state: Mapped[ServiceOrderState] = mapped_column(
        StoredEnum(ServiceOrderState, length=24), nullable=False, default=ServiceOrderState.DRAFT
    )

    # The location the order is priced at, and the day (Swiss time) it was opened: the labour
    # rate is the one of the order's location on the order's day (check 11).
    location_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Location.id). No DB-level FK."
    )
    location_label: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    location_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    order_date: Mapped[dt.date] = mapped_column(Date(), nullable=False)

    # The customer: the contact the order is for.
    contact_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the contact context (group-scoped). No DB-level FK."
    )
    contact_label: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    contact_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)

    invoice_recipient_contact_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the contact context (group-scoped). No DB-level FK."
    )
    invoice_recipient_label: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    invoice_recipient_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)

    driver_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the contact context (group-scoped). No DB-level FK."
    )
    driver_label: Mapped[str | None] = mapped_column(String(LABEL_LENGTH), nullable=True)
    driver_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK."
    )
    vehicle_label: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    vehicle_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)

    # Cancel, with a reason (table 3.1; check 10: who, when and why).
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


# KAN-813, criterion 5: the order's workshop status as the list reads, sorts and filters it — a
# read-only expression over its projection (`service_order_workshop_status`, one row per order,
# written by `services/workshop_status.refresh` alone). Not a column of the order: nothing can set
# it through the order (ADR-213: «never a status column on the order»).
ServiceOrder.workshop_status = column_property(  # type: ignore[attr-defined]
    select(ServiceOrderWorkshopStatus.status)
    .where(ServiceOrderWorkshopStatus.service_order_id == ServiceOrder.id)
    .correlate_except(ServiceOrderWorkshopStatus)
    .scalar_subquery()
    .label("workshop_status"),
    deferred=False,
)


class ServiceOrderJob(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "service_order_job"

    service_order_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("service_order.id"), nullable=False, index=True
    )
    sort_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    billing_category: Mapped[BillingCategory] = mapped_column(
        StoredEnum(BillingCategory, length=16), nullable=False, default=BillingCategory.CUSTOMER
    )
    answer_state: Mapped[JobAnswerState] = mapped_column(
        StoredEnum(JobAnswerState, length=16), nullable=False, default=JobAnswerState.DRAFT
    )
    answered_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    answered_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    # «Stopped» (table 3.2, ruling 48, decision 54; KAN-811): the customer stopped an
    # accepted job after work started. `hours_billed` is the advisor's choice: true keeps
    # the job accepted with what was done, false declines it (the hours stay a cost).
    # A stopped job is never changed, answered or clocked again.
    stopped_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    stopped_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    hours_billed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class ServiceOrderJobAnswer(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One answer of the customer to a job (KAN-811; table 3.2, checks 3 to 5).

    Its own record, never changed: every answer of a job is kept while the job
    exists (deleting a job deletes its answers; the order's history keeps them), so «accepted before: CHF x»
    (ruling 30) and who answered how and when are read from here, never typed.
    `amount_net`/`amount_gross` are the job's amount the customer said yes to, to the
    Rappen — null when the job had no price yet (ruling 167): such a job never waits
    again for a price entered later (ruling 172). The job's `answer_state` is the
    current state; «wartet auf Antwort» and «Arbeit ohne Antwort» are worked out on
    read (section 3.9)."""

    __tablename__ = "service_order_job_answer"

    service_order_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("service_order.id"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("service_order_job.id"), nullable=False, index=True)
    result: Mapped[AnswerResult] = mapped_column(StoredEnum(AnswerResult, length=16), nullable=False)
    channel: Mapped[AnswerChannel] = mapped_column(StoredEnum(AnswerChannel, length=16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    amount_net: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_gross: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Set when the answer stopped an accepted job with clocked time (ruling 48).
    hours_billed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


class ServiceOrderPosition(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "service_order_position"

    service_order_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("service_order.id"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("service_order_job.id"), nullable=False, index=True)
    sort_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[PositionKind] = mapped_column(StoredEnum(PositionKind, length=16), nullable=False)
    state: Mapped[PositionState] = mapped_column(
        StoredEnum(PositionState, length=16), nullable=False, default=PositionState.OPEN
    )
    text: Mapped[str] = mapped_column(String(1000), nullable=False)

    # Labour only: the category it is priced in (platform's master data, D-A-19).
    labour_category_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the platform context (LabourRateCategory.id). No DB-level FK."
    )
    labour_category_label: Mapped[str | None] = mapped_column(String(LABEL_LENGTH), nullable=True)
    labour_category_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Labour time as typed, with the unit and the work units per hour it was typed with
    # (ruling 183, check 48). Required by time, optional at a fixed price.
    time_quantity: Mapped[Decimal | None] = mapped_column(TIME_TYPE, nullable=True)
    time_unit: Mapped[LabourTimeUnit | None] = mapped_column(StoredEnum(LabourTimeUnit, length=16), nullable=True)
    work_units_per_hour: Mapped[Decimal | None] = mapped_column(WORK_UNITS_PER_HOUR_TYPE, nullable=True)

    # The price as typed (decision 15): per hour by time, the whole price at a fixed price.
    unit_price: Mapped[Decimal | None] = mapped_column(UNIT_PRICE_TYPE, nullable=True)
    price_entered: Mapped[PriceEntry | None] = mapped_column(StoredEnum(PriceEntry, length=8), nullable=True)
    vat_rate: Mapped[Decimal | None] = mapped_column(VAT_RATE_TYPE, nullable=True)
    # Where the price came from (check 11) and the platform row behind it.
    rate_source: Mapped[RateSource | None] = mapped_column(StoredEnum(RateSource, length=16), nullable=True)
    labour_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        comment="Owned by the platform context (LabourRate.id or LabourRateAgreement.id, per rate_source). "
        "No DB-level FK; a rate row is never deleted (ruling 178).",
    )
    rate_valid_from: Mapped[dt.date | None] = mapped_column(Date(), nullable=True)
    # Who set the price, and when (section 3.2: the order says who entered each price).
    priced_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    priced_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    # «Prüfung nötig» on a labour position (section 3.6): copied from a service-catalogue
    # package (KAN-818, check 44); the quality check that reads it is KAN-815.
    check_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
