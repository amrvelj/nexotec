"""Vehicle: tenant-agnostic global identity record for a specific physical
vehicle (by VIN) — static specs and title/custody chain. Sales-floor stock
state (price, lot location) lives in a future Inventory module.

No tenant_id (spec cross-cutting #9 + §2 IDs & tenant ownership: a VIN is
decoded manufacturer data, not owned by a dealer). Custody is tracked
separately via the append-only VehicleCustodyEvent log; Vehicle.
current_custodian_partner_id is a denormalized "who holds it now" cache,
only ever written when a custody event is recorded (see
services/vehicle.py), never accepted directly from a create/update payload.

`vehicle_type` here means the Swiss addendum's reference-data taxonomy
(passenger/commercial/etc., seeded in issue #3) — not the original US spec
table's `[new, used, certified_pre_owned, demo]` condition enum. The
addendum silently repurposed the field name without saying so explicitly;
issue #5's field list carries no `new/used/CPO/demo` condition concept at
all, so it's dropped rather than guessed back in. Flagged in the PR.
"""

import datetime as dt
import enum

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin, utcnow
from app.core.types import GUID, UTCDateTime


class VehicleCondition(str, enum.Enum):
    """Hardcoded, not reference-data — dealers won't need to add categories
    here (CTO ruling, 2026-08-06). Was in the original spec draft
    (`condition new|used|certified_pre_owned|unknown`) but dropped when it
    got condensed into the issue #5 backlog text; restored here.
    """

    NEW = "new"
    USED = "used"
    CERTIFIED_PRE_OWNED = "certified_pre_owned"
    DEMO = "demo"


class RegistrationStatus(str, enum.Enum):
    UNREGISTERED = "unregistered"
    REGISTERED = "registered"
    DEREGISTERED = "deregistered"
    EXPORT = "export"


class VehicleStatus(str, enum.Enum):
    IN_TRANSIT = "in_transit"
    IN_STOCK = "in_stock"
    SOLD = "sold"
    IN_SERVICE = "in_service"
    TOTALED = "totaled"
    SCRAPPED = "scrapped"


class CustodyEventType(str, enum.Enum):
    ACQUIRED = "acquired"
    TRANSFERRED = "transferred"
    SOLD = "sold"
    REPOSSESSED = "repossessed"


class Vehicle(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "vehicle"

    vin: Mapped[str] = mapped_column(String(17), nullable=False, unique=True, index=True)
    make: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    model_year: Mapped[int] = mapped_column(Integer, nullable=False)
    trim: Mapped[str | None] = mapped_column(String(100), nullable=True)
    engine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    condition: Mapped[VehicleCondition] = mapped_column(
        SAEnum(VehicleCondition, native_enum=False, length=32), nullable=False
    )

    # Reference-data value_codes (issue #3) — validated against the matching
    # ReferenceList at the service layer, stored here as plain strings (not
    # a DB enum/FK) per the reference-data pattern's own design.
    vehicle_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    drivetrain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transmission: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exterior_color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interior_color: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # AUTO-i-DAT decode fields (Swiss addendum decision #2 + #10) — manual
    # entry fallback for now, same fields either way (no integration built).
    energy_efficiency_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    co2_emissions_gkm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Plain integer, no rollback detection (Swiss addendum decision #14 —
    # descoped entirely, not even a passive log).
    odometer: Mapped[int | None] = mapped_column(Integer, nullable=True)

    registration_status: Mapped[RegistrationStatus] = mapped_column(
        SAEnum(RegistrationStatus, native_enum=False, length=32),
        nullable=False,
        default=RegistrationStatus.UNREGISTERED,
    )
    registration_canton: Mapped[str | None] = mapped_column(String(2), nullable=True)

    status: Mapped[VehicleStatus] = mapped_column(
        SAEnum(VehicleStatus, native_enum=False, length=32), nullable=False, default=VehicleStatus.IN_TRANSIT
    )
    current_custodian_partner_id: Mapped[GUID | None] = mapped_column(
        GUID(), ForeignKey("dealer.id"), nullable=True
    )


class VehicleCustodyEvent(PrimaryKeyMixin, Base):
    """Append-only custody chain — rows are never updated or deleted after
    insert, same convention as AuditEvent.
    """

    __tablename__ = "vehicle_custody_event"
    __table_args__ = (
        Index("ix_vehicle_custody_event_vehicle_id_partner_id", "vehicle_id", "partner_id"),
    )

    vehicle_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("vehicle.id"), nullable=False, index=True)
    partner_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("dealer.id"), nullable=False, index=True)
    event_type: Mapped[CustodyEventType] = mapped_column(
        SAEnum(CustodyEventType, native_enum=False, length=32), nullable=False
    )
    event_date: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    # FK constrained now that Transaction (issue #6) is in this branch's
    # own history — was a bare-UUID forward reference before #6 existed.
    transaction_id: Mapped[GUID | None] = mapped_column(GUID(), ForeignKey("transaction.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    created_by: Mapped[GUID | None] = mapped_column(GUID(), nullable=True)
