"""Stock's projection of the Sales contracts it needs to know about (KAN-630).

ADR-131 puts Stock below Sales: Stock never asks Sales. What its two rules
need from Sales — the orphan sweep (is the contract holding a reservation
signed?) and «Einkauf stornieren» (did a contract on the car request its
invoice?) — Stock keeps here, fed by Sales' own facts through the outbox:
`sales.contract.created`, `.confirmed`, `.invoice_requested` and
`.cancelled`, each consumed idempotently (app.inventory.consumers), and by
`scripts/backfill_inventory_sales_contract_projection.py` for contracts that
existed before. Written only by Stock; Sales' tables are never read here
(the nightly reconciliation compares the two, rule 10).

Delivery is at-least-once and unordered, so every write is a merge that
gives the same row whatever order the facts arrive in: the status only
moves up `_RANK` (cancelled is final), `invoice_requested_at` and
`stock_item_id` are set once.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ProjectedContractStatus(str, enum.Enum):
    """Sales' contract status as Stock knows it — Stock's own enum, the same
    values as Sales' (`sales_contract.status`), so reconciliation compares
    the stored values directly."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    INVOICED = "invoiced"
    CANCELLED = "cancelled"


# The order a contract moves in: pending -> confirmed -> invoiced, and
# pending or confirmed -> cancelled, which is final. A fact never lowers it.
RANK: dict[ProjectedContractStatus, int] = {
    ProjectedContractStatus.PENDING: 0,
    ProjectedContractStatus.CONFIRMED: 1,
    ProjectedContractStatus.INVOICED: 2,
    ProjectedContractStatus.CANCELLED: 3,
}


class InventorySalesContract(PrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "inventory_sales_contract"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contract_id", name="uq_inventory_sales_contract_tenant_id_contract_id"),
        # «Einkauf stornieren»: the contracts on one car.
        Index("ix_inventory_sales_contract_tenant_id_stock_item_id", "tenant_id", "stock_item_id"),
    )

    contract_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the sales context (SalesContract.id). No DB-level FK."
    )
    # Rule 2's display label (the contract number) and when it was read. Null
    # until a fact that carries the number arrives (`sales.contract.confirmed`
    # does not).
    contract_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    contract_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[ProjectedContractStatus] = mapped_column(
        StoredEnum(ProjectedContractStatus, length=16), nullable=False
    )
    # The stock car whose invoice the contract requested (from
    # `sales.contract.invoice_requested`); Stock's own id.
    stock_item_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    # When Sales requested the invoice («Rechnung anfordern»): the event's
    # occurred_at, or Sales' column when the backfill wrote it.
    invoice_requested_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class InventorySalesContractBackfill(PrimaryKeyMixin, Base):
    """One row per committed run of the projection's backfill (KAN-630).
    Its presence is what tells Stock the projection covers the contracts
    whose facts were published before its consumers existed: until a run
    is recorded, a fact Stock's consumer never applied counts as unapplied,
    published or not, and the sweep releases no reservation whose contract
    Stock has never heard of (app.inventory.services.sales_contract)."""

    __tablename__ = "inventory_sales_contract_backfill"

    completed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    contracts_read: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False)
