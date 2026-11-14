"""KAN-214 (D-A-21, FR-A-26; D-A-40's administration family): after
activation a broken prerequisite blocks only its own action, with the item
named — marketplace publishing without a location's opening hours is the
wired one — and the daily job raises one notification per regression."""

import datetime as dt
import types
import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.inventory.models.stock_item_publishing import MarketplaceChannel
from app.inventory.services import publishing
from app.platform.identity.models.notification import Notification
from app.platform.identity.services.notification_catalogue import NotificationEventCode
from app.platform.models.setup_checklist import SetupChecklistRegression
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.models.location_schedule import LocationOpeningHours
from app.platform.public import SetupBlockedAction, require_setup_item_for
from app.platform.services import setup_checklist_regressions
from tests import setup_checklist_support as s


def _active_and_complete(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    s.complete_checklist(db_session, dealership_id, boss)
    dealership = db_session.get(Dealership, dealership_id)
    dealership.status = DealershipStatus.ACTIVE
    dealership.activated_at = dt.datetime.now(dt.UTC)  # as the activation procedure leaves it
    db_session.commit()
    return dealership_id, boss


def _trading_before_the_checklist(client, db_session):
    """A Betrieb KAN-214's migration set active: trading, never activated."""

    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    db_session.get(Dealership, dealership_id).status = DealershipStatus.ACTIVE
    db_session.commit()
    return dealership_id, boss


def _remove_opening_hours(db_session, dealership_id):
    location = s.seat_location(db_session, dealership_id)
    for row in db_session.scalars(select(LocationOpeningHours).where(LocationOpeningHours.location_id == location.id)):
        db_session.delete(row)
    db_session.commit()


def test_publishing_without_opening_hours_is_refused_naming_the_item(client, db_session):
    dealership_id, _ = _active_and_complete(client, db_session)
    _remove_opening_hours(db_session, dealership_id)
    item = types.SimpleNamespace(tenant_id=dealership_id)  # refused before the item is read
    with pytest.raises(ConflictError) as refused:
        publishing.publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=None)
    assert refused.value.details == {
        "reason": "setup_checklist_item_missing",
        "item": "location_opening_hours",
        "link": "/administration/locations",
        "action": "marketplace_publishing",
    }


def _regression_notifications(db_session) -> list[Notification]:
    return list(
        db_session.scalars(
            select(Notification).where(Notification.event_code == NotificationEventCode.CHECKLIST_ITEM_REGRESSED.value)
        ).all()
    )


def test_one_notification_per_regression_and_a_fixed_item_is_closed(client, db_session):
    dealership_id, boss = _active_and_complete(client, db_session)
    assert setup_checklist_regressions.run(db_session).opened == 0

    _remove_opening_hours(db_session, dealership_id)
    first = setup_checklist_regressions.run(db_session)
    assert (first.opened, first.notified) == (1, 1)
    notices = _regression_notifications(db_session)
    assert len(notices) == 1
    assert notices[0].user_id == uuid.UUID(boss["id"])
    assert notices[0].link == "/administration/locations"
    assert notices[0].params == {"item": "location_opening_hours"}

    # Still broken the next night: no second notification.
    assert setup_checklist_regressions.run(db_session).opened == 0
    assert len(_regression_notifications(db_session)) == 1

    s.complete_opening_hours(db_session, dealership_id)
    assert setup_checklist_regressions.run(db_session).resolved == 1
    rows = db_session.scalars(select(SetupChecklistRegression)).all()
    assert len(rows) == 1 and rows[0].resolved_at is not None

    # Broken again later: a new regression, a new notification.
    _remove_opening_hours(db_session, dealership_id)
    assert setup_checklist_regressions.run(db_session).opened == 1
    assert len(_regression_notifications(db_session)) == 2


def test_a_betrieb_being_set_up_is_not_checked(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    s.manager(client, db_session, dealership_id)
    assert setup_checklist_regressions.run(db_session).opened == 0
    assert _regression_notifications(db_session) == []


def test_the_daily_job_is_registered():
    from app.core.daily_scheduler import registered_job_names
    from app.worker import register_daily_jobs

    register_daily_jobs()
    assert "platform.setup_checklist.regressions" in registered_job_names()


def test_a_betrieb_trading_before_the_checklist_is_never_refused_nor_notified(client, db_session):
    """Review finding: the migration set every onboarded Betrieb active. It
    never went through the checklist, so nothing it does is refused and the
    nightly job raises no «regression» for an item it never completed."""

    dealership_id, _ = _trading_before_the_checklist(client, db_session)
    _remove_opening_hours(db_session, dealership_id)  # it may never have had any
    # Not refused by the checklist (publishing's own rules still apply).
    require_setup_item_for(db_session, dealership_id, SetupBlockedAction.MARKETPLACE_PUBLISHING)
    run = setup_checklist_regressions.run(db_session)
    assert (run.opened, run.notified) == (0, 0)
    assert _regression_notifications(db_session) == []
