"""Odometer, accessories, custody (WP-5 PR-5) — the three append-only logs
that make up a vehicle's history against VehicleMdm (PR-3). All three are
global reads, tenant-attributed writes, same data class as the shipped
table's own custody event — a mileage reading or an accessory fitting is a
fact about the car, but who recorded it is a fact about the dealer.

VehicleCustodyEvent here is a NEW table pointed at vehicle_mdm.id, not an
edit to the shipped app.vehicle.models.vehicle.VehicleCustodyEvent (which
points at the old, soon-to-retire vehicle.id and stays untouched until
PR-7's cutover carries its rows across).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TimestampMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.vehicle.models.vehicle_mdm import VehicleMdm


class OdometerSource(str, enum.Enum):
    SERVICE_VISIT = "service_visit"
    SALE = "sale"
    VALUATION = "valuation"
    MANUAL = "manual"
    IMPORT = "import"


class VehicleOdometerReading(PrimaryKeyMixin, TimestampMixin, Base):
    """Append-only. Current mileage is the most recent row BY reading_date,
    computed at read time (app.vehicle.services.odometer.current_mileage) —
    never cached on VehicleMdm, so there is nowhere for it to go stale.

    A reading lower than an earlier one is accepted and flagged
    implausible, never rejected — the amended FR-V-07 rule: only a human
    can tell which of the two readings is wrong, and hiding either one
    destroys the evidence at exactly the moment (a rolled-back odometer)
    the stakes are highest. implausible is computed once, at insert time,
    against whatever was current then, and stored — not recomputed on
    every read, so it doesn't silently change if a later reading arrives.
    """

    __tablename__ = "vehicle_odometer_reading"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_mdm.id"), nullable=False, index=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    reading_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    source: Mapped[OdometerSource] = mapped_column(SAEnum(OdometerSource, native_enum=False, length=16), nullable=False)
    implausible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recording_tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Dealership). No DB-level FK — reconciled nightly."
    )

    vehicle: Mapped[VehicleMdm] = relationship()


class VehicleAccessory(PrimaryKeyMixin, TimestampMixin, Base):
    """Retrofitted, not a factory option (app.vehicle.models.catalogue.
    VariantOption) — a towbar fitted in 2024 and removed in 2026 produces a
    history, not a false standing claim. Append-only: removing one sets
    valid_to, the row is never deleted. accessory_type is a
    reference_value.value_code (PR-1's accessory_type list); description
    is free text alongside it, same shape as the PRD's own FR-V-13.
    """

    __tablename__ = "vehicle_accessory"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_mdm.id"), nullable=False, index=True)
    accessory_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    recording_tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Dealership). No DB-level FK — reconciled nightly."
    )

    vehicle: Mapped[VehicleMdm] = relationship()


class CustodyEventType(str, enum.Enum):
    ACQUIRED = "acquired"
    TRANSFERRED = "transferred"
    SOLD = "sold"
    REPOSSESSED = "repossessed"


class VehicleCustodyEvent(PrimaryKeyMixin, Base):
    """Append-only custody chain against VehicleMdm — rows are never
    updated or deleted after insert, same convention as AuditEvent and as
    the shipped table's own custody log (app.vehicle.models.vehicle),
    which this one supersedes for new vehicles going forward.
    """

    __tablename__ = "vehicle_mdm_custody_event"

    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("vehicle_mdm.id"), nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True,
        comment="Owned by the platform context (Dealership). No DB-level FK — reconciled nightly.",
    )
    event_type: Mapped[CustodyEventType] = mapped_column(SAEnum(CustodyEventType, native_enum=False, length=16), nullable=False)
    event_date: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True,
        comment="Owned by the sales context (Transaction). No DB-level FK — reconciled nightly.",
    )
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
