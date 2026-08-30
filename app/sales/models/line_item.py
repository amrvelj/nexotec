"""SalesLineItem (WP-8 PR-3, extended PR-8) — one table with a `kind`
discriminator for BOTH factory options and accessories (S-D14: accessories
are an offer-level collection, not vehicle data), rather than two
near-identical tables. Factory-option rows are created once, by
`services/snapshot.py::freeze_vehicle_snapshot`, from the frozen vehicle
snapshot's own option list — never hand-typed. Accessory rows (offer-level,
added by the seller) are PR-8 scope; the table exists from PR-3 on so
`pricing.build_up()`'s accessories_total has something real to sum over
the moment PR-8 starts writing them, rather than a second migration.

`discount_type`/`discount_value`/`discount_resolved_amount`/
`discount_suppressed_reason` (per-line discount, "individually
deselectable... suppressed-with-reason on used cars") are also PR-8
fields, declared now for the same reason.

Exactly one of `offer_id`/`contract_id` is set, enforced at the service
layer (the house convention for cross-field rules — see
app.customer.models.customer's own docstring on why this isn't a DB
CHECK), never both, never neither.
"""

import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.types import GUID
from app.db import Base


class LineItemKind(str, enum.Enum):
    FACTORY_OPTION = "factory_option"
    ACCESSORY = "accessory"


class SalesLineItem(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "sales_line_item"

    offer_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)

    kind: Mapped[LineItemKind] = mapped_column(SAEnum(LineItemKind, native_enum=False, length=16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # A factory option deselected without being deleted (PR-8) — its price
    # stops counting toward options_total but the row (and the fact it was
    # once offered) stays visible.
    included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    discount_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "percent" | "amount"
    discount_value: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    discount_resolved_amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # PR-8 — a used car's own factory options are suppressed-with-reason
    # rather than hidden, mirroring app.inventory.services.pricing's
    # ITEMIZABLE_CONDITIONS rule on the stock side.
    discount_suppressed_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
