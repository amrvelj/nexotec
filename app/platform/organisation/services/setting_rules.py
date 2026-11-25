"""Rules across two settings (KAN-806, check 49; ADR-216 (5)).

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
ever.

**A write never makes it worse.** A scope is refused only when the write
breaks the rule there; a scope already broken before (a value stored before
the rule existed) does not block an unrelated write — it is reported by
platform's reconciliation instead.

A restore goes through `change_setting`, so it is checked the same way.
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


def _broken(slots: _Slots, choice_of: SettingDeclaration, choice: str, required: SettingDeclaration,
            location_id: uuid.UUID | None) -> bool:
    return _effective(slots, choice_of, location_id) == choice and _effective(slots, required, location_id) is None


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
    before: _Slots = {}
    for row in db.scalars(
        select(SettingValue).where(SettingValue.dealership_id == dealership_id, SettingValue.key.in_(keys))
    ):
        declaration = declarations[row.key]
        if declaration is not None:
            before[(row.key, row.location_id)] = in_force(declaration, row)
    after = dict(before)
    after[(key, location_id)] = proposed

    active = set(
        db.scalars(select(Location.id).where(Location.tenant_id == dealership_id, Location.is_active.is_(True)))
    )
    scopes: list[uuid.UUID | None] = [None]
    scopes += sorted({loc for (_, loc) in after if loc is not None and loc in active}, key=str)

    for choice_of, choice, required_key in rules:
        required = declarations[required_key]
        if required is None:  # refused at start-up by check_declaration_references
            continue
        for scope in scopes:
            if _broken(after, choice_of, choice, required, scope) and not _broken(before, choice_of, choice, required, scope):
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
