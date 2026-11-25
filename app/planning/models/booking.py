"""Bookings (KAN-808 PR-1c; ADR-221; slice 1, section 3.8): a resource held
for a time.

- **Kinds.** A *work* booking holds exactly one workplace and zero or more
  people for one job on one car. A *drop-off* or *pick-up* slot holds one or
  more resources of any kind for a reference (the order). A *block* holds one
  or more resources with an optional note (at most 200 characters) and no
  reference (Anto, 2026-11-25).
- **Time** is a half-open interval `[starts_at, ends_at)` of instants (UTC);
  two bookings that only touch do not overlap.
- **The caller's references** are other contexts' ids, each with the label
  the caller passed and when it was copied (the three-column pattern, rule 2):
  the reference R (`reference_type` names the owning context and entity,
  e.g. `aftersales.order`), the job, the car (vehicle context).
- **«Neu einplanen».** A booking a later change left in the wrong place is
  marked with a cause: a block over it (`blocked`, the block named), work left
  outside its drop-off or pick-up slot when a slot was booked or moved
  (`outside_drop_off_pick_up`, the slot named), or one of its resources
  deactivated (`resource_deactivated`) or moved to another location
  (`resource_moved`), the resource named. A move clears the mark; a block is
  never marked.
- **Released**, never deleted: a released booking holds nothing and is kept.

`planning_booking_resource` holds a booking's resources. `planning_overruled_warning`
is the record of a warning the booker overruled (check 18; ADR-221 (6)):
the code, the resource, the time booked, who (the name frozen then, never
refreshed) and when. It is append-only; the audit log holds the second copy.

Every table carries `tenant_id` (T7); planning's own references are composite
foreign keys with it, so a booking never names another Betrieb's resource.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.text_collation import IDENTIFIER_TEXT
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.planning.models.resource import LABEL_LENGTH
from app.planning.models.tenancy import PlanningTenantMixin

NOTE_LENGTH = 200
REFERENCE_TYPE_LENGTH = 64


class BookingKind(enum.StrEnum):
    WORK = "work"
    DROP_OFF = "drop_off"
    PICK_UP = "pick_up"
    BLOCK = "block"


class BookingStatus(enum.StrEnum):
    BOOKED = "booked"
    RELEASED = "released"


class ReplanCause(enum.StrEnum):
    BLOCKED = "blocked"
    OUTSIDE_DROP_OFF_PICK_UP = "outside_drop_off_pick_up"
    RESOURCE_DEACTIVATED = "resource_deactivated"
    RESOURCE_MOVED = "resource_moved"


class WarningCode(enum.StrEnum):
    """What warns and stays bookable (check 18; Anto, 2026-11-25)."""

    OUTSIDE_WORKING_HOURS = "outside_working_hours"
    BEFORE_DROP_OFF = "before_drop_off"
    AFTER_PICK_UP = "after_pick_up"
    RESOURCE_BLOCKED = "resource_blocked"


class BookingChange(enum.StrEnum):
    """The write a warning was overruled on."""

    BOOKED = "booked"
    MOVED = "moved"


def _trio(prefix: str) -> str:
    return (
        f"({prefix}_id IS NULL AND {prefix}_label IS NULL AND {prefix}_label_refreshed_at IS NULL) OR "
        f"({prefix}_id IS NOT NULL AND {prefix}_label IS NOT NULL AND {prefix}_label_refreshed_at IS NOT NULL)"
    )


class Booking(PrimaryKeyMixin, PlanningTenantMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "planning_booking"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_planning_booking_id_tenant_id"),
        ForeignKeyConstraint(
            ["replan_caused_by_booking_id", "tenant_id"],
            ["planning_booking.id", "planning_booking.tenant_id"],
            name="fk_planning_booking_replan_caused_by_booking_tenant",
        ),
        ForeignKeyConstraint(
            ["replan_caused_by_resource_id", "tenant_id"],
            ["planning_resource.id", "planning_resource.tenant_id"],
            name="fk_planning_booking_replan_caused_by_resource_tenant",
        ),
        CheckConstraint("starts_at < ends_at", name="ck_planning_booking_interval"),
        CheckConstraint(_trio("reference"), name="ck_planning_booking_reference_trio"),
        CheckConstraint(_trio("job"), name="ck_planning_booking_job_trio"),
        CheckConstraint(_trio("vehicle"), name="ck_planning_booking_vehicle_trio"),
        CheckConstraint("(reference_type IS NULL) = (reference_id IS NULL)", name="ck_planning_booking_reference_type"),
        CheckConstraint(
            "(kind = 'block' AND reference_id IS NULL AND job_id IS NULL AND vehicle_id IS NULL) "
            "OR (kind <> 'block' AND reference_id IS NOT NULL AND note IS NULL)",
            name="ck_planning_booking_reference",
        ),
        CheckConstraint(
            "kind <> 'work' OR (job_id IS NOT NULL AND vehicle_id IS NOT NULL)", name="ck_planning_booking_work"
        ),
        CheckConstraint(
            "(replan_cause IS NULL AND replan_since IS NULL AND replan_caused_by_booking_id IS NULL "
            "AND replan_caused_by_resource_id IS NULL) OR "
            "(kind <> 'block' AND status = 'booked' AND replan_since IS NOT NULL AND ("
            "(replan_cause IN ('blocked', 'outside_drop_off_pick_up') AND replan_caused_by_booking_id IS NOT NULL "
            "AND replan_caused_by_resource_id IS NULL) OR "
            "(replan_cause IN ('resource_deactivated', 'resource_moved') AND replan_caused_by_resource_id IS NOT NULL "
            "AND replan_caused_by_booking_id IS NULL)))",
            name="ck_planning_booking_replan",
        ),
        CheckConstraint("(status = 'released') = (released_at IS NOT NULL)", name="ck_planning_booking_released"),
        CheckConstraint("status = 'released' OR released_by IS NULL", name="ck_planning_booking_released_by"),
        Index(
            "ix_planning_booking_tenant_time",
            "tenant_id",
            "starts_at",
            "ends_at",
            postgresql_where=text("status = 'booked'"),
            sqlite_where=text("status = 'booked'"),
        ),
        Index("ix_planning_booking_tenant_reference", "tenant_id", "reference_id"),
        Index(
            "ix_planning_booking_tenant_vehicle",
            "tenant_id",
            "vehicle_id",
            postgresql_where=text("vehicle_id IS NOT NULL"),
            sqlite_where=text("vehicle_id IS NOT NULL"),
        ),
        Index(
            "ix_planning_booking_tenant_job",
            "tenant_id",
            "job_id",
            postgresql_where=text("job_id IS NOT NULL"),
            sqlite_where=text("job_id IS NOT NULL"),
        ),
    )

    kind: Mapped[BookingKind] = mapped_column(StoredEnum(BookingKind, length=16), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(StoredEnum(BookingStatus, length=16), nullable=False)
    starts_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    ends_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)

    # The caller's reference R, e.g. `aftersales.order`.
    reference_type: Mapped[str | None] = mapped_column(
        String(REFERENCE_TYPE_LENGTH), nullable=True, info=IDENTIFIER_TEXT
    )
    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the context reference_type names. No DB-level FK."
    )
    reference_label: Mapped[str | None] = mapped_column(String(LABEL_LENGTH), nullable=True)
    reference_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # The job a work booking is for: the same job never holds one person twice at one time (check 17).
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the context reference_type names (the job of R). No DB-level FK."
    )
    job_label: Mapped[str | None] = mapped_column(String(LABEL_LENGTH), nullable=True)
    job_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK."
    )
    vehicle_label: Mapped[str | None] = mapped_column(String(LABEL_LENGTH), nullable=True)
    vehicle_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # A block's note.
    note: Mapped[str | None] = mapped_column(String(NOTE_LENGTH), nullable=True)

    replan_cause: Mapped[ReplanCause | None] = mapped_column(StoredEnum(ReplanCause, length=32), nullable=True)
    replan_since: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    replan_caused_by_booking_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    replan_caused_by_resource_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    released_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    released_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the platform context (User.id). No DB-level FK."
    )


class BookingResource(PrimaryKeyMixin, PlanningTenantMixin, Base):
    """A resource a booking holds. Written as a set with the booking."""

    __tablename__ = "planning_booking_resource"
    __table_args__ = (
        UniqueConstraint("booking_id", "resource_id", name="uq_planning_booking_resource_booking_resource"),
        ForeignKeyConstraint(
            ["booking_id", "tenant_id"],
            ["planning_booking.id", "planning_booking.tenant_id"],
            name="fk_planning_booking_resource_booking_tenant",
        ),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            ["planning_resource.id", "planning_resource.tenant_id"],
            name="fk_planning_booking_resource_resource_tenant",
        ),
        Index("ix_planning_booking_resource_resource_id", "resource_id"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)


class OverruledWarning(PrimaryKeyMixin, PlanningTenantMixin, Base):
    """A warning a booking was made against (check 18). Append-only."""

    __tablename__ = "planning_overruled_warning"
    __table_args__ = (
        ForeignKeyConstraint(
            ["booking_id", "tenant_id"],
            ["planning_booking.id", "planning_booking.tenant_id"],
            name="fk_planning_overruled_warning_booking_tenant",
        ),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            ["planning_resource.id", "planning_resource.tenant_id"],
            name="fk_planning_overruled_warning_resource_tenant",
        ),
        ForeignKeyConstraint(
            ["against_booking_id", "tenant_id"],
            ["planning_booking.id", "planning_booking.tenant_id"],
            name="fk_planning_overruled_warning_against_booking_tenant",
        ),
        CheckConstraint("booked_starts_at < booked_ends_at", name="ck_planning_overruled_warning_interval"),
        Index("ix_planning_overruled_warning_booking_id", "booking_id"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    code: Mapped[WarningCode] = mapped_column(StoredEnum(WarningCode, length=32), nullable=False)
    # The resource the warning was about (a person outside their hours, a blocked resource);
    # None for a warning about the booking as a whole (before the drop-off, after the pick-up).
    resource_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    # The booking the warning was raised against (the slot, the block), where there is one.
    against_booking_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    change: Mapped[BookingChange] = mapped_column(StoredEnum(BookingChange, length=16), nullable=False)
    booked_starts_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    booked_ends_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    overruled_by: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (User.id). No DB-level FK."
    )
    # Frozen when the warning was overruled; never refreshed (decision 22).
    overruled_by_name: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    overruled_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
