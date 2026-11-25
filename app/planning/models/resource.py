"""The resources a booking holds (KAN-808, S7): a person or a workplace,
each at one location of the Betrieb.

- A **person** is a platform user who works in the Betrieb: one person
  resource per user per Betrieb (Anto, 2026-11-25), at one location, with a
  technician type, qualifications and weekly working hours.
- A **workplace** is a named place at a location (a lift, a diagnosis bay),
  with a workplace type; its name is unique at its location.

The user and the location are platform's: the three-column pattern (id,
label, `label_refreshed_at`; rule 2, ADR-221 T5). The list entries are
planning's own, so their foreign keys are real — composite with the tenant,
so a resource never names another Betrieb's entry.

`is_active` says whether the resource appears in the planner; a resource is
deactivated, never deleted, so a booking never names nothing.

Working hours are local wall-clock times (Europe/Zurich), one row per
weekday with an optional second interval (a lunch break), the shape of
`location_opening_hours`. They are replaced as a set under the resource's
version, so the rows carry no version of their own.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.planning.models.lists import NAME_LENGTH
from app.planning.models.tenancy import PlanningTenantMixin

LABEL_LENGTH = 200


class ResourceKind(enum.StrEnum):
    PERSON = "person"
    WORKPLACE = "workplace"


class Resource(PrimaryKeyMixin, PlanningTenantMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "planning_resource"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_planning_resource_id_tenant_id"),
        ForeignKeyConstraint(
            ["workplace_type_id", "tenant_id"],
            ["planning_workplace_type.id", "planning_workplace_type.tenant_id"],
            name="fk_planning_resource_workplace_type_tenant",
        ),
        ForeignKeyConstraint(
            ["technician_type_id", "tenant_id"],
            ["planning_technician_type.id", "planning_technician_type.tenant_id"],
            name="fk_planning_resource_technician_type_tenant",
        ),
        CheckConstraint(
            "(kind = 'person' AND user_id IS NOT NULL AND user_label IS NOT NULL "
            "AND user_label_refreshed_at IS NOT NULL AND name IS NULL AND workplace_type_id IS NULL) "
            "OR (kind = 'workplace' AND name IS NOT NULL AND user_id IS NULL AND user_label IS NULL "
            "AND user_label_refreshed_at IS NULL AND technician_type_id IS NULL)",
            name="ck_planning_resource_shape",
        ),
        Index(
            "uq_planning_resource_tenant_user",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_planning_resource_location_name",
            "tenant_id",
            "location_id",
            "name",
            unique=True,
            postgresql_where=text("name IS NOT NULL"),
            sqlite_where=text("name IS NOT NULL"),
        ),
        Index("ix_planning_resource_tenant_location", "tenant_id", "location_id"),
    )

    kind: Mapped[ResourceKind] = mapped_column(StoredEnum(ResourceKind, length=16), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Location.id). No DB-level FK."
    )
    location_label: Mapped[str] = mapped_column(String(LABEL_LENGTH), nullable=False)
    location_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the platform context (User.id). No DB-level FK."
    )
    user_label: Mapped[str | None] = mapped_column(String(LABEL_LENGTH), nullable=True)
    user_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # A workplace's name; a person is named by the user's label.
    name: Mapped[str | None] = mapped_column(String(NAME_LENGTH), nullable=True)
    workplace_type_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    technician_type_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))


class ResourceQualification(PrimaryKeyMixin, PlanningTenantMixin, Base):
    """A qualification a person holds. Written as a set with the resource."""

    __tablename__ = "planning_resource_qualification"
    __table_args__ = (
        UniqueConstraint(
            "resource_id", "qualification_id", name="uq_planning_resource_qualification_resource_qualification"
        ),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            ["planning_resource.id", "planning_resource.tenant_id"],
            name="fk_planning_resource_qualification_resource_tenant",
        ),
        ForeignKeyConstraint(
            ["qualification_id", "tenant_id"],
            ["planning_qualification.id", "planning_qualification.tenant_id"],
            name="fk_planning_resource_qualification_qualification_tenant",
        ),
        Index("ix_planning_resource_qualification_qualification_id", "qualification_id"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    qualification_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)


class WorkingHours(PrimaryKeyMixin, PlanningTenantMixin, Base):
    """One working weekday of a person (ISO: 1 = Monday … 7 = Sunday)."""

    __tablename__ = "planning_working_hours"
    __table_args__ = (
        UniqueConstraint("resource_id", "weekday", name="uq_planning_working_hours_resource_weekday"),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            ["planning_resource.id", "planning_resource.tenant_id"],
            name="fk_planning_working_hours_resource_tenant",
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_planning_working_hours_weekday"),
        CheckConstraint("starts_at < ends_at", name="ck_planning_working_hours_interval"),
        CheckConstraint(
            "(second_starts_at IS NULL AND second_ends_at IS NULL) OR "
            "(second_starts_at IS NOT NULL AND second_ends_at IS NOT NULL "
            "AND ends_at < second_starts_at AND second_starts_at < second_ends_at)",
            name="ck_planning_working_hours_second_interval",
        ),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    starts_at: Mapped[dt.time] = mapped_column(Time, nullable=False)
    ends_at: Mapped[dt.time] = mapped_column(Time, nullable=False)
    second_starts_at: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
    second_ends_at: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
