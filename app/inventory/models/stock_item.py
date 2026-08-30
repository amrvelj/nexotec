"""The stock item (WP-7 PR-1): "this dealership commercially owns this
car." A pre-VIN factory order and a VIN'd car on the lot are the SAME
table, distinguished only by `lifecycle_status` and a null `vehicle_id` —
never a second, pipeline-only table (ADR-045's "pipeline vehicles are
stock items in inventory, never records in vehicle-mdm" cuts the other
way too: once inventory owns them, it owns them in one place).

Two axes, always independent (ADR-054): `lifecycle_status` and
`reservation_state`. Every combination is legal, including
`pipeline + reserved` (a factory order already sold) — a single merged
enum cannot express that, which is the ordinary case, not an edge case.

`lifecycle_status` has exactly THREE values. PRD-Stock's own data-spec
table (revised 2026-08-16, citing ADR-054) is authoritative here over an
earlier, stale four-value draft that included "sold": a sold (invoiced)
vehicle is never a lifecycle value, it is simply absent from the active
list (FR-I-12) — enforced by `left_stock_at IS NOT NULL` (PR-5), not a
fourth enum member. Reinforced by tests/test_inventory_stock_item.py's
own enum-shape assertion, so a future migration can't silently reintroduce
one.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Date, Index, Integer, String, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID
from app.db import Base


class LifecycleStatus(str, enum.Enum):
    PIPELINE = "pipeline"
    IN_STOCK = "in_stock"
    STORNO_PENDING = "storno_pending"


class ReservationState(str, enum.Enum):
    NONE = "none"
    RESERVED = "reserved"


class StockItemCondition(str, enum.Enum):
    NEW = "new"
    USED = "used"
    DEMO = "demo"
    TAGESZ = "tagesz"


class StockNumberSequence(Base):
    """Row-lock allocator for `S-000001`-style stock numbers, one row per
    tenant — mirrors app.customer's per-group CustomerNumberSequence, not
    app.vehicle's single global row: a stock number is dealership-owned
    stock, not a global fact like a VIN.
    """

    __tablename__ = "stock_number_sequence"

    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class StockItem(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "stock_item"
    __table_args__ = (
        UniqueConstraint("tenant_id", "stock_number", name="uq_stock_item_tenant_id_stock_number"),
        Index(
            "uq_stock_item_tenant_id_vin",
            "tenant_id",
            "vin",
            unique=True,
            postgresql_where=text("vin IS NOT NULL"),
            sqlite_where=text("vin IS NOT NULL"),
        ),
    )

    stock_number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Null while lifecycle_status='pipeline' and no VIN has arrived yet
    # (ADR-045) — a pre-VIN factory order or trade-in has no vehicle-mdm
    # record at all. `vin` is denormalized from VehicleMdm once promotion
    # (PR-2) sets vehicle_id, purely so the grid and search don't need a
    # cross-context join for the one field everyone looks a car up by.
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK."
    )
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)

    # Denormalized display text ("Volkswagen Käfer 1303 LS Cabriolet") —
    # populated from the catalogue when a variant is known, or typed by
    # hand for a manual/unverified configuration (FR-I-02). Never the
    # source of truth for make/model/trim; VehicleMdm's own catalogue
    # link is, once vehicle_id is set.
    vehicle_label: Mapped[str] = mapped_column(String(200), nullable=False)

    lifecycle_status: Mapped[LifecycleStatus] = mapped_column(
        SAEnum(LifecycleStatus, native_enum=False, length=16), nullable=False, default=LifecycleStatus.PIPELINE
    )
    reservation_state: Mapped[ReservationState] = mapped_column(
        SAEnum(ReservationState, native_enum=False, length=16), nullable=False, default=ReservationState.NONE
    )
    condition: Mapped[StockItemCondition] = mapped_column(
        SAEnum(StockItemCondition, native_enum=False, length=16), nullable=False
    )

    location_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the platform context (Location.id). No DB-level FK."
    )

    odometer_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    list_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    effective_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    first_registration_date: Mapped[dt.date | None] = mapped_column(Date(), nullable=True)
