"""Number ranges (KAN-207; Dealer Administration PRD "Number ranges", D-A-17,
A-RULE-3, ADR-032, ADR-109).

One row per Betrieb (the legal entity, `tenant_id`) per document type: one
continuous sequence across all its sites (D-A-17) — `{location_code}` in the
pattern is a label, never a second counter. Platform holds the counter and
hands numbers out inside the calling module's own transaction (ADR-109, the
ruled exception to ADR-047), so a document write that fails consumes none.

`last_value` is the last number the range stands at (the current value the
screen shows read-only); `None` until the range hands out or continues its
first. `frozen_at` is set by the first number handed out (A-RULE-3; at
activation once FR-A-34 exists): from then on pattern, start value and yearly
reset change only by a platform admin, with a reason, audited.
"""

import datetime as dt
import enum

from sqlalchemy import Boolean, CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import UTCDateTime
from app.db import Base


class NumberRangeDocumentType(str, enum.Enum):
    """The eleven document types the PRD's table names. Customer numbers stay
    per group (`customer_number_sequence`) and are not here."""

    OFFER = "offer"
    ORDER_CONFIRMATION = "order_confirmation"
    SALES_CONTRACT = "sales_contract"
    PURCHASE_CONTRACT = "purchase_contract"
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DUNNING = "dunning"
    SERVICE_ORDER = "service_order"
    PARTS_ORDER = "parts_order"
    WORKSHOP_JOB = "workshop_job"
    STOCK_NUMBER = "stock_number"


class NumberRange(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "number_range"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_type", name="uq_number_range_tenant_id_document_type"),
        CheckConstraint("start_value >= 1", name="ck_number_range_start_value_positive"),
        CheckConstraint("last_value IS NULL OR last_value >= 0", name="ck_number_range_last_value_not_negative"),
    )

    document_type: Mapped[NumberRangeDocumentType] = mapped_column(
        StoredEnum(NumberRangeDocumentType, length=24), nullable=False
    )
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    start_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    yearly_reset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The Swiss year `last_value` was handed out in: a yearly reset starts
    # again at `start_value` in the first allocation of a new year.
    last_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frozen_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
