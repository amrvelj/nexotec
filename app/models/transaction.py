"""Transaction: the connective master record linking Customer + Vehicle +
User + Dealer. MDM owns the header/identity only — deal structure, RO line
items, and financing terms live in owning modules (Sales, Aftersales,
Finance) that reference `transaction_id`.

Shell scope only (Swiss addendum Round 3): `transaction_type` limited to
[sale, trade_in] — lease/service_order/test_drive/quote from the original
spec table are descoped, not modeled. `status` limited to
[draft, completed, cancelled] — no `pending`, no `void`.

Tenant-owned (unlike Vehicle): `tenant_id` is the Dealer executing the
transaction.
"""

import datetime as dt
import enum
from decimal import Decimal

from sqlalchemy import DECIMAL, Enum as SAEnum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.models.types import GUID, UTCDateTime


class TransactionType(str, enum.Enum):
    SALE = "sale"
    TRADE_IN = "trade_in"


class TransactionStatus(str, enum.Enum):
    DRAFT = "draft"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Transaction(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "transaction"

    # Overrides TenantScopedMixin's bare column to add the FK to dealer.id,
    # same as User/Customer.
    tenant_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("dealer.id"), nullable=False, index=True)

    transaction_type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, native_enum=False, length=16), nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, native_enum=False, length=16), nullable=False, default=TransactionStatus.DRAFT
    )
    customer_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("customer.id"), nullable=False, index=True)
    vehicle_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("vehicle.id"), nullable=False, index=True)
    # Employee of record — FK to user.id, not further tenant-checked at the
    # DB level (enforced at the service layer: must belong to the same
    # tenant as the transaction).
    primary_user_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)

    # Nullable in draft; required before `completed` (enforced at the
    # service layer, not a DB constraint — same convention as other
    # cross-field business rules in this codebase).
    amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # Set on completion, not creation (spec §4 Fields).
    transaction_date: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
