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
from typing import Any

from sqlalchemy import DECIMAL, JSON, Integer, String
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
    # WP-8 PR-3 — a manual configuration has no stock item to price from,
    # so the seller enters this directly; unlike a stock vehicle's price
    # (frozen from live data, ADR-041), this is a plain mutable field with
    # no external source to protect against, same posture as vehicle_label.
    manual_base_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    # WP-8 PR-2 (S-D03) — the Leasing container is a refused calculator:
    # free-text inputs only, captured so a later real calculator can be
    # dropped in without a schema change, never computed here.
    leasing_down_payment: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    leasing_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leasing_km_per_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # WP-8 PR-3 (ADR-041) — frozen once, from app.inventory.public.
    # get_stock_item_pricing's plain dict, never re-read from live stock
    # data afterward: "a later catalogue correction never changes an
    # existing offer" (confirmed live, verbatim, on the reference
    # prototype's own Preisaufbau footer). Re-frozen only if the vehicle
    # identity itself changes (a different stock item, or manual details
    # edited) — see services/snapshot.py::freeze_vehicle_snapshot.
    vehicle_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    vehicle_snapshot_frozen_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # WP-8 PR-3 — pricing.build_up()'s materialized result. base -> options
    # -> list -> accessories -> total -> discount -> price (FR-S spec's own
    # level order). discount_type/discount_value are the seller's input;
    # everything else here is derived, never independently editable.
    base_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    options_total: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    list_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    accessories_total: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    total_before_discount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    discount_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "percent" | "amount"
    discount_value: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # Materialized total — pricing.build_up()'s final `grossPrice`. What
    # the grid and this row itself display; the printed document's own
    # ONE price (ADR-057/S-D10) is this same figure, VAT added on top only
    # at render time (PR-7), never stored here.
    gross_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    # WP-8 PR-3 (ADR-049/ADR-029) — Einstandspreis, sourced from the frozen
    # snapshot's own purchasePrice; None for a manual configuration, which
    # has no real stock-item cost to compare against. Entity-private: no
    # group-read endpoint exists anywhere for sales_offer/sales_contract or
    # sales_deal (see app/sales/models/deal.py's own docstring) — that
    # absence, not a filtered column, is what ADR-029 relies on here.
    cost_basis: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    margin: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    cancelled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
