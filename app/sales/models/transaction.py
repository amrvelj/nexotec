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
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class TransactionType(str, enum.Enum):
    SALE = "sale"
    TRADE_IN = "trade_in"


class TransactionStatus(str, enum.Enum):
    DRAFT = "draft"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Transaction(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "transaction"

    # Overrides TenantScopedMixin's bare column — no DB-level FK to dealer.id
    # (PR-2, ADR-015). Owned by the platform context; reconciled nightly.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Dealer). No DB-level FK."
    )

    transaction_type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, native_enum=False, length=16), nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, native_enum=False, length=16), nullable=False, default=TransactionStatus.DRAFT
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the customer context. No DB-level FK (PR-2, ADR-015)."
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the vehicle context. No DB-level FK (PR-2, ADR-015)."
    )
    # Employee of record — no DB-level FK to user.id (PR-2, ADR-015); tenant
    # match is enforced at the service layer, same as before.
    primary_user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (User). No DB-level FK."
    )

    # Nullable in draft; required before `completed` (enforced at the
    # service layer, not a DB constraint — same convention as other
    # cross-field business rules in this codebase).
    amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # Set on completion, not creation (spec §4 Fields).
    transaction_date: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
