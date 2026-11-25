"""KAN-808 PR-1c: the planner's settings O6 (`planning.grid_minutes`) and O7
(`planning.slot_minutes`) — declared, read where they bind, and the grid a
divisor of 60 from 5 to 60 (Anto, 2026-11-25)."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.permissions import ModuleCode
from app.planning import public as planning
from app.planning.services.planner_settings import GRID_MINUTES, SLOT_MINUTES
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.models.setting_value import SettingValue
from app.platform.organisation.services import setting_declarations as declarations
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingDeclarationError,
    SettingKind,
    SettingValueError,
    WhenNotSet,
    get_declaration,
)
from tests import planning_support as p
from tests import setup_checklist_support as s
from tests.identity_support import create_dealership


def _refused(call, error=ConflictError) -> dict:
    with pytest.raises(error) as raised:
        call()
    return raised.value.details or {}


def test_o6_and_o7_are_declared_as_aftersales_checklist_items():
    import app.setting_registry  # noqa: F401 — the composition root registers them

    for declaration in (GRID_MINUTES, SLOT_MINUTES):
        assert get_declaration(declaration.key) == declaration
        assert (declaration.module, declaration.kind, declaration.when_not_set, declaration.checklist_item) == (
            "aftersales", SettingKind.NUMBER, WhenNotSet.REFUSES, True,
        )
        assert (declaration.unit, declaration.checklist_blocks) == ("minutes", None)
    assert (GRID_MINUTES.minimum, GRID_MINUTES.maximum, GRID_MINUTES.divides) == (Decimal(5), Decimal(60), Decimal(60))
    assert (SLOT_MINUTES.minimum, SLOT_MINUTES.maximum, SLOT_MINUTES.divides) == (Decimal(5), Decimal(480), None)


def test_the_grid_is_a_divisor_of_60_from_5_to_60():
    allowed = [minutes for minutes in range(1, 121) if _fits(minutes)]
    assert allowed == [5, 6, 10, 12, 15, 20, 30, 60]
    for value, reason in ((7, "setting_value_not_a_divisor"), (25, "setting_value_not_a_divisor"),
                          (45, "setting_value_not_a_divisor"), (4, "setting_value_below_minimum"),
                          (120, "setting_value_above_maximum")):
        with pytest.raises(SettingValueError) as caught:
            declarations.validate_value(GRID_MINUTES, Decimal(value))
        assert caught.value.reason == reason
    with pytest.raises(SettingValueError) as caught:
        declarations.validate_value(GRID_MINUTES, Decimal(25))
    assert caught.value.details == {"divides": "60"}


def _fits(minutes: int) -> bool:
    try:
        declarations.validate_value(GRID_MINUTES, Decimal(minutes))
    except SettingValueError:
        return False
    return True


def test_a_divisor_rule_needs_a_whole_number_with_a_minimum_above_zero():
    for wrong in (
        SettingDeclaration(key="testarea.grid", module=None, kind=SettingKind.AMOUNT, when_not_set=WhenNotSet.QUIET,
                           minimum=Decimal(1), divides=Decimal(60)),
        SettingDeclaration(key="testarea.grid", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET,
                           minimum=Decimal(1), decimals=1, divides=Decimal(60)),
        SettingDeclaration(key="testarea.grid", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET,
                           divides=Decimal(60)),
        SettingDeclaration(key="testarea.grid", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET,
                           minimum=Decimal(1), divides=Decimal("60.5")),
    ):
        declarations.open_test_area(True)
        try:
            with pytest.raises(SettingDeclarationError):
                declarations.register_setting(wrong)
        finally:
            declarations.unregister_setting(wrong.key)
            declarations.open_test_area(False)


def test_the_settings_api_refuses_a_grid_that_does_not_divide_60(client, db_session):
    dealership_id = uuid.UUID(create_dealership(client))
    db_session.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
    db_session.commit()
    boss = s.headers_for(s.manager(client, db_session, dealership_id), dealership_id)
    path = f"/v1/dealerships/{dealership_id}/setting-values/planning.grid_minutes"
    refused = client.put(path, json={"value": {"number": "25"}}, headers={**boss, "If-Match": "0"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "setting_value_not_a_divisor", "divides": "60"}
    written = client.put(path, json={"value": {"number": "20"}}, headers={**boss, "If-Match": "0"})
    assert written.status_code == 200, written.text
    area = client.get(f"/v1/dealerships/{dealership_id}/setting-areas/planning", headers=boss)
    assert area.status_code == 200, area.text
    settings = {item["declaration"]["key"]: item for item in area.json()["settings"]}
    assert sorted(settings) == ["planning.grid_minutes", "planning.slot_minutes"]
    assert settings["planning.grid_minutes"]["dealership"]["value"]["number"] == "20"


def test_not_set_refuses_work_and_slots_and_names_the_setting(client, db_session):
    shop = p.workshop(client, db_session, grid=None, slot=None)
    details = _refused(lambda: p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1]))
    assert details == {"reason": "planning_setting_not_set", "setting": "planning.grid_minutes"}
    p.set_setting(db_session, shop.dealership_id, "planning.grid_minutes", 15)
    details = _refused(
        lambda: p.slot(db_session, shop, planning.BookingKind.DROP_OFF, starts_at=p.at(8), reference=p.order(),
                       resources=[shop.max])
    )
    assert details == {"reason": "planning_setting_not_set", "setting": "planning.slot_minutes"}
    # A block reads neither.
    p.block(db_session, shop, starts_at=p.at(8, 3), ends_at=p.at(8, 41), resources=[shop.max])


def test_work_and_slots_start_and_end_on_the_grid(db_session, shop):
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(9, 5), ends_at=p.at(10), resources=[shop.lift_1]),
        UnprocessableEntityError,
    )
    assert details == {"reason": "planning_time_off_grid", "gridMinutes": 15, "fields": ["startsAt"]}
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(9, 50), resources=[shop.lift_1]),
        UnprocessableEntityError,
    )
    assert details["fields"] == ["endsAt"]
    details = _refused(
        lambda: p.slot(db_session, shop, planning.BookingKind.PICK_UP, starts_at=p.at(16, 10), reference=p.order(),
                       resources=[shop.max]),
        UnprocessableEntityError,
    )
    assert details["fields"] == ["startsAt", "endsAt"]


@pytest.fixture()
def shop(client, db_session) -> p.Workshop:
    return p.workshop(client, db_session)


def test_a_locations_own_value_counts_there(db_session, shop):
    p.set_setting(db_session, shop.dealership_id, "planning.grid_minutes", 10, location_id=shop.location_id)
    booked = p.work(db_session, shop, starts_at=p.at(9, 10), ends_at=p.at(9, 50), resources=[shop.lift_1])
    assert booked.booking.starts_at == p.at(9, 10)
    p.set_setting(db_session, shop.dealership_id, "planning.slot_minutes", 20, location_id=shop.location_id)
    slot = p.slot(db_session, shop, planning.BookingKind.DROP_OFF, starts_at=p.at(8), reference=p.order(),
                  resources=[shop.max])
    assert slot.booking.ends_at - slot.booking.starts_at == dt.timedelta(minutes=20)


def test_a_slot_is_as_long_as_o7(db_session, shop):
    order = p.order()
    details = _refused(
        lambda: planning.book(
            db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, kind=planning.BookingKind.DROP_OFF,
            starts_at=p.at(8), ends_at=p.at(9), resource_ids=[shop.max], reference=order, idempotency_key=shop.keys(),
        ),
        UnprocessableEntityError,
    )
    assert details == {"reason": "planning_slot_length_fixed", "slotMinutes": 30}
    booked = planning.book(
        db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, kind=planning.BookingKind.DROP_OFF,
        starts_at=p.at(8), ends_at=p.at(8, 30), resource_ids=[shop.max], reference=order, idempotency_key=shop.keys(),
    )
    assert booked.booking.ends_at == p.at(8, 30)


def test_a_stored_grid_that_no_longer_fits_reads_as_not_set(db_session, shop):
    row = db_session.scalar(
        select(SettingValue).where(
            SettingValue.dealership_id == shop.dealership_id, SettingValue.key == "planning.grid_minutes"
        )
    )
    row.value_decimal = Decimal(7)  # written before the divisor rule, say
    db_session.commit()
    details = _refused(lambda: p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1]))
    assert details == {"reason": "planning_setting_not_set", "setting": "planning.grid_minutes"}
