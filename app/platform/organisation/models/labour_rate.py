"""Labour rates (KAN-806; Dealer Administration D-A-19; Aftersales slice 1,
section 5; Anto's rulings 177 and 178).

A Betrieb's own master data — it describes the Betrieb, not the work
(A-RULE-0), so Platform keeps it and Aftersales only asks «which rate
applies here» (`services/labour_rate_answer.py`):

- `labour_rate_category`: the Betrieb's categories (Mechanik, Karosserie …),
  dealer-defined. A name typed once with optional translations (ruling 176).
  Never deleted; deactivated.
- `labour_rate`: the rate of a category at a location **from a date**, per
  hour, exactly as typed, with whether it was typed net or with VAT
  (`entered`, ruling 177 — the Betrieb's switch O16 at entry). A rate runs
  until the next rate of its category and location starts.
- `labour_rate_agreement`: a fixed rate agreed with a contact (an insurer,
  an importer, a fleet) for a category, Betrieb-wide, from a date; it comes
  first for that contact as invoice recipient. An `end` row stops an
  agreement from its date.

**A rate in use is never changed (ruling 178).** Rate and agreement rows are
written once: a new price is a new row from a date today or later. A row
that has not started yet may be withdrawn (`withdrawn_at`); once its date has
come it stays. That is why the rows are versioned: the withdrawal takes
If-Match. A withdrawn row is kept, so an id a priced position names never
points at nothing.

The amount is **per hour, always** — work units are a way of showing time,
never a price basis (ruling 183, ADR-215 (4)). No net or gross counterpart
is stored: the other figure follows from the VAT rate where it is shown
(`app.core.money.Price`), in Aftersales.

Every table references `dealership`, `location` or another of these, which
platform owns, so the foreign keys are real — except the contact, which
contact owns: the three-column pattern (rule 2).
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.money import PriceEntry
from app.core.types import GUID, UTCDateTime
from app.db import Base

# Up to four decimals per unit, like any unit price (decision 23).
AMOUNT_TYPE = Numeric(12, 4)


class LabourRateAgreementKind(enum.StrEnum):
    # A fixed rate from `valid_from`.
    RATE = "rate"
    # The agreement ends from `valid_from`: the category rate applies again.
    END = "end"


class LabourRateCategory(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "labour_rate_category"
    __table_args__ = (
        # The target of the composite foreign keys below: a rate, an agreement and
        # an allocation can only name a category of their own Betrieb.
        UniqueConstraint("id", "dealership_id", name="uq_labour_rate_category_id_dealership_id"),
        Index("ix_labour_rate_category_dealership_id", "dealership_id"),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # {"fr": …, "it": …} — only the languages translated (ruling 176).
    name_translations: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LabourRate(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "labour_rate"
    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "dealership_id"],
            ["labour_rate_category.id", "labour_rate_category.dealership_id"],
            name="fk_labour_rate_category_dealership",
        ),
        CheckConstraint("amount > 0", name="ck_labour_rate_amount_positive"),
        Index(
            "uq_labour_rate_category_location_valid_from",
            "category_id",
            "location_id",
            "valid_from",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL"),
            sqlite_where=text("withdrawn_at IS NULL"),
        ),
        Index("ix_labour_rate_dealership_id", "dealership_id"),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("location.id"), nullable=False)
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # Per hour, as typed (ruling 183: never per work unit).
    amount: Mapped[Decimal] = mapped_column(AMOUNT_TYPE, nullable=False)
    # Typed net or with VAT (ruling 177): O16 at the time it was typed.
    entered: Mapped[PriceEntry] = mapped_column(StoredEnum(PriceEntry, length=8), nullable=False)
    withdrawn_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class LabourRateAgreement(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "labour_rate_agreement"
    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "dealership_id"],
            ["labour_rate_category.id", "labour_rate_category.dealership_id"],
            name="fk_labour_rate_agreement_category_dealership",
        ),
        CheckConstraint(
            "(kind = 'rate' AND amount > 0 AND entered IS NOT NULL) "
            "OR (kind = 'end' AND amount IS NULL AND entered IS NULL)",
            name="ck_labour_rate_agreement_kind_amount",
        ),
        Index(
            "uq_labour_rate_agreement_contact_category_valid_from",
            "dealership_id",
            "contact_id",
            "category_id",
            "valid_from",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL"),
            sqlite_where=text("withdrawn_at IS NULL"),
        ),
        Index("ix_labour_rate_agreement_contact_id", "contact_id"),
        Index("ix_labour_rate_agreement_dealership_id", "dealership_id"),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by contact (group-scoped, ADR-014); no FK (rule 2)."
    )
    contact_label: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    kind: Mapped[LabourRateAgreementKind] = mapped_column(StoredEnum(LabourRateAgreementKind, length=8), nullable=False)
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(AMOUNT_TYPE, nullable=True)
    entered: Mapped[PriceEntry | None] = mapped_column(StoredEnum(PriceEntry, length=8), nullable=True)
    withdrawn_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # KAN-831 (ADR-232): a contact merge found the survivor holding an agreement
    # of this category in the Betrieb, so this row of the duplicate stays on the
    # duplicate's id and is set aside — never an answer of labour_rate_for (Anto,
    # option A: the survivor's agreement survives). Written once (ruling 178).
    set_aside_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
