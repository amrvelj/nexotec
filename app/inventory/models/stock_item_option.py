"""Factory options — one list, two consumers (WP-7 PR-9, FR-I-22).
`pricing.options[]` + `basePrice` drives both the Sales offer price
build-up AND the marketplace's own equipmentCode via `equipment_code` —
one source, so "a car cannot be advertised with equipment it is not
priced with." Itemised only where condition is new/tagesz/demo — never
on a used car, to avoid inviting per-line discount negotiation.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class StockItemOption(PrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "stock_item_option"

    stock_item_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("stock_item.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    # Feeds the marketplace equipment codes (ADR-062) directly — null for
    # an option with no marketplace-searchable equivalent.
    equipment_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
