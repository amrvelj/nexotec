"""SalesOffer (WP-8 PR-1, S-D01): "a quote for one customer and one car,
not yet a deal." An offer starts as a bare draft — the reference prototype
allocates a number ("O-003347") before Kunde or Fahrzeug are chosen — and
is filled in by the container-based generation workspace (PR-2 onward).

`offer_number` is an immutable business key, never reused (same posture as
app.customer's customer_number — see app/customer/services/customer.py's
own reasoning: a reused number would silently point at two different
records over time).

`customer_id`/`customer_label`/`customer_locality` follow the "split by
staleness class" rule established for this package: offer_number itself is
an immutable business key and never refreshes; these three are genuinely
mutable display fields and share ONE refresh timestamp, kept current by a
customer.updated/customer.merged consumer (app/sales/consumers.py, PR-7).

`vehicle_label` here is a plain mutable working field, distinct from the
ADR-041 *frozen* `vehicle_snapshot` that PR-3 adds once pricing exists —
before a snapshot is taken there is nothing to freeze yet.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class OfferStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    CANCELLED = "cancelled"


class SalesOffer(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "sales_offer"

    offer_number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[OfferStatus] = mapped_column(
        SAEnum(OfferStatus, native_enum=False, length=16), nullable=False, default=OfferStatus.DRAFT
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the customer context. No DB-level FK."
    )
    customer_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_denorm_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # WP-8 PR-2 (FR-S-08's two-path vehicle container): "stock" means
    # stock_item_id is the source of truth; "manual" means vehicle_label +
    # manual_vehicle_condition are hand-typed (ADR-045 — this never creates
    # a vehicle-mdm record; a pipeline stock item is only materialized on
    # contract confirmation, PR-6).
    vehicle_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "stock" | "manual"
    stock_item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the inventory context (StockItem.id). No DB-level FK."
    )
    vehicle_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manual_vehicle_condition: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # WP-8 PR-2 (S-D03) — the Leasing container is a refused calculator:
    # free-text inputs only, captured so a later real calculator can be
    # dropped in without a schema change, never computed here.
    leasing_down_payment: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    leasing_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leasing_km_per_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Materialized total (PR-3's pricing.build_up() is the real derivation;
    # this column is what the grid and this row itself display).
    gross_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    cancelled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
