"""The settings store (KAN-805, ADR-214): read a declared setting, change it,
see its history, restore an earlier value.

**Reading** (`read_setting`, through `platform.public`): an area names the
Betrieb, the location (or none) and a literal key, and gets the location's
value where the location has one, else the Betrieb's, else «not set» with
what «not set» does (`SettingRead`). A key nobody declared is a programming
error (`UndeclaredSettingError`); `tests/architecture/test_setting_keys_declared_and_read.py`
catches it before it runs.

**Changing** (`change_setting`, `restore_change`): the manager's write
(capability `dealership_settings`, checked by the caller). One transaction
writes the slot, its history row and the audit row. Writes for one Betrieb
are serialised by an advisory transaction lock, so a rule across two keys
(KAN-806: «work units» only with work units per hour, `setting_rules`) reads
a value no concurrent write can change under it; the slot's `version` is checked
against the caller's (If-Match). A slot never written has version 0: the
first write expects 0 and makes 1. A write that changes nothing writes
nothing.

**Restore** (Anto, 2026-11-24: kept for the settings store only; D-A-39 /
KAN-219 stay dropped elsewhere): a restore is itself a change, written like
an edit with `origin = restore` and the change it brought back; the old value
is checked against today's declaration first. Restoring the value the slot
already holds changes nothing, so it writes nothing (as any edit that
changes nothing).

A location is never a scoping boundary (`platform-admin.md`): a location of
another Betrieb is not found, and an inactive location's own value is not
changed (`SettingRefused`).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import NotFoundError
from app.core.pagination import SortPageParams, paginate_query_sorted
from app.core.sorting import build_page_for_sort, parse_sort
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.setting_value import (
    SettingChangeOrigin,
    SettingState,
    SettingValue,
    SettingValueChange,
)
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingKind,
    SettingValueError,
    TextValue,
    WhenNotSet,
    get_declaration,
    validate_value,
)
from app.platform.organisation.services.setting_rules import check_choice_rules

Value = bool | Decimal | str | TextValue

logger = logging.getLogger(__name__)


class UndeclaredSettingError(LookupError):
    """A key no area declared — a programming error, never a user's."""


class SettingRefused(Exception):
    """A change the store refuses; `reason` is the refusal code the API answers with."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class _NotSet:
    def __repr__(self) -> str:
        return "NOT_SET"


# What a caller writes to clear a slot back to «not set».
NOT_SET: Any = _NotSet()


@dataclass(frozen=True)
class SettingRead:
    """What an area reads. `source`: `location`, `dealership`, or None when
    not set. `acts_as`: what «not set» acts as, for a declaration that says so."""

    key: str
    is_set: bool
    value: Value | None
    source: str | None
    when_not_set: WhenNotSet
    acts_as: bool | Decimal | str | None

    @property
    def effective(self) -> Value | None:
        """The value to work with: the one set, else what «not set» acts as (else None)."""

        return self.value if self.is_set else self.acts_as


@dataclass(frozen=True)
class SettingSlot:
    """One slot as the administration sees it: the Betrieb's (`location_id`
    None) or a location's."""

    key: str
    location_id: uuid.UUID | None
    is_set: bool
    value: Value | None
    version: int
    changed_at: Any
    changed_by: uuid.UUID | None
    # False: a value is stored but no longer fits today's declaration, so it is
    # read as not set (`_still_fits`); the administration says so instead of
    # showing it as the value in force.
    fits_declaration: bool = True


@dataclass(frozen=True)
class SettingChange:
    id: uuid.UUID
    key: str
    location_id: uuid.UUID | None
    origin: SettingChangeOrigin
    is_set: bool
    value: Value | None
    version_after: int
    restored_from_id: uuid.UUID | None
    created_at: Any
    created_by: uuid.UUID | None


def _declaration(key: str) -> SettingDeclaration:
    declaration = get_declaration(key)
    if declaration is None:
        raise UndeclaredSettingError(f"No area declared the setting {key!r} (app/setting_registry.py).")
    return declaration


def _plain(number: Decimal) -> Decimal:
    """The number without trailing zeros and never in exponent form (10, not 1E+1)."""

    normal = number.normalize()
    exponent = normal.as_tuple().exponent
    return normal.quantize(Decimal(1)) if isinstance(exponent, int) and exponent > 0 else normal


def _value_of(row: SettingValue | SettingValueChange) -> Value | None:
    if row.state == SettingState.NOT_SET:
        return None
    kind = SettingKind(row.kind)
    if kind == SettingKind.SWITCH:
        return row.value_switch
    if kind in (SettingKind.NUMBER, SettingKind.AMOUNT):
        assert row.value_decimal is not None
        return _plain(row.value_decimal) if kind == SettingKind.NUMBER else row.value_decimal.quantize(Decimal("0.01"))
    if kind == SettingKind.CHOICE:
        return row.value_choice
    assert row.value_text is not None
    return TextValue(name=row.value_text, translations=tuple(sorted((row.value_text_translations or {}).items())))


def _write_value(row: SettingValue | SettingValueChange, kind: SettingKind, value: Value | None) -> None:
    row.kind = kind
    row.state = SettingState.NOT_SET if value is None else SettingState.SET
    row.value_switch = value if kind == SettingKind.SWITCH else None  # type: ignore[assignment]
    row.value_decimal = value if kind in (SettingKind.NUMBER, SettingKind.AMOUNT) else None  # type: ignore[assignment]
    row.value_choice = value if kind == SettingKind.CHOICE else None  # type: ignore[assignment]
    if kind == SettingKind.TEXT and isinstance(value, TextValue):
        row.value_text = value.name
        row.value_text_translations = dict(value.translations) or None
    else:
        row.value_text = None
        row.value_text_translations = None


def _audit_value(value: Value | None) -> Any:
    if value is None:
        return None
    if isinstance(value, TextValue):
        return {"name": value.name, "translations": dict(value.translations)}
    if isinstance(value, Decimal):
        return str(value)
    return value


def _slots(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, key: str) -> list[SettingValue]:
    """The location's slot and the Betrieb's, in that order of precedence."""

    stmt = select(SettingValue).where(SettingValue.dealership_id == dealership_id, SettingValue.key == key)
    if location_id is None:
        stmt = stmt.where(SettingValue.location_id.is_(None))
    else:
        stmt = stmt.where((SettingValue.location_id == location_id) | SettingValue.location_id.is_(None))
    rows = list(db.scalars(stmt))
    return sorted(rows, key=lambda row: row.location_id is None)


def _in_force(declaration: SettingDeclaration, row: SettingValue) -> Value | None:
    """A stored slot's value as reads count it: None when not set or no longer fitting."""

    return _value_of(row) if row.state == SettingState.SET and _still_fits(declaration, row) else None


def _still_fits(declaration: SettingDeclaration, row: SettingValue) -> bool:
    """A stored value counts only while it fits today's declaration: the same
    kind, and a value it still accepts (a choice withdrawn, a bound moved). A
    slot that no longer fits reads as not set, and is logged: ADR-216 says a
    change of kind needs a data migration, and this is what one would miss."""

    try:
        if SettingKind(row.kind) != SettingKind(declaration.kind):
            raise SettingValueError(f"stored as {row.kind}", reason="setting_value_no_longer_valid")
        validate_value(declaration, _value_of(row))
    except SettingValueError as exc:
        logger.warning(
            "Setting %s of business %s (location %s) no longer fits its declaration (%s): read as not set.",
            row.key, row.dealership_id, row.location_id, exc,
        )
        return False
    return True


def read_setting(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, key: str) -> SettingRead:
    """The value of `key` for this Betrieb at this location (None: the Betrieb's
    own): the location's where it has one, else the Betrieb's, else «not set»."""

    declaration = _declaration(key)
    for row in _slots(db, dealership_id=dealership_id, location_id=location_id, key=key):
        if row.state == SettingState.SET and _still_fits(declaration, row):
            return SettingRead(
                key=key,
                is_set=True,
                value=_value_of(row),
                source="dealership" if row.location_id is None else "location",
                when_not_set=declaration.when_not_set,
                acts_as=declaration.acts_as,
            )
    return SettingRead(
        key=key, is_set=False, value=None, source=None, when_not_set=declaration.when_not_set, acts_as=declaration.acts_as
    )


def is_set_for_dealership(db: Session, *, dealership_id: uuid.UUID, key: str) -> bool:
    """Whether the Betrieb's own value of `key` is in force: set, and still
    fitting its declaration (`_still_fits`). The setup checklist's test for a
    setting item (KAN-805 PR-3, ADR-216): a read without a location reads the
    Betrieb's own value only, so values at every location do not complete the
    item while that read would answer «not set». A location's value overrides
    the Betrieb's; it never stands in for it."""

    declaration = _declaration(key)
    row = db.scalar(
        select(SettingValue).where(
            SettingValue.dealership_id == dealership_id,
            SettingValue.location_id.is_(None),
            SettingValue.key == key,
        )
    )
    return row is not None and row.state == SettingState.SET and _still_fits(declaration, row)


def _location(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> Location:
    location = db.scalar(select(Location).where(Location.id == location_id, Location.tenant_id == dealership_id))
    if location is None:
        raise NotFoundError("Location not found.")
    return location


def _slot_row(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, key: str, for_update: bool = False
) -> SettingValue | None:
    stmt = select(SettingValue).where(
        SettingValue.dealership_id == dealership_id,
        SettingValue.key == key,
        SettingValue.location_id.is_(None) if location_id is None else SettingValue.location_id == location_id,
    )
    if for_update:
        # populate_existing: a row the session loaded before the lock is read again, so the
        # version checked is the committed one.
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.scalar(stmt)


def _slot_of(key: str, location_id: uuid.UUID | None, row: SettingValue | None) -> SettingSlot:
    if row is None:
        return SettingSlot(key=key, location_id=location_id, is_set=False, value=None, version=0, changed_at=None, changed_by=None)
    declaration = get_declaration(key)
    fits = row.state != SettingState.SET or declaration is None or _still_fits(declaration, row)
    in_force = row.state == SettingState.SET and fits
    return SettingSlot(
        key=key,
        location_id=location_id,
        is_set=in_force,
        value=_value_of(row) if in_force else None,
        version=row.version,
        changed_at=row.updated_at,
        changed_by=row.updated_by,
        fits_declaration=fits,
    )


def get_slot(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, key: str) -> SettingSlot:
    _declaration(key)
    if location_id is not None:
        _location(db, dealership_id=dealership_id, location_id=location_id)
    return _slot_of(key, location_id, _slot_row(db, dealership_id=dealership_id, location_id=location_id, key=key))


def list_slots(db: Session, *, dealership_id: uuid.UUID, keys: list[str]) -> list[SettingSlot]:
    """Every written slot of these keys for the Betrieb (the Betrieb's and its
    locations'); a slot never written is not listed — it reads «not set», version 0."""

    if not keys:
        return []
    rows = db.scalars(
        select(SettingValue)
        .where(SettingValue.dealership_id == dealership_id, SettingValue.key.in_(keys))
        .order_by(SettingValue.key, SettingValue.location_id.is_not(None), SettingValue.location_id)
    )
    return [_slot_of(row.key, row.location_id, row) for row in rows]


def _lock_dealership(db: Session, dealership_id: uuid.UUID) -> None:
    """Serialises every settings write of one Betrieb until the transaction ends."""

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"platform.setting_store:{dealership_id}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})


def lock_settings(db: Session, *, dealership_id: uuid.UUID) -> None:
    """Holds the Betrieb's settings lock until the transaction ends, for a
    caller that reads slots before it writes them (the configuration copy):
    a colleague's change waits and then meets the moved version, instead of
    slipping in between the read and the write. `change_setting` takes the
    same lock again; it is reentrant."""

    _lock_dealership(db, dealership_id)


def change_setting(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    location_id: uuid.UUID | None,
    key: str,
    value: Any,
    expected_version: int,
    actor_id: uuid.UUID | None,
    origin: SettingChangeOrigin = SettingChangeOrigin.EDIT,
    restored_from_id: uuid.UUID | None = None,
) -> SettingSlot:
    """Sets the slot to `value` (or `NOT_SET`). The caller commits. Raises
    `SettingValueError` for a value that does not fit, `SettingRefused` for an
    inactive location, `NotFoundError` for another Betrieb's location, and the
    version conflict for a stale `expected_version`."""

    declaration = _declaration(key)
    kind = SettingKind(declaration.kind)
    checked: Value | None = None if value is NOT_SET else validate_value(declaration, value)
    _lock_dealership(db, dealership_id)
    if location_id is not None:
        location = _location(db, dealership_id=dealership_id, location_id=location_id)
        if not location.is_active:
            raise SettingRefused(
                "A deactivated location's settings are not changed.", reason="setting_location_inactive"
            )

    row = _slot_row(db, dealership_id=dealership_id, location_id=location_id, key=key, for_update=True)
    check_version(row.version if row is not None else 0, expected_version, entity_name="setting")
    check_choice_rules(db, dealership_id=dealership_id, location_id=location_id, key=key, proposed=checked, in_force=_in_force)
    before = _value_of(row) if row is not None else None
    if row is None and checked is None:
        # Clearing a slot never written changes nothing.
        return _slot_of(key, location_id, None)
    if (
        row is not None
        and SettingKind(row.kind) == kind
        and before == checked
        and (row.state == SettingState.SET) == (checked is not None)
    ):
        return _slot_of(key, location_id, row)

    now = utcnow()
    if row is None:
        row = SettingValue(dealership_id=dealership_id, location_id=location_id, key=key, version=1, created_by=actor_id)
        db.add(row)
    else:
        row.version += 1
    _write_value(row, kind, checked)
    row.updated_by = actor_id
    row.updated_at = now
    db.flush()

    change = SettingValueChange(
        setting_value_id=row.id,
        dealership_id=dealership_id,
        location_id=location_id,
        key=key,
        origin=origin,
        restored_from_id=restored_from_id,
        version_after=row.version,
        created_at=now,
        created_by=actor_id,
    )
    _write_value(change, kind, checked)
    db.add(change)
    record_audit_event(
        db,
        entity_type="setting_value",
        entity_id=row.id,
        tenant_id=dealership_id,
        action=f"setting.{origin.value}",
        actor_id=actor_id,
        before={"key": key, "locationId": str(location_id) if location_id else None, "value": _audit_value(before)},
        after={"key": key, "locationId": str(location_id) if location_id else None, "value": _audit_value(checked)},
    )
    db.flush()
    return _slot_of(key, location_id, row)


def _change_of(row: SettingValueChange) -> SettingChange:
    return SettingChange(
        id=row.id,
        key=row.key,
        location_id=row.location_id,
        origin=SettingChangeOrigin(row.origin),
        is_set=row.state == SettingState.SET,
        value=_value_of(row),
        version_after=row.version_after,
        restored_from_id=row.restored_from_id,
        created_at=row.created_at,
        created_by=row.created_by,
    )


def setting_history(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, key: str
) -> list[SettingChange]:
    """Every change of the slot, newest first."""

    _declaration(key)
    slot = _slot_row(db, dealership_id=dealership_id, location_id=location_id, key=key)
    if slot is None:
        return []
    rows = db.scalars(
        select(SettingValueChange)
        .where(SettingValueChange.setting_value_id == slot.id)
        .order_by(SettingValueChange.created_at.desc(), SettingValueChange.id.desc())
    )
    return [_change_of(row) for row in rows]


def get_change(db: Session, *, dealership_id: uuid.UUID, change_id: uuid.UUID) -> SettingChange:
    """One change of the Betrieb's settings; another Betrieb's is not found."""

    row = db.scalar(
        select(SettingValueChange).where(
            SettingValueChange.id == change_id, SettingValueChange.dealership_id == dealership_id
        )
    )
    if row is None:
        raise NotFoundError("Setting change not found.")
    return _change_of(row)


def restore_change(
    db: Session, *, dealership_id: uuid.UUID, change_id: uuid.UUID, expected_version: int, actor_id: uuid.UUID | None
) -> SettingSlot:
    """Brings back the value an earlier change wrote, as a new change of its
    slot. Another Betrieb's change is not found; a value that no longer fits
    today's declaration is refused (`setting_value_no_longer_valid`)."""

    change = db.scalar(
        select(SettingValueChange).where(
            SettingValueChange.id == change_id, SettingValueChange.dealership_id == dealership_id
        )
    )
    if change is None:
        raise NotFoundError("Setting change not found.")
    declaration = _declaration(change.key)
    old = _value_of(change)
    if SettingKind(change.kind) != SettingKind(declaration.kind):
        raise SettingValueError(
            f"{change.key} is no longer a {change.kind}.", reason="setting_value_no_longer_valid"
        )
    if old is not None:
        try:
            validate_value(declaration, old)
        except SettingValueError as exc:
            raise SettingValueError(str(exc), reason="setting_value_no_longer_valid") from exc
    return change_setting(
        db,
        dealership_id=dealership_id,
        location_id=change.location_id,
        key=change.key,
        value=NOT_SET if old is None else old,
        expected_version=expected_version,
        actor_id=actor_id,
        origin=SettingChangeOrigin.RESTORE,
        restored_from_id=change.id,
    )


HISTORY_SORT = "createdAt:desc"


def history_sort_fields() -> list:
    """Newest first: the history page's one order."""

    return parse_sort(HISTORY_SORT, allowed={"createdAt": SettingValueChange.created_at})


def setting_history_page(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None, key: str, params: SortPageParams
) -> tuple[list[SettingChange], str | None]:
    """One page of the slot's changes, newest first (cursor pagination). A
    location of another Betrieb is not found; a slot never written has none."""

    _declaration(key)
    if location_id is not None:
        _location(db, dealership_id=dealership_id, location_id=location_id)
    slot = _slot_row(db, dealership_id=dealership_id, location_id=location_id, key=key)
    if slot is None:
        return [], None
    stmt = select(SettingValueChange).where(SettingValueChange.setting_value_id == slot.id)
    rows = list(db.scalars(paginate_query_sorted(stmt, model=SettingValueChange, params=params)).all())
    page, next_cursor = build_page_for_sort(rows, params)
    return [_change_of(row) for row in page], next_cursor
