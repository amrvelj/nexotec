"""WP-6 PR-6, ADR-025: expiry warnings fire only at exactly T-30/14/7,
never duplicate, and the daily support digest is one send, never
per-event.
"""

import datetime as dt
import uuid

from app.core.notifications import LoggingNotificationSender, get_notification_sender, set_notification_sender
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope
from app.integration.models.notification import IntegrationNotification, NotificationKind
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import notifications as notification_service
from app.platform.models.user import User, UserRole, UserStatus

TODAY = dt.date(2026, 8, 31)


class _RecordingSender:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)


def _reset_sender():
    set_notification_sender(LoggingNotificationSender())


def _make_provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock", category="vehicle_data", display_name="auto-i-dat (mock)",
        auth_type="none", required_secret_slots=[], capability_codes=["vehicle_data"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider, *, tenant_id, expires_at=None):
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    connection.expires_at = expires_at
    db_session.commit()
    db_session.refresh(connection)
    return connection


def _make_manager(db_session, *, tenant_id, email):
    user = User(
        tenant_id=tenant_id, first_name="Ada", last_name="Manager", email=email, role=UserRole.SALES,
        access_roles=[], is_dealer_manager=True, status=UserStatus.ACTIVE, auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(user)
    db_session.commit()
    return user


# --- expiry warnings ---------------------------------------------------------


def test_expiry_warning_fires_at_exactly_thirty_days(db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_manager(db_session, tenant_id=tenant_id, email="manager30@example.com")
    expires_at = dt.datetime.combine(TODAY + dt.timedelta(days=30), dt.time.min, tzinfo=dt.UTC)
    _make_connection(db_session, provider, tenant_id=tenant_id, expires_at=expires_at)

    sent = notification_service.check_and_send_expiry_warnings(db_session, today=TODAY)

    assert len(sent) == 1
    assert sent[0].threshold_days == 30


def test_expiry_warning_does_not_fire_at_twenty_nine_or_thirty_one_days(db_session):
    provider = _make_provider(db_session)
    for offset in (29, 31):
        tenant_id = uuid.uuid4()
        _make_manager(db_session, tenant_id=tenant_id, email=f"manager{offset}@example.com")
        _make_connection(
            db_session, provider, tenant_id=tenant_id,
            expires_at=dt.datetime.combine(TODAY + dt.timedelta(days=offset), dt.time.min, tzinfo=dt.UTC),
        )

    sent = notification_service.check_and_send_expiry_warnings(db_session, today=TODAY)

    assert sent == []


def test_expiry_warning_never_duplicates_the_same_threshold_the_same_day(db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_manager(db_session, tenant_id=tenant_id, email="manager14@example.com")
    _make_connection(
        db_session, provider, tenant_id=tenant_id,
        expires_at=dt.datetime.combine(TODAY + dt.timedelta(days=14), dt.time.min, tzinfo=dt.UTC),
    )

    first = notification_service.check_and_send_expiry_warnings(db_session, today=TODAY)
    second = notification_service.check_and_send_expiry_warnings(db_session, today=TODAY)

    assert len(first) == 1
    assert second == []


def test_expiry_warning_skips_platform_scope_connections(db_session):
    provider = _make_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=None,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="platform-wide", environment=ConnectionEnvironment.SANDBOX,
            scope=ConnectionScope.PLATFORM,
        ),
        actor_id=uuid.uuid4(),
    )
    connection.expires_at = dt.datetime.combine(TODAY + dt.timedelta(days=7), dt.time.min, tzinfo=dt.UTC)
    db_session.commit()

    sent = notification_service.check_and_send_expiry_warnings(db_session, today=TODAY)

    assert sent == []


def test_expiry_warning_sends_to_the_dealer_manager(db_session, monkeypatch):
    recording = _RecordingSender()
    set_notification_sender(recording)
    try:
        provider = _make_provider(db_session)
        tenant_id = uuid.uuid4()
        _make_manager(db_session, tenant_id=tenant_id, email="realmanager@example.com")
        _make_connection(
            db_session, provider, tenant_id=tenant_id,
            expires_at=dt.datetime.combine(TODAY + dt.timedelta(days=7), dt.time.min, tzinfo=dt.UTC),
        )

        notification_service.check_and_send_expiry_warnings(db_session, today=TODAY)

        assert len(recording.sent) == 1
        assert recording.sent[0].recipient == "realmanager@example.com"
    finally:
        _reset_sender()


# --- break-glass -------------------------------------------------------------


def test_break_glass_notification_reaches_the_manager_every_time(db_session):
    recording = _RecordingSender()
    set_notification_sender(recording)
    try:
        provider = _make_provider(db_session)
        tenant_id = uuid.uuid4()
        _make_manager(db_session, tenant_id=tenant_id, email="breakglass@example.com")
        connection = _make_connection(db_session, provider, tenant_id=tenant_id)

        notification_service.notify_break_glass_access(db_session, connection=connection, actor_id=uuid.uuid4(), reason="support ticket #1")
        notification_service.notify_break_glass_access(db_session, connection=connection, actor_id=uuid.uuid4(), reason="support ticket #2")

        assert len(recording.sent) == 2  # never deduplicated by day
        rows = db_session.query(IntegrationNotification).filter_by(kind=NotificationKind.BREAK_GLASS_ACCESS).all()
        assert len(rows) == 2
    finally:
        _reset_sender()


def test_break_glass_notification_is_a_noop_for_platform_scope_connections(db_session):
    provider = _make_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=None,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="platform-wide", environment=ConnectionEnvironment.SANDBOX,
            scope=ConnectionScope.PLATFORM,
        ),
        actor_id=uuid.uuid4(),
    )

    result = notification_service.notify_break_glass_access(db_session, connection=connection, actor_id=uuid.uuid4(), reason="test")

    assert result is None


# --- daily digest --------------------------------------------------------------


def test_digest_sends_once_when_there_is_something_to_report(db_session):
    recording = _RecordingSender()
    set_notification_sender(recording)
    try:
        digest = notification_service.send_daily_support_digest(
            db_session, today=TODAY, warnings=[], alarms=[uuid.uuid4(), uuid.uuid4()]
        )
        assert digest is not None
        assert len(recording.sent) == 1
    finally:
        _reset_sender()


def test_digest_is_a_noop_with_nothing_to_report(db_session):
    digest = notification_service.send_daily_support_digest(db_session, today=TODAY, warnings=[], alarms=[])
    assert digest is None


def test_digest_never_sends_twice_the_same_day(db_session):
    recording = _RecordingSender()
    set_notification_sender(recording)
    try:
        first = notification_service.send_daily_support_digest(db_session, today=TODAY, warnings=[], alarms=[uuid.uuid4()])
        second = notification_service.send_daily_support_digest(db_session, today=TODAY, warnings=[], alarms=[uuid.uuid4()])

        assert first is not None
        assert second is None
        assert len(recording.sent) == 1
    finally:
        _reset_sender()


def test_get_notification_sender_returns_a_logging_sender_by_default():
    _reset_sender()
    assert isinstance(get_notification_sender(), LoggingNotificationSender)
