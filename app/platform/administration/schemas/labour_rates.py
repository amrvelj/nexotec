"""The labour-rates screen's API shapes (KAN-806; D-A-19)."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.money import PriceEntry
from app.core.schemas import CamelModel
from app.core.validators import DecimalStr
from app.platform.organisation.models.labour_rate import LabourRateAgreementKind
from app.platform.organisation.services.labour_rate_answer import LabourRateMissing, LabourRateSource
from app.platform.organisation.services.labour_rates import LabourRateStatus

# Per hour, up to four decimals (decision 23), above zero.
RateAmount = DecimalStr


class LabourRateRead(CamelModel):
    id: uuid.UUID
    location_id: uuid.UUID
    valid_from: dt.date
    # Per hour, exactly as typed; `entered` says whether net or with VAT (ruling 177).
    amount: Decimal
    entered: PriceEntry
    # As of `today` in the list: in_force, scheduled, superseded or withdrawn.
    status: LabourRateStatus
    withdrawn_at: dt.datetime | None
    version: int
    created_at: dt.datetime
    created_by: uuid.UUID | None


class LabourCategoryMemberRead(CamelModel):
    user_id: uuid.UUID
    display_name: str
    # False: the user can no longer work in the business; the allocation is kept.
    is_active: bool


class LabourRateCategoryRead(CamelModel):
    id: uuid.UUID
    name: str
    # Only the languages translated (ruling 176).
    name_translations: dict[str, str]
    is_active: bool
    version: int
    created_at: dt.datetime
    rates: list[LabourRateRead]
    members: list[LabourCategoryMemberRead]


class LabourRateCategoryList(CamelModel):
    # The business's today (Europe/Zurich): what `status` is computed for, and
    # the earliest date a new rate may start (ruling 178).
    today: dt.date
    items: list[LabourRateCategoryRead]


class LabourRateCategoryCreate(CamelModel):
    name: str = Field(min_length=1, max_length=100)
    name_translations: dict[str, str] | None = None


class LabourRateCategoryUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    name_translations: dict[str, str] | None = None
    is_active: bool | None = None


class LabourRateCreate(CamelModel):
    location_id: uuid.UUID
    valid_from: dt.date
    amount: RateAmount = Field(gt=0, max_digits=12, decimal_places=4)
    # What the screen showed the manager: O16 at the location. Checked, never relabelled.
    entered: PriceEntry


class LabourCategoryMemberCreate(CamelModel):
    user_id: uuid.UUID


class LabourRateAgreementRead(CamelModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    contact_label: str
    category_id: uuid.UUID
    # `rate`: a fixed rate from `validFrom`; `end`: the agreement ends from `validFrom`.
    kind: LabourRateAgreementKind
    valid_from: dt.date
    amount: Decimal | None
    entered: PriceEntry | None
    status: LabourRateStatus
    withdrawn_at: dt.datetime | None
    version: int
    created_at: dt.datetime


class LabourRateAgreementPage(CamelModel):
    today: dt.date
    items: list[LabourRateAgreementRead]
    next_cursor: str | None


class LabourRateAgreementCreate(CamelModel):
    contact_id: uuid.UUID
    category_id: uuid.UUID
    kind: LabourRateAgreementKind = LabourRateAgreementKind.RATE
    valid_from: dt.date
    amount: RateAmount | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    entered: PriceEntry | None = None


class LabourRateAnswerRead(CamelModel):
    """«Which rate applies here» — what Aftersales gets through platform.public."""

    category_id: uuid.UUID
    found: bool
    source: LabourRateSource | None
    rate_id: uuid.UUID | None
    amount: Decimal | None
    entered: PriceEntry | None
    valid_from: dt.date | None
    category_is_active: bool
    missing: LabourRateMissing | None
