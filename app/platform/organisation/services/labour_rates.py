"""Labour rates: a Betrieb's categories, rates per location from a date, and
fixed rates agreed with a contact (KAN-806; D-A-19; rulings 177 and 178).

**Ruling 178 — a rate in use is never changed.** Rates and agreements are
written once. A new price is a new row from a date, today or later
(`labour_rate_starts_in_the_past`); there is no edit. A row whose date has
not come yet may be withdrawn — a mistyped rate for next month — and once its
date has come it is in use and stays, even on its first day (Anto,
2026-11-25: in use from its start date). A withdrawn row is kept and marked,
so an id that a priced position names never points at nothing.

**Ruling 177 — net or with VAT.** The Betrieb's switch O16 (at the rate's
location; for an agreement, the Betrieb's own, Anto 2026-11-25: agreements
follow O16) says how rates are typed. While it is not set no rate can be
entered (`labour_rates_entry_basis_not_set`). The client says what the
screen showed (`entered`); if O16 changed meanwhile the write is refused
(`labour_rate_entry_basis_changed`) rather than the amount relabelled. The
row keeps how it was typed, so changing O16 later changes no rate.

The caller checks the role and the Betrieb (the API: `labour_rates` matrix
row, tenant match, the `aftersales` module) and commits. Every write is
audited. No outbox event: nothing consumes one yet, and Aftersales asks
`platform.public.labour_rate_for` in one hop (ADR-216 (8) is the precedent).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.money import UNIT_PRICE_PLACES, PriceEntry
from app.core.relative_dates import today_in_session_zone
from app.core.text_collation import TextSortKey
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.labour_rate import (
    LabourRate,
    LabourRateAgreement,
    LabourRateAgreementKind,
    LabourRateCategory,
)
from app.platform.organisation.services.dealership import get_dealership_for_update_or_404
from app.platform.organisation.services.labour_rate_settings import RATES_ENTERED_KEY, rates_entered

NAME_MAX_LENGTH = 100
LANGUAGES = ("de", "fr", "it", "en")
# labour_rate.amount is Numeric(12, 4): eight digits before the point.
_AMOUNT_LIMIT = Decimal("1E8")


class LabourRateStatus(StrEnum):
    """A rate row as of a day: the one in force, one still to come, one a
    later rate replaced, or one withdrawn before it started."""

    IN_FORCE = "in_force"
    SCHEDULED = "scheduled"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


def today() -> dt.date:
    return today_in_session_zone()[0]


# --- categories ---------------------------------------------------------------------


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > NAME_MAX_LENGTH:
        raise UnprocessableEntityError(
            f"A labour category's name has 1 to {NAME_MAX_LENGTH} characters.",
            details={"reason": "labour_rate_category_name_length"},
        )
    return cleaned


def _clean_translations(translations: dict[str, str] | None) -> dict[str, str] | None:
    """Only the languages given (ruling 176); an empty one is dropped."""

    if not translations:
        return None
    cleaned: dict[str, str] = {}
    for language, text in sorted(translations.items()):
        if language not in LANGUAGES:
            raise UnprocessableEntityError(
                f"{language!r} is no language of Nexotec.", details={"reason": "labour_rate_category_name_language"}
            )
        text = " ".join((text or "").split())
        if len(text) > NAME_MAX_LENGTH:
            raise UnprocessableEntityError(
                f"A labour category's name has 1 to {NAME_MAX_LENGTH} characters.",
                details={"reason": "labour_rate_category_name_length"},
            )
        if text:
            cleaned[language] = text
    return cleaned or None


def _name_taken(db: Session, dealership_id: uuid.UUID, name: str, *, other_than: uuid.UUID | None = None) -> bool:
    stmt = select(LabourRateCategory.id).where(
        LabourRateCategory.dealership_id == dealership_id,
        func.lower(LabourRateCategory.name) == name.lower(),
    )
    if other_than is not None:
        stmt = stmt.where(LabourRateCategory.id != other_than)
    return db.scalar(stmt.limit(1)) is not None


def list_categories(
    db: Session, *, dealership_id: uuid.UUID, include_inactive: bool = True
) -> list[LabourRateCategory]:
    stmt = select(LabourRateCategory).where(LabourRateCategory.dealership_id == dealership_id)
    if not include_inactive:
        stmt = stmt.where(LabourRateCategory.is_active.is_(True))
    return list(db.scalars(stmt.order_by(TextSortKey(LabourRateCategory.name), LabourRateCategory.id)))


def get_category_or_404(
    db: Session, *, dealership_id: uuid.UUID, category_id: uuid.UUID, lock: bool = False
) -> LabourRateCategory:
    stmt = select(LabourRateCategory).where(
        LabourRateCategory.id == category_id, LabourRateCategory.dealership_id == dealership_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    category = db.scalar(stmt)
    if category is None:
        raise NotFoundError("Labour category not found.")
    return category


def _category_audit(category: LabourRateCategory) -> dict:
    return {"name": category.name, "nameTranslations": category.name_translations, "isActive": category.is_active}


def create_category(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    name: str,
    name_translations: dict[str, str] | None,
    actor_id: uuid.UUID | None,
) -> LabourRateCategory:
    cleaned = _clean_name(name)
    translations = _clean_translations(name_translations)
    # The Betrieb's row, so two categories of one name cannot be created at once.
    get_dealership_for_update_or_404(db, dealership_id)
    if _name_taken(db, dealership_id, cleaned):
        raise ConflictError(
            "The business already has a labour category of this name.",
            details={"reason": "labour_rate_category_name_taken"},
        )
    category = LabourRateCategory(
        dealership_id=dealership_id,
        name=cleaned,
        name_translations=translations,
        is_active=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(category)
    db.flush()
    record_audit_event(
        db,
        entity_type="labour_rate_category",
        entity_id=category.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_category_audit(category),
    )
    return category


_UNCHANGED = object()


def change_category(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
    name: str | None = None,
    name_translations: object = _UNCHANGED,
    is_active: bool | None = None,
) -> LabourRateCategory:
    """Renames, translates, deactivates or reactivates a category. Its rates
    and agreements stay as they are: a name is not a price."""

    get_dealership_for_update_or_404(db, dealership_id)
    category = get_category_or_404(db, dealership_id=dealership_id, category_id=category_id, lock=True)
    check_version(category.version, expected_version, entity_name="LabourRateCategory")
    before = _category_audit(category)
    if name is not None:
        cleaned = _clean_name(name)
        if _name_taken(db, dealership_id, cleaned, other_than=category.id):
            raise ConflictError(
                "The business already has a labour category of this name.",
                details={"reason": "labour_rate_category_name_taken"},
            )
        category.name = cleaned
    if name_translations is not _UNCHANGED:
        category.name_translations = _clean_translations(name_translations)  # type: ignore[arg-type]
    if is_active is not None:
        category.is_active = is_active
    after = _category_audit(category)
    if after == before:
        return category
    category.version += 1
    category.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type="labour_rate_category",
        entity_id=category.id,
        tenant_id=dealership_id,
        action="update",
        actor_id=actor_id,
        before=before,
        after=after,
    )
    return category


# --- what is typed ------------------------------------------------------------------


def _check_amount(amount: Decimal) -> Decimal:
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0 or amount.copy_abs() >= _AMOUNT_LIMIT:
        raise UnprocessableEntityError(
            "A labour rate is an amount above zero.", details={"reason": "labour_rate_amount_invalid"}
        )
    exponent = amount.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > UNIT_PRICE_PLACES:
        raise UnprocessableEntityError(
            f"A labour rate has at most {UNIT_PRICE_PLACES} decimals.", details={"reason": "labour_rate_amount_invalid"}
        )
    return amount


def _check_starts(valid_from: dt.date, on: dt.date) -> None:
    if valid_from < on:
        raise UnprocessableEntityError(
            "A new labour rate starts today or later; a rate in use is never changed.",
            details={"reason": "labour_rate_starts_in_the_past", "today": on.isoformat()},
        )


def _check_entered(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, entered: PriceEntry
) -> None:
    basis = rates_entered(db, dealership_id=dealership_id, location_id=location_id)
    if basis is None:
        raise ConflictError(
            "Set first whether the business types labour rates net or with VAT.",
            details={"reason": "labour_rates_entry_basis_not_set", "setting": RATES_ENTERED_KEY},
        )
    if basis is not entered:
        raise ConflictError(
            "The business now types labour rates differently; reload and type the rate again.",
            details={"reason": "labour_rate_entry_basis_changed", "entered": basis.value},
        )


# --- rates --------------------------------------------------------------------------


def rates_of(db: Session, *, dealership_id: uuid.UUID, category_ids: list[uuid.UUID] | None = None) -> list[LabourRate]:
    """Every rate row of the Betrieb (or of these categories), newest start first."""

    stmt = select(LabourRate).where(LabourRate.dealership_id == dealership_id)
    if category_ids is not None:
        stmt = stmt.where(LabourRate.category_id.in_(category_ids))
    return list(db.scalars(stmt.order_by(LabourRate.valid_from.desc(), LabourRate.created_at.desc(), LabourRate.id)))


def rate_statuses(rows: list[LabourRate], on: dt.date) -> dict[uuid.UUID, LabourRateStatus]:
    """Each row's status on `on`, per category and location."""

    statuses: dict[uuid.UUID, LabourRateStatus] = {}
    in_force_seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for row in sorted(rows, key=lambda r: r.valid_from, reverse=True):
        if row.withdrawn_at is not None:
            statuses[row.id] = LabourRateStatus.WITHDRAWN
        elif row.valid_from > on:
            statuses[row.id] = LabourRateStatus.SCHEDULED
        elif (row.category_id, row.location_id) in in_force_seen:
            statuses[row.id] = LabourRateStatus.SUPERSEDED
        else:
            statuses[row.id] = LabourRateStatus.IN_FORCE
            in_force_seen.add((row.category_id, row.location_id))
    return statuses


def _rate_audit(rate: LabourRate) -> dict:
    return {
        "categoryId": str(rate.category_id),
        "locationId": str(rate.location_id),
        "validFrom": rate.valid_from.isoformat(),
        "amount": str(rate.amount),
        "entered": rate.entered.value,
        "withdrawnAt": rate.withdrawn_at.isoformat() if rate.withdrawn_at else None,
    }


def create_rate(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    category_id: uuid.UUID,
    location_id: uuid.UUID,
    valid_from: dt.date,
    amount: Decimal,
    entered: PriceEntry,
    actor_id: uuid.UUID | None,
    on: dt.date | None = None,
) -> LabourRate:
    """A category's rate at a location from `valid_from` (today or later)."""

    on = on or today()
    amount = _check_amount(amount)
    _check_starts(valid_from, on)
    # The category's row serialises the rates of a category: the duplicate check below
    # cannot race another writer.
    category = get_category_or_404(db, dealership_id=dealership_id, category_id=category_id, lock=True)
    if not category.is_active:
        raise ConflictError("The labour category is deactivated.", details={"reason": "labour_rate_category_inactive"})
    location = db.scalar(select(Location).where(Location.id == location_id, Location.tenant_id == dealership_id))
    if location is None:
        raise NotFoundError("Location not found.")
    if not location.is_active:
        raise ConflictError(
            "A deactivated location gets no new labour rate.", details={"reason": "labour_rate_location_inactive"}
        )
    _check_entered(db, dealership_id=dealership_id, location_id=location_id, entered=entered)
    taken = db.scalar(
        select(LabourRate.id).where(
            LabourRate.category_id == category_id,
            LabourRate.location_id == location_id,
            LabourRate.valid_from == valid_from,
            LabourRate.withdrawn_at.is_(None),
        )
    )
    if taken is not None:
        raise ConflictError(
            "This category already has a rate from that date at this location.",
            details={"reason": "labour_rate_exists_on_date", "rateId": str(taken)},
        )
    rate = LabourRate(
        dealership_id=dealership_id,
        category_id=category_id,
        location_id=location_id,
        valid_from=valid_from,
        amount=amount,
        entered=entered,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(rate)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise ConflictError(
            "This category already has a rate from that date at this location.",
            details={"reason": "labour_rate_exists_on_date"},
        )
    record_audit_event(
        db,
        entity_type="labour_rate",
        entity_id=rate.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_rate_audit(rate),
    )
    return rate


def withdraw_rate(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    rate_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
    on: dt.date | None = None,
) -> LabourRate:
    """Withdraws a rate that has not started yet. One whose date has come is
    in use and stays (ruling 178)."""

    on = on or today()
    rate = db.scalar(
        select(LabourRate)
        .where(LabourRate.id == rate_id, LabourRate.dealership_id == dealership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rate is None:
        raise NotFoundError("Labour rate not found.")
    check_version(rate.version, expected_version, entity_name="LabourRate")
    if rate.withdrawn_at is not None:
        raise ConflictError(
            "The labour rate is already withdrawn.", details={"reason": "labour_rate_already_withdrawn"}
        )
    if rate.valid_from <= on:
        raise ConflictError(
            "A labour rate in use is never changed; enter a new rate from a later date.",
            details={"reason": "labour_rate_in_use"},
        )
    before = _rate_audit(rate)
    rate.withdrawn_at = utcnow()
    rate.version += 1
    rate.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type="labour_rate",
        entity_id=rate.id,
        tenant_id=dealership_id,
        action="withdraw",
        actor_id=actor_id,
        before=before,
        after=_rate_audit(rate),
    )
    return rate


# --- agreements ---------------------------------------------------------------------


@dataclass(frozen=True)
class AgreementContact:
    """The contact of an agreement as the composition root resolved it: its
    id and label. Platform never reads contact itself (ADR-131)."""

    id: uuid.UUID
    label: str


def _agreement_audit(row: LabourRateAgreement) -> dict:
    return {
        "contactId": str(row.contact_id),
        "categoryId": str(row.category_id),
        "kind": row.kind.value,
        "validFrom": row.valid_from.isoformat(),
        "amount": str(row.amount) if row.amount is not None else None,
        "entered": row.entered.value if row.entered else None,
        "withdrawnAt": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
    }


def agreement_statuses(rows: list[LabourRateAgreement], on: dt.date) -> dict[uuid.UUID, LabourRateStatus]:
    """Each row's status on `on`, per contact and category (an end row that
    is in force means the agreement has ended)."""

    statuses: dict[uuid.UUID, LabourRateStatus] = {}
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for row in sorted(rows, key=lambda r: r.valid_from, reverse=True):
        if row.withdrawn_at is not None:
            statuses[row.id] = LabourRateStatus.WITHDRAWN
        elif row.valid_from > on:
            statuses[row.id] = LabourRateStatus.SCHEDULED
        elif (row.contact_id, row.category_id) in seen:
            statuses[row.id] = LabourRateStatus.SUPERSEDED
        else:
            statuses[row.id] = LabourRateStatus.IN_FORCE
            seen.add((row.contact_id, row.category_id))
    return statuses


def create_agreement(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    contact: AgreementContact,
    category_id: uuid.UUID,
    kind: LabourRateAgreementKind,
    valid_from: dt.date,
    amount: Decimal | None,
    entered: PriceEntry | None,
    actor_id: uuid.UUID | None,
    on: dt.date | None = None,
) -> LabourRateAgreement:
    """A fixed rate agreed with a contact for a category, Betrieb-wide, from
    `valid_from` (today or later); or, with kind `end`, the end of the one in
    force or scheduled before that date. The caller resolved the contact in
    the Betrieb's group (contact is above platform)."""

    on = on or today()
    _check_starts(valid_from, on)
    if kind is LabourRateAgreementKind.RATE:
        if amount is None or entered is None:
            raise UnprocessableEntityError(
                "An agreed rate needs its amount.", details={"reason": "labour_rate_amount_invalid"}
            )
        amount = _check_amount(amount)
    elif amount is not None or entered is not None:
        raise UnprocessableEntityError(
            "The end of an agreement has no amount.", details={"reason": "labour_rate_agreement_end_has_no_amount"}
        )
    category = get_category_or_404(db, dealership_id=dealership_id, category_id=category_id, lock=True)
    if kind is LabourRateAgreementKind.RATE and not category.is_active:
        raise ConflictError("The labour category is deactivated.", details={"reason": "labour_rate_category_inactive"})
    if entered is not None:
        _check_entered(db, dealership_id=dealership_id, location_id=None, entered=entered)
    rows = list(
        db.scalars(
            select(LabourRateAgreement).where(
                LabourRateAgreement.dealership_id == dealership_id,
                LabourRateAgreement.contact_id == contact.id,
                LabourRateAgreement.category_id == category_id,
                LabourRateAgreement.withdrawn_at.is_(None),
            )
        )
    )
    same_day = next((row for row in rows if row.valid_from == valid_from), None)
    if same_day is not None:
        raise ConflictError(
            "An agreement of this contact and category already starts or ends on that date.",
            details={"reason": "labour_rate_agreement_exists_on_date", "agreementId": str(same_day.id)},
        )
    if kind is LabourRateAgreementKind.END:
        before = [row for row in rows if row.valid_from < valid_from]
        latest = max(before, key=lambda row: row.valid_from, default=None)
        if latest is None or latest.kind is LabourRateAgreementKind.END:
            raise ConflictError(
                "There is no agreement of this contact and category to end on that date.",
                details={"reason": "labour_rate_agreement_nothing_to_end"},
            )
    row = LabourRateAgreement(
        dealership_id=dealership_id,
        contact_id=contact.id,
        contact_label=contact.label[:200],
        contact_label_refreshed_at=utcnow(),
        category_id=category_id,
        kind=kind,
        valid_from=valid_from,
        amount=amount,
        entered=entered,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise ConflictError(
            "An agreement of this contact and category already starts or ends on that date.",
            details={"reason": "labour_rate_agreement_exists_on_date"},
        )
    record_audit_event(
        db,
        entity_type="labour_rate_agreement",
        entity_id=row.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_agreement_audit(row),
    )
    return row


def withdraw_agreement(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    agreement_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
    on: dt.date | None = None,
) -> LabourRateAgreement:
    """Withdraws an agreement row that has not started yet (ruling 178)."""

    on = on or today()
    row = db.scalar(
        select(LabourRateAgreement)
        .where(LabourRateAgreement.id == agreement_id, LabourRateAgreement.dealership_id == dealership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("Labour rate agreement not found.")
    check_version(row.version, expected_version, entity_name="LabourRateAgreement")
    if row.withdrawn_at is not None:
        raise ConflictError(
            "The agreement is already withdrawn.", details={"reason": "labour_rate_agreement_already_withdrawn"}
        )
    if row.valid_from <= on:
        raise ConflictError(
            "An agreement in use is never changed; enter a new one or its end from a later date.",
            details={"reason": "labour_rate_agreement_in_use"},
        )
    before = _agreement_audit(row)
    row.withdrawn_at = utcnow()
    row.version += 1
    row.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type="labour_rate_agreement",
        entity_id=row.id,
        tenant_id=dealership_id,
        action="withdraw",
        actor_id=actor_id,
        before=before,
        after=_agreement_audit(row),
    )
    return row


def agreements_query(
    *,
    dealership_id: uuid.UUID,
    contact_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    include_withdrawn: bool = False,
):
    stmt = select(LabourRateAgreement).where(LabourRateAgreement.dealership_id == dealership_id)
    if contact_id is not None:
        stmt = stmt.where(LabourRateAgreement.contact_id == contact_id)
    if category_id is not None:
        stmt = stmt.where(LabourRateAgreement.category_id == category_id)
    if not include_withdrawn:
        stmt = stmt.where(LabourRateAgreement.withdrawn_at.is_(None))
    return stmt


def agreements_of(db: Session, **filters) -> list[LabourRateAgreement]:
    return list(db.scalars(agreements_query(**filters)))


def has_rate_ready(db: Session, *, dealership_id: uuid.UUID) -> bool:
    """The Aftersales checklist's labour-rate item (Anto, 2026-11-25): an
    active category has a rate at an active location that is not withdrawn —
    one starting later counts, so a Betrieb can prepare before it opens."""

    return (
        db.scalar(
            select(LabourRate.id)
            .join(LabourRateCategory, LabourRateCategory.id == LabourRate.category_id)
            .join(Location, Location.id == LabourRate.location_id)
            .where(
                LabourRate.dealership_id == dealership_id,
                LabourRate.withdrawn_at.is_(None),
                LabourRateCategory.is_active.is_(True),
                Location.is_active.is_(True),
            )
            .limit(1)
        )
        is not None
    )
