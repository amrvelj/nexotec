"""Number ranges (KAN-207; Dealer Administration PRD "Number ranges",
D-A-17, A-RULE-3, ADR-032; ADR-109).

Platform holds each Betrieb's counter per document type and hands numbers out
inside the calling module's own transaction (`allocate_number`, reached
through `platform.public`): a row lock (`SELECT … FOR UPDATE`) serialises the
callers of one range, and the counter moves in the same transaction as the
document that carries the number — so a document write that fails rolls the
counter back with it and consumes no number (gapless, ADR-032). That is the
one shape ADR-047 forbids; ADR-109 (Anto, 2026-10-31: numbering is central
and lives in Platform) rules it a permanent exception for number allocation
alone.

The pattern is literal characters plus three tokens: `{year}` (four digits,
the Swiss calendar year of the allocation), `{location_code}` (the site's
code, a label — never a second counter, D-A-17) and exactly one
`{counter:N}` (the counter zero-padded to N digits). A rendered number fits
16 characters (`sales_offer.offer_number` and the other document numbers).

A range freezes when it hands out its first number (A-RULE-3; at activation
too, once FR-A-34's activation exists): pattern, start value and yearly reset
then change only by a platform admin, with a reason, audited.
"""

import datetime as dt
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.permissions import ModuleCode
from app.platform.models.dealership import Location
from app.platform.models.number_range import NumberRange, NumberRangeDocumentType

ENTITY_TYPE = "number_range"
MAX_NUMBER_LENGTH = 16
MAX_COUNTER_DIGITS = 9
_LITERAL_RE = re.compile(r"^[A-Za-z0-9./_-]*$")
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_COUNTER_RE = re.compile(r"^counter:([1-9])$")

DocumentType = NumberRangeDocumentType

# Which module a range belongs to: the screen shows a range only to a Betrieb
# entitled to it (KAN-207, settled (d)).
MODULE_OF: dict[NumberRangeDocumentType, ModuleCode] = {
    DocumentType.OFFER: ModuleCode.SALES,
    DocumentType.ORDER_CONFIRMATION: ModuleCode.SALES,
    DocumentType.SALES_CONTRACT: ModuleCode.SALES,
    DocumentType.PURCHASE_CONTRACT: ModuleCode.SALES,
    DocumentType.STOCK_NUMBER: ModuleCode.SALES,
    DocumentType.INVOICE: ModuleCode.FINANCE,
    DocumentType.CREDIT_NOTE: ModuleCode.FINANCE,
    DocumentType.DUNNING: ModuleCode.FINANCE,
    DocumentType.SERVICE_ORDER: ModuleCode.AFTERSALES,
    DocumentType.WORKSHOP_JOB: ModuleCode.AFTERSALES,
    DocumentType.PARTS_ORDER: ModuleCode.PARTS,
}


@dataclass(frozen=True)
class Seed:
    pattern: str
    yearly_reset: bool


# Neutral seeds, one per document type. The offer, contract and stock-number
# seeds print what Nexotec prints today (`O-000123`, `C-000045`, `S-000067`):
# the offer range continues each Betrieb's existing sequence (Anto,
# 2026-11-01, option 1); the contract and stock ranges are not drawn from yet
# and keep today's shape for when their modules switch.
SEEDS: dict[NumberRangeDocumentType, Seed] = {
    DocumentType.OFFER: Seed("O-{counter:6}", False),
    DocumentType.ORDER_CONFIRMATION: Seed("AB-{year}-{counter:5}", True),
    DocumentType.SALES_CONTRACT: Seed("C-{counter:6}", False),
    DocumentType.PURCHASE_CONTRACT: Seed("EV-{year}-{counter:5}", True),
    DocumentType.INVOICE: Seed("R-{year}-{counter:6}", True),
    DocumentType.CREDIT_NOTE: Seed("G-{year}-{counter:5}", True),
    DocumentType.DUNNING: Seed("M-{year}-{counter:5}", True),
    DocumentType.SERVICE_ORDER: Seed("SA-{year}-{counter:5}", True),
    DocumentType.PARTS_ORDER: Seed("TB-{year}-{counter:5}", True),
    DocumentType.WORKSHOP_JOB: Seed("WA-{year}-{counter:5}", True),
    DocumentType.STOCK_NUMBER: Seed("S-{counter:6}", False),
}

# A document type whose documents do not know their site yet cannot print
# `{location_code}` (KAN-207, settled (a)): an offer carries no location today.
_NO_LOCATION_YET = frozenset({DocumentType.OFFER})


@dataclass(frozen=True)
class ParsedPattern:
    pattern: str
    counter_digits: int
    has_year: bool
    has_location_code: bool

    def render(self, *, counter: int, year: int, location_code: str | None) -> str:
        def token(match: re.Match[str]) -> str:
            name = match.group(1)
            if name == "year":
                return f"{year:04d}"
            if name == "location_code":
                return location_code or ""
            return f"{counter:0{self.counter_digits}d}"

        return _TOKEN_RE.sub(token, self.pattern)

    def length_with(self, location_code_length: int) -> int:
        literal = len(_TOKEN_RE.sub("", self.pattern))
        return (
            literal
            + self.counter_digits
            + (4 if self.has_year else 0)
            + (location_code_length if self.has_location_code else 0)
        )


def _invalid(problem: str) -> UnprocessableEntityError:
    return UnprocessableEntityError(
        f"The number pattern is not valid ({problem}).",
        details={"reason": "number_pattern_invalid", "problem": problem},
    )


def parse_pattern(pattern: str) -> ParsedPattern:
    """The grammar above; 422 `number_pattern_invalid` with a `problem`."""

    if not pattern or len(pattern) > 64:
        raise _invalid("length")
    tokens = _TOKEN_RE.findall(pattern)
    if not _LITERAL_RE.match(_TOKEN_RE.sub("", pattern)):
        raise _invalid("character")
    digits: list[int] = []
    years = locations = 0
    for name in tokens:
        if name == "year":
            years += 1
        elif name == "location_code":
            locations += 1
        elif match := _COUNTER_RE.match(name):
            digits.append(int(match.group(1)))
        else:
            raise _invalid("unknown_token")
    if not digits:
        raise _invalid("counter_missing")
    if len(digits) > 1 or years > 1 or locations > 1:
        raise _invalid("token_repeated")
    return ParsedPattern(pattern=pattern, counter_digits=digits[0], has_year=years == 1, has_location_code=locations == 1)


def swiss_year() -> int:
    return dt.datetime.now(ZoneInfo(get_settings().session_timezone)).year


def _location_codes(db: Session, dealership_id: uuid.UUID) -> list[str]:
    codes = db.scalars(
        select(Location.code)
        .where(Location.tenant_id == dealership_id, Location.code.is_not(None), Location.is_active.is_(True))
        .order_by(Location.code)
    ).all()
    return [code for code in codes if code]


def validate(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    document_type: NumberRangeDocumentType,
    pattern: str,
    start_value: int,
    yearly_reset: bool,
) -> ParsedPattern:
    """Everything a range must satisfy to be saved (422 with a reason)."""

    parsed = parse_pattern(pattern)
    if parsed.has_location_code and document_type in _NO_LOCATION_YET:
        raise UnprocessableEntityError(
            "This document type does not know its location yet; its pattern cannot use {location_code}.",
            details={"reason": "number_pattern_location_unavailable"},
        )
    longest = 0
    if parsed.has_location_code:
        codes = _location_codes(db, dealership_id)
        if not codes:
            raise UnprocessableEntityError(
                "No location of this business has a code for {location_code}.",
                details={"reason": "number_pattern_location_code_missing"},
            )
        longest = max(len(code) for code in codes)
    length = parsed.length_with(longest)
    if length > MAX_NUMBER_LENGTH:
        raise UnprocessableEntityError(
            f"A number following this pattern has {length} characters; at most {MAX_NUMBER_LENGTH} fit.",
            details={"reason": "number_pattern_too_long", "length": length, "maxLength": MAX_NUMBER_LENGTH},
        )
    if yearly_reset and not parsed.has_year:
        # Without the year in it, a reset would hand out a number again.
        raise UnprocessableEntityError(
            "A range that starts again every year needs {year} in its pattern.",
            details={"reason": "number_pattern_reset_needs_year"},
        )
    if not 1 <= start_value < 10**parsed.counter_digits:
        raise UnprocessableEntityError(
            "The start value does not fit the counter's digits.",
            details={"reason": "number_range_start_out_of_range", "maxValue": 10**parsed.counter_digits - 1},
        )
    return parsed


def _next_counter(row: NumberRange, *, year: int, continue_after: int | None) -> int:
    if row.last_value is None or (row.yearly_reset and row.last_year != year):
        counter = row.start_value
    else:
        counter = row.last_value + 1
    if continue_after is not None and row.frozen_at is None:
        # A range that has handed out nothing yet continues the sequence the
        # module numbered with before (KAN-207, Anto 2026-11-01, option 1).
        counter = max(counter, continue_after + 1)
    return counter


def next_number_example(db: Session, row: NumberRange, *, year: int | None = None) -> str:
    """The number the range would hand out next — the screen's example. A
    `{location_code}` shows the Betrieb's first location code."""

    parsed = parse_pattern(row.pattern)
    year = year or swiss_year()
    codes = _location_codes(db, row.tenant_id) if parsed.has_location_code else []
    return parsed.render(counter=_next_counter(row, year=year, continue_after=None), year=year, location_code=codes[0] if codes else "")


def preview(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    document_type: NumberRangeDocumentType,
    pattern: str,
    start_value: int,
    yearly_reset: bool,
) -> str:
    """The live example of an edit: the range's next number as if saved."""

    validate(
        db, dealership_id=dealership_id, document_type=document_type, pattern=pattern,
        start_value=start_value, yearly_reset=yearly_reset,
    )
    row = get_or_seed(db, dealership_id=dealership_id, document_type=document_type, lock=False)
    probe = NumberRange(
        tenant_id=dealership_id, document_type=document_type, pattern=pattern, start_value=start_value,
        yearly_reset=yearly_reset, last_value=row.last_value, last_year=row.last_year, frozen_at=row.frozen_at,
    )
    return next_number_example(db, probe)


def new_range(dealership_id: uuid.UUID, document_type: NumberRangeDocumentType, actor_id: uuid.UUID | None) -> NumberRange:
    seed = SEEDS[document_type]
    return NumberRange(
        tenant_id=dealership_id,
        document_type=document_type,
        pattern=seed.pattern,
        start_value=1,
        yearly_reset=seed.yearly_reset,
        last_value=None,
        last_year=None,
        frozen_at=None,
        version=1,
        created_by=actor_id,
        updated_by=actor_id,
    )


def seed_number_ranges(db: Session, *, dealership_id: uuid.UUID, actor_id: uuid.UUID | None) -> None:
    """A new Betrieb's eleven ranges, with the neutral seeds (onboarding)."""

    for document_type in DocumentType:
        db.add(new_range(dealership_id, document_type, actor_id))


def get_or_seed(
    db: Session, *, dealership_id: uuid.UUID, document_type: NumberRangeDocumentType, lock: bool
) -> NumberRange:
    """The Betrieb's range, created from its seed on first use when the Betrieb
    predates its ranges — a concurrent first caller's row is taken (the
    savepoint keeps the caller's transaction alive), as Sales' counters do."""

    def read() -> NumberRange | None:
        stmt = select(NumberRange).where(
            NumberRange.tenant_id == dealership_id, NumberRange.document_type == document_type
        )
        if lock:
            stmt = stmt.with_for_update()
        return db.scalars(stmt).one_or_none()

    row = read()
    if row is not None:
        return row
    db.flush()
    try:
        with db.begin_nested():
            db.add(new_range(dealership_id, document_type, None))
    except IntegrityError:
        pass
    row = read()
    assert row is not None, "number_range row missing after its first-use INSERT or the concurrent winner's"
    return row


def list_ranges(
    db: Session, *, dealership_id: uuid.UUID, modules: Iterable[ModuleCode]
) -> list[NumberRange]:
    """The Betrieb's ranges for the modules it holds, in the PRD's order. A
    range a Betrieb predating it does not hold yet reads as its seed (not
    written: a read writes nothing; the first edit or number creates it)."""

    held = set(modules)
    wanted = [t for t in DocumentType if MODULE_OF[t] in held]
    rows = {
        row.document_type: row
        for row in db.scalars(select(NumberRange).where(NumberRange.tenant_id == dealership_id)).all()
    }
    return [rows.get(t) or new_range(dealership_id, t, None) for t in wanted]


def _audit_state(row: NumberRange) -> dict[str, object]:
    return {
        "pattern": row.pattern,
        "startValue": row.start_value,
        "yearlyReset": row.yearly_reset,
        "lastValue": row.last_value,
        "frozenAt": row.frozen_at.isoformat() if row.frozen_at else None,
    }


def update_range(
    db: Session,
    *,
    row: NumberRange,
    pattern: str | None,
    start_value: int | None,
    yearly_reset: bool | None,
    reason: str | None,
    actor_id: uuid.UUID,
    actor_is_platform_admin: bool,
) -> NumberRange:
    """The manager's edit before the range froze; after, a platform admin's
    intervention with a reason (A-RULE-3). Audited with before/after. The
    caller checked If-Match on the row it locked."""

    if row.frozen_at is not None:
        if not actor_is_platform_admin:
            raise ConflictError(
                "This number range has handed out its first number and is frozen.",
                details={"reason": "number_range_frozen"},
            )
        if not (reason and reason.strip()):
            raise UnprocessableEntityError(
                "A change to a frozen number range needs a reason.",
                details={"reason": "number_range_reason_required"},
            )
    new_pattern = row.pattern if pattern is None else pattern
    new_start = row.start_value if start_value is None else start_value
    new_reset = row.yearly_reset if yearly_reset is None else yearly_reset
    validate(
        db, dealership_id=row.tenant_id, document_type=row.document_type, pattern=new_pattern,
        start_value=new_start, yearly_reset=new_reset,
    )
    before = _audit_state(row)
    row.pattern = new_pattern
    row.start_value = new_start
    row.yearly_reset = new_reset
    row.version += 1
    row.updated_by = actor_id
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        tenant_id=row.tenant_id,
        action="number_range_frozen_changed" if row.frozen_at is not None else "number_range_updated",
        actor_id=actor_id,
        before=before,
        after=_audit_state(row),
        reason=reason.strip() if reason and reason.strip() else None,
    )
    db.flush()
    return row


def allocate_number(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    document_type: NumberRangeDocumentType,
    location_id: uuid.UUID | None = None,
    continue_after: int | None = None,
) -> str:
    """The next number of the Betrieb's range, inside the caller's transaction
    (ADR-109): the range row is locked until the caller commits or rolls
    back, so concurrent callers are served one after the other and a rolled
    back document gives its number back. Never commits.

    `continue_after` is the last number a module handed out on its own before
    it drew from the range (Sales' offer counter): the range's first number
    continues after it. The first number freezes the range (A-RULE-3)."""

    row = get_or_seed(db, dealership_id=dealership_id, document_type=document_type, lock=True)
    parsed = parse_pattern(row.pattern)
    year = swiss_year()
    counter = _next_counter(row, year=year, continue_after=continue_after)
    if counter >= 10**parsed.counter_digits:
        raise ConflictError(
            "The number range has no number left for its counter's digits.",
            details={"reason": "number_range_exhausted"},
        )
    location_code = None
    if parsed.has_location_code:
        location = db.get(Location, location_id) if location_id is not None else None
        if location is None or location.tenant_id != dealership_id or not location.code:
            raise ConflictError(
                "This number range prints the location's code, and the document names no location with one.",
                details={"reason": "number_location_code_missing"},
            )
        location_code = location.code
    number = parsed.render(counter=counter, year=year, location_code=location_code)
    if len(number) > MAX_NUMBER_LENGTH:
        raise ConflictError(
            f"The number {number} is longer than {MAX_NUMBER_LENGTH} characters.",
            details={"reason": "number_pattern_too_long", "length": len(number), "maxLength": MAX_NUMBER_LENGTH},
        )
    first = row.frozen_at is None
    row.last_value = counter
    row.last_year = year
    row.version += 1
    if first:
        row.frozen_at = utcnow()
        record_audit_event(
            db,
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            tenant_id=dealership_id,
            action="number_range_frozen",
            actor_id=None,
            after=_audit_state(row),
            reason="first number handed out",
        )
    db.flush()
    return number


def continue_sequence(
    db: Session, *, dealership_id: uuid.UUID, document_type: NumberRangeDocumentType, after: int
) -> bool:
    """A range that has handed out nothing takes over the sequence a module
    numbered with before: its current value becomes `after` (never lowered),
    and it stays unfrozen (KAN-207: Betriebe already active are seeded
    unfrozen). False when there was nothing to do. Audited."""

    row = get_or_seed(db, dealership_id=dealership_id, document_type=document_type, lock=True)
    if row.frozen_at is not None or (row.last_value is not None and row.last_value >= after) or after < 1:
        return False
    before = _audit_state(row)
    row.last_value = after
    row.last_year = swiss_year()
    row.version += 1
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        tenant_id=dealership_id,
        action="number_range_continued",
        actor_id=None,
        before=before,
        after=_audit_state(row),
        reason="continues the module's existing sequence",
    )
    db.flush()
    return True
