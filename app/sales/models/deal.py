"""SalesDeal — the ADR-060 grid-facing read model, and `SalesNumberSequence`
— the per-tenant, per-series number allocator shared by offers and
contracts.

Why a materialized table rather than `UNION ALL sales_offer, sales_contract`
at query time: `app.core.pagination.paginate_query_sorted` takes a single
`model: type[ModelT]` and reaches for `model.id`/`model.created_at` — a
union subquery is not a model; U-03's "a column with no index is not
shipped as sortable" is unachievable over a union; and derived columns
(here: margin, once PR-3 lands) would have to be recomputed per row per
page. `sales_deal` is instead written SYNCHRONOUSLY, in the same local
transaction as every offer/contract mutation, through exactly one function
— `app.sales.services.deal_projection.upsert_deal_projection` — so there is
no ADR-047 question and no eventual-consistency window on a grid the
seller just wrote to.

`id` is deliberately NOT a fresh UUIDv7 on every row: it equals whichever
of `offer_id`/`contract_id` is this deal's *stable* identity — the
confirmed reference prototype's own grid shows exactly one row per deal
lineage, never two, once "Vertrag erzeugen" turns an offer into a contract
(the offer's own number simply stops appearing in the grid; it lives on
only as `offer_number` lineage on the contract's own detail header). So:
a deal that originated as a bare offer keys on `offer.id`; the moment a
contract is created FROM that offer, the SAME row is updated in place
(`entity_type` flips to "contract", `number` becomes the contract number)
— never a second row. A contract created directly, with no prior offer,
keys on its own `contract.id`.

Deliberately has NO group-read endpoint anywhere (contrast
app.inventory's ADR-055 group listing, which is a second, hand-authored
schema over the SAME table) — margin, discount and trade-in purchase price
stay private to the legal entity (ADR-029/ADR-049) by there being no
cross-tenant reader of this table at all, not by remembering to exclude
columns from one.

Columns grow additively across PR-3/5/6/7 as the underlying capability
lands (margin in PR-3, trade-in fields in PR-5, financing/signed_at in
PR-6, invoice_ref/documents_count in PR-7) — this is the expected shape of
a materialized read model, not schema churn.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class SalesNumberSequence(Base):
    """Row-lock allocator, one row per (tenant_id, series) — mirrors
    app.inventory's per-tenant StockNumberSequence, with an added `series`
    column since sales allocates two independent number spaces
    ("offer" -> O-000001, "contract" -> C-000001) rather than one.
    """

    __tablename__ = "sales_number_sequence"

    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    series: Mapped[str] = mapped_column(String(16), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SalesDeal(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "sales_deal"

    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "offer" | "contract"
    number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    offer_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    offer_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    contract_number: Mapped[str | None] = mapped_column(String(16), nullable=True)

    customer_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    customer_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_denorm_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    vehicle_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    gross_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # Entity-private (ADR-029/ADR-049) — never exposed via any group-scoped
    # endpoint, enforced structurally by there being none (see module
    # docstring), not by column filtering.
    margin: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    documents_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
