"""«Which labour rate applies here?» (KAN-806; D-A-19; slice 1, check 11).

The one question Aftersales asks about labour rates: the rate for category C
at location L on day D for invoice recipient R. The answer:

1. **an agreed rate first** — the contact R's agreement for C in force on D
   (the latest row from D or earlier that is not withdrawn; an `end` row
   means the agreement has ended);
2. **else the category rate** — C's rate at L in force on D;
3. **else no rate** — an answer, never an error and never a default
   (`found=False`). The caller decides what a missing rate means.

The answer names its source and the row, the amount per hour exactly as
typed, and whether it was typed net or with VAT; the other figure follows in
Aftersales with `app.core.money.Price` (platform carries no net amount,
ADR-212). An inactive category or location still answers: an order of last
month asks about last month. A position keeps what it was priced with
(ruling 178), so a rate written later changes no answer for an earlier day.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import PriceEntry
from app.platform.organisation.models.labour_rate import (
    LabourRate,
    LabourRateAgreement,
    LabourRateAgreementKind,
    LabourRateCategory,
)


class LabourRateSource(StrEnum):
    AGREEMENT = "agreement"
    CATEGORY_RATE = "category_rate"


class LabourRateMissing(StrEnum):
    # Neither an agreement nor a category rate on that day.
    NO_RATE = "no_rate"
    # The category is not one of this Betrieb's.
    UNKNOWN_CATEGORY = "unknown_category"


@dataclass(frozen=True)
class LabourRateAnswer:
    category_id: uuid.UUID
    found: bool
    source: LabourRateSource | None = None
    # labour_rate.id or labour_rate_agreement.id
    rate_id: uuid.UUID | None = None
    # Per hour, exactly as typed (ruling 183: never per work unit).
    amount: Decimal | None = None
    entered: PriceEntry | None = None
    valid_from: dt.date | None = None
    category_is_active: bool = False
    missing: LabourRateMissing | None = None


def labour_rate_for(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category_id: uuid.UUID,
    location_id: uuid.UUID,
    on: dt.date,
    invoice_recipient_contact_id: uuid.UUID | None,
) -> LabourRateAnswer:
    category = db.scalar(
        select(LabourRateCategory).where(
            LabourRateCategory.id == category_id, LabourRateCategory.dealership_id == dealership_id
        )
    )
    if category is None:
        return LabourRateAnswer(category_id=category_id, found=False, missing=LabourRateMissing.UNKNOWN_CATEGORY)

    if invoice_recipient_contact_id is not None:
        agreement = db.scalar(
            select(LabourRateAgreement)
            .where(
                LabourRateAgreement.dealership_id == dealership_id,
                LabourRateAgreement.contact_id == invoice_recipient_contact_id,
                LabourRateAgreement.category_id == category_id,
                LabourRateAgreement.valid_from <= on,
                LabourRateAgreement.withdrawn_at.is_(None),
            )
            .order_by(LabourRateAgreement.valid_from.desc())
            .limit(1)
        )
        if agreement is not None and agreement.kind is LabourRateAgreementKind.RATE:
            return LabourRateAnswer(
                category_id=category_id,
                found=True,
                source=LabourRateSource.AGREEMENT,
                rate_id=agreement.id,
                amount=agreement.amount,
                entered=agreement.entered,
                valid_from=agreement.valid_from,
                category_is_active=category.is_active,
            )

    rate = db.scalar(
        select(LabourRate)
        .where(
            LabourRate.dealership_id == dealership_id,
            LabourRate.category_id == category_id,
            LabourRate.location_id == location_id,
            LabourRate.valid_from <= on,
            LabourRate.withdrawn_at.is_(None),
        )
        .order_by(LabourRate.valid_from.desc())
        .limit(1)
    )
    if rate is None:
        return LabourRateAnswer(
            category_id=category_id,
            found=False,
            category_is_active=category.is_active,
            missing=LabourRateMissing.NO_RATE,
        )
    return LabourRateAnswer(
        category_id=category_id,
        found=True,
        source=LabourRateSource.CATEGORY_RATE,
        rate_id=rate.id,
        amount=rate.amount,
        entered=rate.entered,
        valid_from=rate.valid_from,
        category_is_active=category.is_active,
    )
