"""dealership_settings (KAN-203, PRD "Commercial and fiscal defaults"): the
defaults documents and modules inherit, one row per dealership, written
with the dealership (`create_dealership`) and backfilled for every existing
one by the KAN-203 migration — so a brand-new Betrieb is operable before
anyone opens the Business screen (FR-A-25).

The shipped values are neutral and Swiss: CHF, Rappenrundung 0.05, 30 days
net, fiscal year from 1 January, the KMU chart of accounts, periods locked
after month-end close. Nothing commercial is set by Nexotec — no VAT rate
(it stays `dealership.vat_rate`, ADR-057's one figure; its semantics are
not this table's) and no bank details (`dealership_bank_account`).

There is no `vat_method` (D-A-27): the effective method is the only one in
v1; Saldosteuersatz, if it ever comes, is a tax-calculation change in
Finance, not a field here. `accounting_system` and `mandant` are the
identifiers the Finance export writes into; the credential behind them is
an integration connection, never a column here.
"""

import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, ForeignKey, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID
from app.db import Base


class ChartOfAccountsVariant(str, enum.Enum):
    KMU = "kmu"  # Schweizer Kontenrahmen KMU — the shipped default
    KAEFER = "kaefer"  # Kontenrahmen Käfer, the older standard still in use


class PeriodLockPolicy(str, enum.Enum):
    MONTH_END_CLOSE = "month_end_close"  # shipped default (ADR-032)
    QUARTER_END_CLOSE = "quarter_end_close"
    YEAR_END_CLOSE = "year_end_close"


SHIPPED_CURRENCY = "CHF"
SHIPPED_CASH_ROUNDING = Decimal("0.05")
SHIPPED_PAYMENT_TERMS_DAYS = 30
SHIPPED_FISCAL_YEAR_START = "01-01"  # MM-DD
# KAN-37 (ADR-101; Anto, 2026-10-25): address autocomplete is on for every
# Betrieb until its manager switches it off — the one non-fiscal setting
# here, because this is the row the Business screen edits (A-RULE-0).
SHIPPED_ADDRESS_AUTOCOMPLETE_ENABLED = True


class DealershipSettings(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "dealership_settings"

    dealership_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("dealership.id"), nullable=False, unique=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default=SHIPPED_CURRENCY)
    cash_rounding: Mapped[Decimal] = mapped_column(DECIMAL(4, 2), nullable=False, default=SHIPPED_CASH_ROUNDING)
    default_payment_terms_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=SHIPPED_PAYMENT_TERMS_DAYS
    )
    # MM-DD — a day in the year, not a date; validated by the schema.
    fiscal_year_start: Mapped[str] = mapped_column(String(5), nullable=False, default=SHIPPED_FISCAL_YEAR_START)
    accounting_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mandant: Mapped[str | None] = mapped_column(String(50), nullable=True)
    chart_of_accounts_variant: Mapped[ChartOfAccountsVariant] = mapped_column(
        StoredEnum(ChartOfAccountsVariant, length=16), nullable=False, default=ChartOfAccountsVariant.KMU
    )
    period_lock_policy: Mapped[PeriodLockPolicy] = mapped_column(
        StoredEnum(PeriodLockPolicy, length=24), nullable=False, default=PeriodLockPolicy.MONTH_END_CLOSE
    )
    # KAN-37: whether the Betrieb's address forms offer Google Places
    # Autocomplete. The switch alone — the key is platform configuration
    # (`Settings.google_places_browser_key`), never a column. The server
    # default stays (as `user.commission_relevant`'s does): the KAN-203
    # backfill's INSERT names its columns and is immutable, so a NOT NULL
    # column added after it needs a default the database supplies.
    address_autocomplete_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=SHIPPED_ADDRESS_AUTOCOMPLETE_ENABLED, server_default=true()
    )
