"""Dealer Administration's view of the settings store (KAN-805 PR-2, ADR-214,
ADR-216): which areas a Betrieb configures, and one area's settings with the
Betrieb's value and each location's.

An area is the first part of its settings' keys (`aftersales.time_unit`). A
Betrieb sees an area, a setting and its history only while it holds the
setting's module (`module_entitlement`; a setting of no module, always):
otherwise they are not found, as every route of a module it does not hold.

The per-location column (Anto, 2026-11-24): shown when the Betrieb has more
than one active location, and also while any location holds a value of its
own — a value that applies is never hidden.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.services.entitlement import list_active_modules
from app.platform.organisation.services.location import list_locations
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    declared_settings,
    get_declaration,
)
from app.platform.organisation.services.setting_store import SettingSlot, list_slots


@dataclass(frozen=True)
class SettingArea:
    area: str
    setting_count: int


@dataclass(frozen=True)
class AreaSetting:
    declaration: SettingDeclaration
    dealership: SettingSlot
    locations: tuple[SettingSlot, ...]


@dataclass(frozen=True)
class AreaView:
    area: str
    locations: tuple[Location, ...]
    shows_locations: bool
    settings: tuple[AreaSetting, ...]


def _visible(db: Session, dealership_id: uuid.UUID) -> list[SettingDeclaration]:
    held = list_active_modules(db, dealership_id)
    return [d for d in declared_settings() if d.module is None or d.module in held]


def list_areas(db: Session, *, dealership_id: uuid.UUID) -> list[SettingArea]:
    counts: dict[str, int] = {}
    for declaration in _visible(db, dealership_id):
        counts[declaration.area] = counts.get(declaration.area, 0) + 1
    return [SettingArea(area=area, setting_count=count) for area, count in sorted(counts.items())]


def require_visible_setting(db: Session, *, dealership_id: uuid.UUID, key: str) -> SettingDeclaration:
    """The declaration of `key` if the Betrieb sees it, else 404."""

    declaration = get_declaration(key)
    if declaration is None or (
        declaration.module is not None and declaration.module not in list_active_modules(db, dealership_id)
    ):
        raise NotFoundError("Setting not found.")
    return declaration


def area_view(db: Session, *, dealership_id: uuid.UUID, area: str) -> AreaView:
    declarations = [d for d in _visible(db, dealership_id) if d.area == area]
    if not declarations:
        raise NotFoundError("Settings area not found.")
    locations = tuple(list_locations(db, dealership_id=dealership_id))
    slots = list_slots(db, dealership_id=dealership_id, keys=[d.key for d in declarations])
    settings = []
    any_location_value = False
    for declaration in declarations:
        own = [slot for slot in slots if slot.key == declaration.key]
        betrieb = next((slot for slot in own if slot.location_id is None), None)
        at_locations = tuple(slot for slot in own if slot.location_id is not None)
        any_location_value = any_location_value or any(slot.is_set for slot in at_locations)
        settings.append(
            AreaSetting(
                declaration=declaration,
                dealership=betrieb
                or SettingSlot(
                    key=declaration.key, location_id=None, is_set=False, value=None, version=0,
                    changed_at=None, changed_by=None,
                ),
                locations=at_locations,
            )
        )
    active = sum(1 for location in locations if location.is_active)
    return AreaView(
        area=area,
        locations=locations,
        shows_locations=active > 1 or any_location_value,
        settings=tuple(settings),
    )
