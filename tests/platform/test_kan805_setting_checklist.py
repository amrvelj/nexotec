"""KAN-805 PR-3 (ADR-214): a setting declared as a setup-checklist item is
an item of the derived checklist — open while «not set» somewhere the Betrieb
works, its own action blocked only when its declaration names one, and
notified once by the daily regression job (Anto, 2026-11-24: a setting
declared after activation only notifies)."""

import dataclasses
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.permissions import ModuleCode
from app.core.setup_checklist import ChecklistContribution, register_checklist_contribution
from app.platform.administration.models.setup_checklist import SetupChecklistRegression
from app.platform.administration.services import setup_checklist, setup_checklist_regressions
from app.platform.identity.models.notification import Notification
from app.platform.identity.services.notification_catalogue import NotificationEventCode
from app.platform.organisation.models.dealership import Dealership, DealershipStatus, Location
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingDeclarationError,
    SettingKind,
    WhenNotSet,
    get_declaration,
    unregister_setting,
)
from app.platform.public import register_setting, require_setup_item_for
from tests import setting_store_support as samples
from tests import setup_checklist_support as s

KEY = samples.WAIT.key  # a checklist item of the module aftersales, blocking nothing
LINK = "/administration/settings/testarea"


def _active_and_complete(client, db_session, *, aftersales: bool = True):
    dealership_id = s.pending_dealership(
        client, db_session, modules=(ModuleCode.SALES, ModuleCode.AFTERSALES) if aftersales else (ModuleCode.SALES,)
    )
    boss = s.manager(client, db_session, dealership_id)
    s.complete_checklist(db_session, dealership_id, boss)
    dealership = db_session.get(Dealership, dealership_id)
    dealership.status = DealershipStatus.ACTIVE
    dealership.activated_at = dt.datetime.now(dt.UTC)
    db_session.commit()
    return dealership_id, boss


def _item(db_session, dealership_id, code=KEY):
    dealership = db_session.get(Dealership, dealership_id)
    return next((item for item in setup_checklist.derive(db_session, dealership).items if item.code == code), None)


def _set(db_session, dealership_id, value, location_id=None, key=KEY):
    version = store.get_slot(db_session, dealership_id=dealership_id, location_id=location_id, key=key).version
    store.change_setting(
        db_session, dealership_id=dealership_id, location_id=location_id, key=key, value=value,
        expected_version=version, actor_id=None,
    )
    db_session.commit()


def _seat(db_session, dealership_id):
    return db_session.scalar(select(Location.id).where(Location.tenant_id == dealership_id))


@pytest.fixture()
def declared():
    with samples.declared(*samples.SAMPLES):
        yield


def test_a_checklist_setting_is_an_item_where_its_module_is_held(client, db_session, declared):
    with_aftersales, _ = _active_and_complete(client, db_session)
    without, _ = _active_and_complete(client, db_session, aftersales=False)

    item = _item(db_session, with_aftersales)
    assert item is not None
    assert (item.complete, item.blocks, item.link) == (False, None, LINK)
    assert _item(db_session, without) is None
    # A setting that is not a checklist item is not on it.
    assert _item(db_session, with_aftersales, samples.UNIT.key) is None


def test_the_betriebs_own_value_completes_it(client, db_session, declared):
    dealership_id, _ = _active_and_complete(client, db_session)
    _set(db_session, dealership_id, Decimal(15))
    assert _item(db_session, dealership_id).complete is True
    _set(db_session, dealership_id, store.NOT_SET)
    assert _item(db_session, dealership_id).complete is False


def test_values_at_every_location_do_not_stand_in_for_the_betriebs_own(client, db_session, declared):
    """ADR-216: a read without a location reads the Betrieb's own value only,
    so the item stays open until that value is set; a location's value
    overrides it, never stands in for it."""

    dealership_id, _ = _active_and_complete(client, db_session)
    seat = _seat(db_session, dealership_id)
    nord = samples.add_location(db_session, dealership_id, name="Nord")
    _set(db_session, dealership_id, Decimal(10), location_id=seat)
    _set(db_session, dealership_id, Decimal(20), location_id=nord)
    assert _item(db_session, dealership_id).complete is False
    _set(db_session, dealership_id, Decimal(15))
    assert _item(db_session, dealership_id).complete is True


def test_a_value_that_no_longer_fits_does_not_complete_it(client, db_session):
    loose = SettingDeclaration(
        key=KEY, module="aftersales", kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET, checklist_item=True
    )
    with samples.declared(loose):
        dealership_id, _ = _active_and_complete(client, db_session)
        _set(db_session, dealership_id, Decimal(500))
        assert _item(db_session, dealership_id).complete is True
    tight = SettingDeclaration(
        key=KEY, module="aftersales", kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET, checklist_item=True,
        maximum=Decimal(240),
    )
    with samples.declared(tight):
        assert _item(db_session, dealership_id).complete is False


def test_an_item_blocking_nothing_only_notifies_once(client, db_session, declared):
    """Anto, 2026-11-24: a checklist setting declared after a Betrieb's
    activation is open and notified; nothing is refused."""

    dealership_id, boss = _active_and_complete(client, db_session)
    for action in setup_checklist.BlockedAction:
        require_setup_item_for(db_session, dealership_id, action)  # nothing raises

    first = setup_checklist_regressions.run(db_session)
    assert (first.opened, first.notified) == (1, 1)
    notice = db_session.scalars(
        select(Notification).where(Notification.event_code == NotificationEventCode.CHECKLIST_ITEM_REGRESSED.value)
    ).one()
    assert (notice.user_id, notice.link, notice.params) == (uuid.UUID(boss["id"]), LINK, {"item": KEY})
    assert setup_checklist_regressions.run(db_session).opened == 0  # the next night: no second one

    _set(db_session, dealership_id, Decimal(15))
    assert setup_checklist_regressions.run(db_session).resolved == 1
    row = db_session.scalars(select(SetupChecklistRegression)).one()
    assert (row.item_code, row.resolved_at is not None) == (KEY, True)


def test_an_item_naming_an_action_blocks_that_action(client, db_session):
    blocking = SettingDeclaration(
        key=KEY, module="aftersales", kind=SettingKind.NUMBER, when_not_set=WhenNotSet.REFUSES,
        checklist_item=True, checklist_blocks="invoice_issue",
    )
    with samples.declared(blocking):
        dealership_id, _ = _active_and_complete(client, db_session)
        with pytest.raises(ConflictError) as refused:
            require_setup_item_for(db_session, dealership_id, setup_checklist.BlockedAction.INVOICE_ISSUE)
        assert (refused.value.details["item"], refused.value.details["link"]) == (KEY, LINK)
        # The daily job's notice names the action (the SPA's «gesperrt» body).
        assert setup_checklist_regressions.run(db_session).opened == 1
        notice = db_session.scalars(select(Notification)).one()
        assert notice.params == {"item": KEY, "blocks": "invoice_issue"}
        _set(db_session, dealership_id, Decimal(5))
        require_setup_item_for(db_session, dealership_id, setup_checklist.BlockedAction.INVOICE_ISSUE)


def test_an_action_the_checklist_does_not_know_is_refused_at_start_up():
    """platform.public.register_setting — what the composition root calls —
    refuses it when declared, not when the checklist is derived for every
    Betrieb."""

    unknown = SettingDeclaration(
        key=KEY, module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.REFUSES,
        checklist_item=True, checklist_blocks="launch_rockets",
    )
    known = dataclasses.replace(unknown, checklist_blocks="invoice_issue")
    with samples.declared():
        try:
            with pytest.raises(SettingDeclarationError, match="launch_rockets"):
                register_setting(unknown)
            assert get_declaration(KEY) is None
            register_setting(known)
            assert get_declaration(KEY) == known
        finally:
            unregister_setting(KEY)


def test_a_contributed_item_code_never_has_a_dot():
    """A code with a dot is a setting's key; the SPA titles it by the label."""

    with pytest.raises(ValueError, match="dot"):
        register_checklist_contribution(
            ChecklistContribution(
                code="publishing.profile", applies_to_modules=frozenset(), blocks=None, link="/x",
                is_complete=lambda db, dealership_id: True,
            )
        )


def test_a_pending_betrieb_needs_the_item_for_activation(client, db_session, declared):
    dealership_id = s.pending_dealership(client, db_session, modules=(ModuleCode.AFTERSALES,))
    boss = s.manager(client, db_session, dealership_id)
    s.complete_checklist(db_session, dealership_id, boss)
    dealership = db_session.get(Dealership, dealership_id)
    checklist = setup_checklist.derive(db_session, dealership)
    assert [item.code for item in checklist.missing] == [KEY]
    _set(db_session, dealership_id, Decimal(15))
    assert setup_checklist.derive(db_session, dealership).complete is True


def test_the_regression_ledger_holds_a_key_of_64_characters(client, db_session):
    longest = SettingDeclaration(
        key="testarea." + "k" * 55, module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET,
        checklist_item=True,
    )
    assert len(longest.key) == 64
    with samples.declared(longest):
        _active_and_complete(client, db_session, aftersales=False)
        assert setup_checklist_regressions.run(db_session).opened == 1
    assert db_session.scalars(select(SetupChecklistRegression.item_code)).one() == longest.key
