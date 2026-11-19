"""KAN-763: a daily integration job deletes mailbox link states (KAN-215) used or expired more
than a day ago, with its own run record; a live link and one spent within the day stay."""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core import daily_scheduler
from app.core.base import utcnow
from app.core.daily_scheduler_model import DailyJobRun
from app.integration import daily_jobs
from app.integration.models.mail import MailLinkLeg, MailLinkState, MailProviderCode, SendingIdentityKind
from app.integration.services.mail_link import LINK_TTL, purge_spent_link_states
from app.worker import register_daily_jobs

JOB = "integration.mail_link_states.purge"


def _state(db_session, *, created: dt.datetime, consumed: dt.datetime | None = None) -> uuid.UUID:
    row = MailLinkState(
        state_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        kind=SendingIdentityKind.USER_MAILBOX,
        provider_code=MailProviderCode.MICROSOFT_365,
        leg=next(iter(MailLinkLeg)),
        expires_at=created + LINK_TTL,
        consumed_at=consumed,
    )
    db_session.add(row)
    db_session.commit()
    return row.id


def _remaining(db_session) -> set[uuid.UUID]:
    db_session.expire_all()
    return set(db_session.scalars(select(MailLinkState.id)))


def test_a_spent_state_goes_after_a_day_and_a_live_one_stays(db_session):
    now = utcnow()
    expired_long_ago = _state(db_session, created=now - dt.timedelta(days=2))
    used_long_ago = _state(db_session, created=now - dt.timedelta(days=3), consumed=now - dt.timedelta(days=3))
    live = _state(db_session, created=now - dt.timedelta(minutes=2))
    used_today = _state(db_session, created=now - dt.timedelta(hours=2), consumed=now - dt.timedelta(hours=2))
    expired_today = _state(db_session, created=now - dt.timedelta(hours=5))

    assert purge_spent_link_states(db_session, now=now) == 2

    assert _remaining(db_session) == {live, used_today, expired_today}
    assert expired_long_ago not in _remaining(db_session) and used_long_ago not in _remaining(db_session)


def test_the_job_is_registered_and_its_run_is_recorded(db_session):
    saved = dict(daily_scheduler._REGISTRY)
    daily_scheduler._REGISTRY.clear()
    try:
        register_daily_jobs()
        names = daily_scheduler.registered_job_names()
        assert daily_scheduler._REGISTRY[JOB] is daily_jobs.run_daily_mail_link_state_purge
        # Retention, not a repair: it runs before the two purges registered last.
        assert names[-1] == "vehicle.configurations.purge_unattached"
        assert names.index(JOB) < names.index("valuation.provider_quotes.purge_unused")

        stale = _state(db_session, created=utcnow() - dt.timedelta(days=2))
        daily_scheduler._REGISTRY.clear()
        daily_scheduler.register_daily_job(JOB, daily_jobs.run_daily_mail_link_state_purge)
        assert daily_scheduler.run_due_daily_jobs(db_session) == [JOB]
    finally:
        daily_scheduler._REGISTRY.clear()
        daily_scheduler._REGISTRY.update(saved)

    assert stale not in _remaining(db_session)
    runs = db_session.scalars(select(DailyJobRun).where(DailyJobRun.job_name == JOB)).all()
    assert len(runs) == 1
