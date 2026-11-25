"""The Betrieb's planner lists (KAN-808, S7): workplace types, technician
types and qualifications. Each is a name typed once, with optional
translations (ruling 176), and a place in the list (`sort_order`).

Workplace and technician types may arrive as Nexotec's starting set
(ADR-222, copied in a later PR): such an entry is a sample (`is_sample`) with
its `seed_code`, until the manager changes or confirms it. A sample carries
no rank and no counted-time factor — a figure is the Betrieb's own, never
Nexotec's (ruling 174), so the database refuses one on a sample.

The `(id, tenant_id)` unique constraints are the targets of the resource's
composite foreign keys: a resource can only name an entry of its own Betrieb.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, Index, Numeric, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.text_collation import IDENTIFIER_TEXT
from app.db import Base
from app.planning.models.tenancy import PlanningTenantMixin

NAME_LENGTH = 100
SEED_CODE_LENGTH = 64


class WorkplaceType(PrimaryKeyMixin, PlanningTenantMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "planning_workplace_type"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_planning_workplace_type_id_tenant_id"),
        Index(
            "uq_planning_workplace_type_tenant_seed_code",
            "tenant_id",
            "seed_code",
            unique=True,
            postgresql_where=text("seed_code IS NOT NULL"),
            sqlite_where=text("seed_code IS NOT NULL"),
        ),
        CheckConstraint("is_sample = false OR seed_code IS NOT NULL", name="ck_planning_workplace_type_sample_has_seed"),
    )

    name: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False)
    # {"fr": …, "it": …} — only the languages translated (ruling 176).
    name_translations: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    seed_code: Mapped[str | None] = mapped_column(String(SEED_CODE_LENGTH), nullable=True, info=IDENTIFIER_TEXT)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))


class TechnicianType(PrimaryKeyMixin, PlanningTenantMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "planning_technician_type"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_planning_technician_type_id_tenant_id"),
        Index(
            "uq_planning_technician_type_tenant_seed_code",
            "tenant_id",
            "seed_code",
            unique=True,
            postgresql_where=text("seed_code IS NOT NULL"),
            sqlite_where=text("seed_code IS NOT NULL"),
        ),
        CheckConstraint(
            "is_sample = false OR seed_code IS NOT NULL", name="ck_planning_technician_type_sample_has_seed"
        ),
        CheckConstraint(
            "is_sample = false OR (rank IS NULL AND counted_time_factor IS NULL)",
            name="ck_planning_technician_type_sample_without_figure",
        ),
        CheckConstraint("rank IS NULL OR rank BETWEEN 1 AND 99", name="ck_planning_technician_type_rank"),
        CheckConstraint(
            "counted_time_factor IS NULL OR (counted_time_factor > 0 AND counted_time_factor <= 9.99)",
            name="ck_planning_technician_type_counted_time_factor",
        ),
    )

    name: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False)
    name_translations: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    seed_code: Mapped[str | None] = mapped_column(String(SEED_CODE_LENGTH), nullable=True, info=IDENTIFIER_TEXT)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    # 1–99: the Betrieb's own order of seniority. None: not ranked.
    rank: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Counted time = clocked time × factor. None: counted time is clocked time.
    counted_time_factor: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)


class Qualification(PrimaryKeyMixin, PlanningTenantMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "planning_qualification"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_planning_qualification_id_tenant_id"),)

    name: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False)
    name_translations: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    # Stored only (Anto, 2026-11-25): nothing enforces it until a job or a
    # workplace names a required qualification.
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
