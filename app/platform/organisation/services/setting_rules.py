"""Rules across two settings (KAN-806, check 49; ADR-216 (5); KAN-808).

A declaration may say that one of its choices needs another setting in force:
O18 «work units» needs O19 «work units per hour» (ruling 183). The store
enforces it on every write of either key, inside the write's advisory lock
(`setting_store._lock_dealership`), so no concurrent write can change the
other key under it:

- choosing the choice while the other setting is not in force is refused
  (`setting_choice_requires_setting`);
- clearing the other setting while the choice is in force is refused
  (`setting_required_by_choice`).

**Where it is checked.** A value counts per location (ruling 175): a
location's own value, else the Betrieb's. So the rule is checked for the
Betrieb (a read without a location) and for every *active* location that
holds a slot of either key, the one being written included. An inactive
location is left out: its own slots cannot be changed
(`setting_location_inactive`), so counting it would block the Betrieb for
ever. That leaves one way to a broken scope: a location deactivated while it
relied on the Betrieb's value, which is then cleared, and reactivated. Platform's
nightly reconciliation reports it for the one rule shipped
(«setting_value labour_rates.time_unit on work units without work units per
hour»); a new rule adds its own check.

**A write never makes it worse.** A scope is refused only when the write
breaks the rule there; a scope already broken before does not block an
unrelated write.

A restore goes through `change_setting`, so it is checked the same way.

**A multiple** (KAN-808; Anto, 2026-11-25): a number declared
`multiple_of=<key>` is a whole multiple of that key's value wherever both are
in force — O7, the slot length, of O6, the planner's grid. The same scopes,
the same lock, the same «never worse»:

- writing the multiple to a value that is not one is refused
  (`setting_not_a_multiple_of_setting`);
- writing the other key to a value the multiple no longer is a multiple of is
  refused (`setting_no_longer_divides_setting`).

«Not set» on either side breaks nothing: each setting refuses its own reader
while not set. No acts-as applies: a number with an acts-as value has none here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.setting_value import SettingValue
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingValueError,
    WhenNotSet,
    choice_rules,
    get_declaration,
    multiple_rules,
)

# (key, location_id) -> the value in force in that slot, None when not set.
_Slots = dict[tuple[str, uuid.UUID | None], Any]


def _effective(slots: _Slots, declaration: SettingDeclaration, location_id: uuid.UUID | None) -> Any:
    value = slots.get((declaration.key, location_id)) if location_id is not None else None
    if value is None:
        value = slots.get((declaration.key, None))
    if value is None and declaration.when_not_set == WhenNotSet.ACTS_AS:
        value = declaration.acts_as
    return value


def _broken(
    slots: _Slots,
    choice_of: SettingDeclaration,
    choice: str,
    required: SettingDeclaration,
    location_id: uuid.UUID | None,
) -> bool:
    return _effective(slots, choice_of, location_id) == choice and _effective(slots, required, location_id) is None


def _slots_of(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    keys: set[str],
    in_force: Callable[[SettingDeclaration, SettingValue], Any],
) -> _Slots:
    declarations = {k: get_declaration(k) for k in keys}
    found: _Slots = {}
    for row in db.scalars(
        select(SettingValue).where(SettingValue.dealership_id == dealership_id, SettingValue.key.in_(keys))
    ):
        declaration = declarations[row.key]
        if declaration is not None:
            found[(row.key, row.location_id)] = in_force(declaration, row)
    return found


def _scopes(db: Session, dealership_id: uuid.UUID, slots: _Slots) -> list[uuid.UUID | None]:
    """The Betrieb, and every active location holding a slot of the keys."""

    active = set(
        db.scalars(select(Location.id).where(Location.tenant_id == dealership_id, Location.is_active.is_(True)))
    )
    scopes: list[uuid.UUID | None] = [None]
    scopes += sorted({loc for (_, loc) in slots if loc is not None and loc in active}, key=str)
    return scopes


def check_choice_rules(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    location_id: uuid.UUID | None,
    key: str,
    proposed: Any,
    in_force: Callable[[SettingDeclaration, SettingValue], Any],
) -> None:
    """Refuses the write of `proposed` (None: clearing) to `key`'s slot at
    `location_id` when it breaks a rule across two keys. `in_force` is the
    store's reading of a stored slot (its value, or None when not set or no
    longer fitting). Called under the Betrieb's settings lock."""

    rules = [
        (choice_of, choice, required_key)
        for choice_of, choice, required_key in choice_rules()
        if key in (choice_of.key, required_key)
    ]
    if not rules:
        return
    keys = {choice_of.key for choice_of, _, _ in rules} | {required_key for _, _, required_key in rules}
    declarations = {k: get_declaration(k) for k in keys}
    before = _slots_of(db, dealership_id=dealership_id, keys=keys, in_force=in_force)
    after = dict(before)
    after[(key, location_id)] = proposed
    scopes = _scopes(db, dealership_id, after)

    for choice_of, choice, required_key in rules:
        required = declarations[required_key]
        if required is None:  # refused at start-up by check_declaration_references
            continue
        for scope in scopes:
            if _broken(after, choice_of, choice, required, scope) and not _broken(
                before, choice_of, choice, required, scope
            ):
                details: dict[str, object] = {
                    "setting": choice_of.key,
                    "choice": choice,
                    "requires": required_key,
                    "locationId": str(scope) if scope is not None else None,
                }
                if key == choice_of.key:
                    raise SettingValueError(
                        f"{choice_of.key} cannot be {choice!r} while {required_key} is not set.",
                        reason="setting_choice_requires_setting",
                        details=details,
                    )
                raise SettingValueError(
                    f"{required_key} cannot be cleared while {choice_of.key} is {choice!r}.",
                    reason="setting_required_by_choice",
                    details=details,
                )


def _not_a_multiple(slots: _Slots, multiple: SettingDeclaration, divisor: SettingDeclaration, scope: uuid.UUID | None) -> bool:
    value, of = _effective(slots, multiple, scope), _effective(slots, divisor, scope)
    if value is None or of is None or of == 0:
        return False
    return value % of != 0


def check_multiple_rules(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    location_id: uuid.UUID | None,
    key: str,
    proposed: Any,
    in_force: Callable[[SettingDeclaration, SettingValue], Any],
) -> None:
    """Refuses the write of `proposed` (None: clearing) to `key`'s slot at
    `location_id` when it leaves a multiple no multiple of its key in a scope
    where it was one. Called under the Betrieb's settings lock."""

    rules = [(multiple, of) for multiple, of in multiple_rules() if key in (multiple.key, of)]
    if not rules:
        return
    keys = {multiple.key for multiple, _ in rules} | {of for _, of in rules}
    before = _slots_of(db, dealership_id=dealership_id, keys=keys, in_force=in_force)
    after = dict(before)
    after[(key, location_id)] = proposed
    scopes = _scopes(db, dealership_id, after)

    for multiple, of_key in rules:
        divisor = get_declaration(of_key)
        if divisor is None:  # refused at start-up by check_declaration_references
            continue
        for scope in scopes:
            if _not_a_multiple(after, multiple, divisor, scope) and not _not_a_multiple(before, multiple, divisor, scope):
                details: dict[str, object] = {
                    "setting": multiple.key,
                    "multipleOf": of_key,
                    "locationId": str(scope) if scope is not None else None,
                }
                if key == multiple.key:
                    raise SettingValueError(
                        f"{multiple.key} must be a multiple of {of_key}.",
                        reason="setting_not_a_multiple_of_setting",
                        details=details,
                    )
                raise SettingValueError(
                    f"{of_key} must divide {multiple.key} without remainder.",
                    reason="setting_no_longer_divides_setting",
                    details=details,
                )
