"""The service catalogue (KAN-818, AS1-15; build slice 1, section 3.10;
rulings 174, 176, 182, 183; checks 44, 45, 46).

Two tables, both Aftersales' own (the screen is in Dealer Administration,
A-RULE-0; the data is Aftersales', `aftersales.md`):

- `service_package` — a named piece of work: a name typed once with an
  optional translation per language (ruling 176), active or inactive, and a
  `version` — the package is the aggregate, its positions are unversioned
  child rows written with it.
- `service_package_position` — what a package holds: **labour** with a
  fixed time and a labour category, and «check needed»; or **text**.

**Time** (ruling 183): typed in the Betrieb's unit — hours, or work units
with the work units per hour in force — and kept with that unit and that
number, as a workshop position keeps them (check 48): a later change of
the setting changes no package.

**A package is copied onto a job, never linked** (section 10; check 45):
no workshop position names the package it came from. A package is never
deleted — it is set inactive, and an inactive package is not offered.
It holds no article and no price of its own in this slice: its price is its
time at the labour rate that applies on the order (section 3.10).

Nexotec ships no package (ruling 174): a package carries times, and Nexotec
ships no figure.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.aftersales.models.service_order import TIME_TYPE, WORK_UNITS_PER_HOUR_TYPE, LabourTimeUnit
from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base

PACKAGE_NAME_LENGTH = 200
POSITION_TEXT_LENGTH = 1000
CATEGORY_LABEL_LENGTH = 200


class PackagePositionKind(str, enum.Enum):
    """Section 3.10: labour positions with a fixed time, and text positions.
    Articles join with the slice «parts on the order»."""

    LABOUR = "labour"
    TEXT = "text"


class ServicePackage(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "service_package"

    name: Mapped[str] = mapped_column(String(PACKAGE_NAME_LENGTH), nullable=False)
    # Ruling 176: {language: name} for the languages given; none means «as typed».
    name_translations: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ServicePackagePosition(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "service_package_position"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'labour' AND labour_category_id IS NOT NULL AND time_quantity IS NOT NULL "
            "AND time_quantity > 0 AND time_unit IS NOT NULL) "
            "OR (kind = 'text' AND labour_category_id IS NULL AND time_quantity IS NULL AND NOT check_needed)",
            name="ck_service_package_position_shape",
        ),
    )

    package_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("service_package.id"), nullable=False, index=True)
    sort_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[PackagePositionKind] = mapped_column(StoredEnum(PackagePositionKind, length=16), nullable=False)
    text: Mapped[str] = mapped_column(String(POSITION_TEXT_LENGTH), nullable=False)

    # Labour only: the category it is priced in (platform's master data, D-A-19) — rule 2's
    # three-column pattern; the label is refreshed nightly (`aftersales.package_labels`).
    labour_category_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the platform context (LabourRateCategory.id). No DB-level FK."
    )
    labour_category_label: Mapped[str | None] = mapped_column(String(CATEGORY_LABEL_LENGTH), nullable=True)
    labour_category_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # The fixed time as typed, with the unit and the work units per hour it was typed with (ruling 183).
    time_quantity: Mapped[Decimal | None] = mapped_column(TIME_TYPE, nullable=True)
    time_unit: Mapped[LabourTimeUnit | None] = mapped_column(StoredEnum(LabourTimeUnit, length=16), nullable=True)
    work_units_per_hour: Mapped[Decimal | None] = mapped_column(WORK_UNITS_PER_HOUR_TYPE, nullable=True)

    # «Prüfung nötig» (section 3.6): a labour position whose work gets a second look.
    check_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
