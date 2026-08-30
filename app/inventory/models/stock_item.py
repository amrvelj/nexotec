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

from sqlalchemy import DECIMAL, Boolean, Date, Index, Integer, String, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
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


class AgeingBucket(str, enum.Enum):
    """WP-7 PR-7. Fixed, NOT dealer-configurable — "a configurable
    threshold is a settings screen, a migration and a per-tenant answer to
    'why is this car orange for you and green for me' for a number three
    buckets already express." Derived on read from in_stock_at, never
    stored. A SEPARATE, genuinely dealer-configurable alert-threshold
    setting (Dealership.ageing_alert_thresholds, default 30/60/90) drives
    notifications only — same underlying "days in stock" number, two
    independent consumers, never the same field or the same config
    screen.
    """

    GREEN = "green"  # 0-60 days
    AMBER = "amber"  # 61-120 days
    RED = "red"  # 121+ days


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
        # WP-7 PR-2: the defense-in-depth half of consumer idempotency — the
        # outbox harness's ProcessedEvent table already stops the SAME
        # message id from being handled twice, but this stops a genuinely
        # duplicate emission (a different message id, same business event)
        # from double-creating a pipeline item.
        Index(
            "uq_stock_item_tenant_id_pipeline_ref",
            "tenant_id",
            "pipeline_ref",
            unique=True,
            postgresql_where=text("pipeline_ref IS NOT NULL"),
            sqlite_where=text("pipeline_ref IS NOT NULL"),
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
    # WP-7 PR-4 (ADR-047). reserved_by_contract_id is opaque — Sales's own
    # id, never a real FK (no app.sales.Contract exists yet either). One
    # active reservation per item, enforced in services/reservation.py by
    # a row lock + state check, not a DB constraint (a partial unique
    # index on reservation_state alone can't express "at most one
    # RESERVED row," only "at most one row with this exact value," which
    # a native/portable index can't distinguish from the intent here
    # without also keying on the item itself — already true by definition
    # since these are columns on the item, not a separate table).
    reserved_by_contract_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the sales context (a future Contract.id). No DB-level FK."
    )
    active_reservation_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True, unique=True)
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

    # WP-7 PR-2 (ADR-045). Set only for an item that originated from a
    # Sales auto-create path (manual configuration or trade-in) — never
    # user-editable, the sole reason it exists is idempotency (see
    # __table_args__ above). A manually-created item (StockCreatePage,
    # PR-1) never has one.
    pipeline_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    order_date: Mapped[dt.date | None] = mapped_column(Date(), nullable=True)
    expected_delivery: Mapped[dt.date | None] = mapped_column(Date(), nullable=True)
    # Set once, by promote_to_vehicle_mdm, the moment lifecycle_status
    # flips pipeline -> in_stock. Ageing (PR-7) is derived from this, never
    # from created_at — a factory order's time in the pipeline doesn't
    # count as ageing on the lot.
    in_stock_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # WP-7 PR-3: purchase / landed cost / fiktiver Vorsteuerabzug
    # (Art. 28a MWSTG). ADR-057 — there is NO vatTreatment field anywhere;
    # notional_input_tax_* is a purchase-side fact, never shown to a
    # customer, never a per-vehicle "VAT treatment" the way pre-2010
    # Margenbesteuerung was.
    supplier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    supplier_is_vat_registered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    purchase_date: Mapped[dt.date | None] = mapped_column(Date(), nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    purchase_invoice_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    landed_cost: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # Prefilled automatically from supplier_is_vat_registered (false for a
    # VAT-registered business, true for a private individual) — an admin
    # override is permitted but always produces its own audit entry
    # (services/purchase.py::override_notional_input_tax), never a silent
    # overwrite.
    notional_input_tax_applicable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notional_input_tax_rate: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 2), nullable=True)
    notional_input_tax_amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    notional_input_tax_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # WP-7 PR-5 (ADR-052) — a replicated fact, set by record_purchase once
    # BOTH the VIN is known and the purchase is booked (S-D10: "the
    # surviving confirmation gate is the purchase, not the tax"). Columns
    # land here (PR-3) since record_purchase is what first needs them; the
    # full reconciliation-against-finance.invoice.issued logic is PR-5.
    is_invoiceable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # FR-I-12: a sold (invoiced) item leaves the active list — enforced by
    # filtering WHERE left_stock_at IS NULL, never a 4th lifecycle value.
    left_stock_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
