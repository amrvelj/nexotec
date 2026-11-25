"""KAN-805 test support: sample setting declarations for the settings store.

Nothing here ships: `app/setting_registry.py` declares only what an area's
ticket brought with its reader (KAN-806: the labour-rate settings). The tests declare samples of each
kind in a test-only area, `testarea`, register them for one test and remove
them again (`declared` fixture), so the registry the app sees is unchanged.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy.orm import Session

from app.platform.organisation.models.dealership import Location
from app.platform.organisation.services import setting_declarations
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingKind,
    WhenNotSet,
    open_test_area,
    register_setting,
    unregister_setting,
)

WAIT = SettingDeclaration(
    key="testarea.wait_minutes",
    module="aftersales",
    kind=SettingKind.NUMBER,
    when_not_set=WhenNotSet.QUIET,
    minimum=Decimal(0),
    maximum=Decimal(240),
    unit="minutes",
    checklist_item=True,
)
SWITCH = SettingDeclaration(
    key="testarea.typed_with_vat", module="aftersales", kind=SettingKind.SWITCH, when_not_set=WhenNotSet.REFUSES
)
UNIT = SettingDeclaration(
    key="testarea.time_unit",
    module="aftersales",
    kind=SettingKind.CHOICE,
    when_not_set=WhenNotSet.ACTS_AS,
    acts_as="hours",
    choices=("hours", "work_units"),
)
FEE = SettingDeclaration(
    key="testarea.small_parts_fee", module="aftersales", kind=SettingKind.AMOUNT, when_not_set=WhenNotSet.QUIET,
    minimum=Decimal(0),
)
NOTE = SettingDeclaration(key="testarea.desk_note", module=None, kind=SettingKind.TEXT, when_not_set=WhenNotSet.QUIET)

SAMPLES = (WAIT, SWITCH, UNIT, FEE, NOTE)


@contextmanager
def declared(*declarations: SettingDeclaration) -> Iterator[None]:
    was_open = setting_declarations._test_area_open
    open_test_area(True)
    try:
        for declaration in declarations:
            register_setting(declaration)
        yield
    finally:
        for declaration in declarations:
            unregister_setting(declaration.key)
        open_test_area(was_open)


def add_location(db: Session, dealership_id: uuid.UUID, *, name: str = "Filiale Nord", active: bool = True) -> uuid.UUID:
    location = Location(tenant_id=dealership_id, name=name, is_active=active)
    db.add(location)
    db.commit()
    return location.id
