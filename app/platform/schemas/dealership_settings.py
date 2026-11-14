"""Commercial and fiscal defaults of a Betrieb (KAN-203, PRD "Commercial and
fiscal defaults"). Every field has a shipped value, so the read never has a
null a document would have to guess around; the PATCH is partial.
"""

import datetime as dt
import re
import uuid
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.core.schemas import CamelModel
from app.platform.models.dealership_settings import ChartOfAccountsVariant, PeriodLockPolicy
from app.platform.reference.schemas.patch import refuse_null_on_not_null_fields

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_FISCAL_START_RE = re.compile(r"^(\d{2})-(\d{2})$")
# Rappenrundung as Swiss invoices use it: to the Rappen, to 5 Rappen, to 10
# Rappen or to the franc. Anything else is a typo, not a policy.
_CASH_ROUNDINGS = (Decimal("0.01"), Decimal("0.05"), Decimal("0.10"), Decimal("1.00"))


def _validate_fiscal_year_start(value: str) -> str:
    match = _FISCAL_START_RE.match(value)
    if not match:
        raise ValueError("fiscalYearStart must be a day of the year as MM-DD, e.g. '01-01'.")
    month, day = int(match.group(1)), int(match.group(2))
    if (month, day) == (2, 29):
        raise ValueError("fiscalYearStart cannot be 29 February — the day does not exist every year.")
    try:
        dt.date(2001, month, day)  # 2001 is not a leap year
    except ValueError:
        raise ValueError(f"'{value}' is not a day of the year (MM-DD).") from None
    return value


class DealershipSettingsUpdate(CamelModel):
    currency: str | None = Field(default=None, pattern=_CURRENCY_RE.pattern)
    cash_rounding: Decimal | None = None
    default_payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    fiscal_year_start: str | None = None
    accounting_system: str | None = Field(default=None, max_length=50)
    mandant: str | None = Field(default=None, max_length=50)
    chart_of_accounts_variant: ChartOfAccountsVariant | None = None
    period_lock_policy: PeriodLockPolicy | None = None
    # KAN-37: the per-Betrieb switch for Google Places Autocomplete.
    address_autocomplete_enabled: bool | None = None

    @field_validator("cash_rounding")
    @classmethod
    def _cash_rounding_is_a_swiss_step(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value not in _CASH_ROUNDINGS:
            raise ValueError("cashRounding must be 0.01, 0.05, 0.10 or 1.00.")
        return value

    @field_validator("fiscal_year_start")
    @classmethod
    def _fiscal_year_start_is_a_day(cls, value: str | None) -> str | None:
        return None if value is None else _validate_fiscal_year_start(value)

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "DealershipSettingsUpdate":
        refuse_null_on_not_null_fields(self, nullable={"accounting_system", "mandant"})
        return self


class DealershipSettingsRead(CamelModel):
    dealership_id: uuid.UUID
    currency: str
    cash_rounding: Decimal
    default_payment_terms_days: int
    fiscal_year_start: str
    accounting_system: str | None
    mandant: str | None
    chart_of_accounts_variant: ChartOfAccountsVariant
    period_lock_policy: PeriodLockPolicy
    address_autocomplete_enabled: bool
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    updated_by: uuid.UUID | None


class AddressAutocompleteConfigRead(CamelModel):
    """What the SPA needs to offer Google Places Autocomplete on an address
    form (KAN-37, ADR-101): `enabled` holds only when the Betrieb's switch is
    on AND the platform holds a key; `browserKey` is that key, or null. The
    key is Nexotec's and public configuration: shipping it to a signed-in
    browser is its purpose, not a leak — what protects it is Google Cloud's
    restriction to our origins and to Places API (New), configured and
    verified at go-live (KAN-451). It is the one piece of configuration this
    read carries beyond the switch.
    """

    enabled: bool
    browser_key: str | None
