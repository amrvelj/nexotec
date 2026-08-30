"""SalesContract (WP-8 PR-1, S-D01/S-D06): contract = order for v1, no
separate Order entity. `offer_id` is nullable — the confirmed reference
prototype shows both a "Vertrag erzeugen" path (from an existing offer,
which denormalizes the offer's own number as lineage: "C-001195 ← O-003216")
and a direct "Vertrag erstellen" primary action on a stock item's detail
header with no prior offer at all.

`offer_id` carries no DB FK by house convention (a cross-context id would
never have one; here it is same-context but still deliberately opaque,
since a contract's whole point is that it can outlive being "the offer
that became this contract" — same posture PRD-Sales v2 gives it).

`status` has FOUR values, not three — `invoiced` is a real value the
reference prototype's own grid shows ("Fakturiert"), but WP-8 emits no
code path that sets it: finance (WP-9+) is what will flip a contract to
`invoiced` on `finance.invoice.issued`, once that context exists. Declaring
the value now keeps the schema honest about what the confirmed UI actually
renders, without inventing a fake trigger for it.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ContractStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    INVOICED = "invoiced"


class SalesContract(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "sales_contract"

    contract_number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Opaque lineage, set once at creation, never repointed. Null for a
    # contract created directly (S-D01's "two linked entities" does not
    # require the link to exist).
    offer_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    offer_number: Mapped[str | None] = mapped_column(String(16), nullable=True)

    status: Mapped[ContractStatus] = mapped_column(
        SAEnum(ContractStatus, native_enum=False, length=16), nullable=False, default=ContractStatus.PENDING
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the customer context. No DB-level FK."
    )
    customer_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_denorm_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    stock_item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the inventory context (StockItem.id). No DB-level FK."
    )
    vehicle_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    gross_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # WP-8 PR-3 — copied from the offer at the moment of creation (like
    # gross_price above); entity-private, same posture as
    # SalesOffer.margin. A direct contract (no offer) has no pricing
    # build-up of its own yet, so this stays None.
    margin: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    cancelled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
