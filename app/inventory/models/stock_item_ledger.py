"""The Wagenbuch (WP-7 PR-6) — append-only, entity-private (ADR-029, never
group-readable, unlike vehicle identity). Its own table, not literally
AuditEvent rows: a different privacy domain (per-vehicle commercial
figures, not a generic audit trail) with its own retention story. Append-
only by omission, same discipline as app.core.audit_model.AuditEvent — no
update/delete function exists anywhere in services/ledger.py; a
correction is a counter-entry, never an edit (same storno discipline
elsewhere in this codebase).

Commission is deliberately excluded (K-10) — not a category here at all.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class LedgerCategory(str, enum.Enum):
    # Costs
    EINSTANDSPREIS = "einstandspreis"
    LANDED_COST = "landed_cost"
    AUFBEREITUNG = "aufbereitung"
    REPARATUR = "reparatur"
    GUTACHTEN = "gutachten"
    STANDKOSTEN = "standkosten"
    WERBUNG = "werbung"
    GARANTIE = "garantie"
    ABWERTUNG = "abwertung"
    PROMOTION = "promotion"
    SONSTIGE_KOSTEN = "sonstige_kosten"
    # Revenues
    VERKAUFSERLOES = "verkaufserloes"
    KICKBACK = "kickback"
    ZUSATZERLOES = "zusatzerloes"
    FOERDERUNG = "foerderung"
    SONSTIGE_ERTRAEGE = "sonstige_ertraege"


class LedgerDirection(str, enum.Enum):
    COST = "cost"
    REVENUE = "revenue"


# Never accepted from a caller — derived server-side from category, closed
# vocabulary, no free text (services/ledger.py::record_cost).
DIRECTION_BY_CATEGORY: dict[LedgerCategory, LedgerDirection] = {
    LedgerCategory.EINSTANDSPREIS: LedgerDirection.COST,
    LedgerCategory.LANDED_COST: LedgerDirection.COST,
    LedgerCategory.AUFBEREITUNG: LedgerDirection.COST,
    LedgerCategory.REPARATUR: LedgerDirection.COST,
    LedgerCategory.GUTACHTEN: LedgerDirection.COST,
    LedgerCategory.STANDKOSTEN: LedgerDirection.COST,
    LedgerCategory.WERBUNG: LedgerDirection.COST,
    LedgerCategory.GARANTIE: LedgerDirection.COST,
    LedgerCategory.ABWERTUNG: LedgerDirection.COST,
    LedgerCategory.PROMOTION: LedgerDirection.COST,
    LedgerCategory.SONSTIGE_KOSTEN: LedgerDirection.COST,
    LedgerCategory.VERKAUFSERLOES: LedgerDirection.REVENUE,
    LedgerCategory.KICKBACK: LedgerDirection.REVENUE,
    LedgerCategory.ZUSATZERLOES: LedgerDirection.REVENUE,
    LedgerCategory.FOERDERUNG: LedgerDirection.REVENUE,
    LedgerCategory.SONSTIGE_ERTRAEGE: LedgerDirection.REVENUE,
}

# verkaufserloes/foerderung stay automatic-only; kickback/zusatzerloes are
# hand-bookable in practice (amended 2026-08-21) since Finance doesn't
# exist yet to post them.
AUTOMATIC_ONLY_CATEGORIES = frozenset({LedgerCategory.VERKAUFSERLOES, LedgerCategory.FOERDERUNG})


class StockItemLedger(PrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "stock_item_ledger"
    __table_args__ = (UniqueConstraint("tenant_id", "source_ref", name="uq_stock_item_ledger_tenant_id_source_ref"),)

    stock_item_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("stock_item.id"), nullable=False, index=True)
    category: Mapped[LedgerCategory] = mapped_column(SAEnum(LedgerCategory, native_enum=False, length=32), nullable=False)
    direction: Mapped[LedgerDirection] = mapped_column(SAEnum(LedgerDirection, native_enum=False, length=8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    # The idempotency key for recordCost — unique per tenant. A human
    # booking by hand still supplies one (a client-generated UUID), same
    # as any other caller of this surface.
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    is_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
