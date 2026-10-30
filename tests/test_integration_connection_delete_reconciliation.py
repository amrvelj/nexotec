"""KAN-494: deleting a connection that has notifications.

Anto's ruling (2026-10-29, option A — the same rule as KAN-470's call log):
a deleted connection's notification history is kept, and the nightly
reconciliation check on `integration_notification.connection_id` skips the
ids of deleted connections. A deleted connection is one whose delete is on
the audit trail (`delete_connection` records it); an id naming a connection
that vanished any other way is still drift.
"""

import datetime as dt
import uuid

import pytest

from app.core.audit import record_audit_event
from app.core.reconciliation import ReconciliationAlarm
from app.integration import reconciliation as integration_reconciliation
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.notification import IntegrationNotification, NotificationKind
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from tests.test_integration_gateway import _make_provider

_LABEL = "integration_notification.connection_id -> integration_connection.id"


def _notification(db_session, *, connection_id: uuid.UUID, tenant_id: uuid.UUID) -> IntegrationNotification:
    row = IntegrationNotification(
        connection_id=connection_id,
        tenant_id=tenant_id,
        kind=NotificationKind.EXPIRY_WARNING,
        threshold_days=30,
        sent_date=dt.date(2026, 10, 1),
        recipient="manager@example.test",
        summary="Connection 'auto-i-dat Hauptkonto' expires in 30 day(s).",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _notification_orphans(db_session) -> list:
    try:
        integration_reconciliation.run(db_session)
    except ReconciliationAlarm as alarm:
        return [o for o in alarm.orphans if o.check_label == _LABEL]
    return []


def test_a_deleted_connections_notifications_are_kept_and_are_no_reconciliation_finding(db_session):
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=_make_provider(db_session).id, display_name="auto-i-dat Hauptkonto",
            environment=ConnectionEnvironment.SANDBOX,
        ),
        actor_id=None,
    )
    connection_id = connection.id
    notification = _notification(db_session, connection_id=connection_id, tenant_id=tenant_id)
    notification_id = notification.id

    connection_service.delete_connection(db_session, connection=connection, confirm=True, actor_id=None)

    db_session.expire_all()
    kept = db_session.get(IntegrationNotification, notification_id)
    assert kept is not None and kept.connection_id == connection_id
    assert _notification_orphans(db_session) == []


def test_a_notification_naming_a_connection_that_was_never_deleted_is_still_drift(db_session):
    """The skip covers audited deletes only: an id that names no connection
    and no recorded delete is reported, as before."""
    dangling = uuid.uuid4()
    notification = _notification(db_session, connection_id=dangling, tenant_id=uuid.uuid4())

    orphans = _notification_orphans(db_session)

    assert [(o.source_row_id, o.dangling_value) for o in orphans] == [(notification.id, dangling)]


@pytest.mark.parametrize("action", ["update", "disable"])
def test_only_a_delete_on_the_audit_trail_excuses_a_missing_connection(db_session, action):
    dangling = uuid.uuid4()
    record_audit_event(
        db_session, entity_type="integration_connection", entity_id=dangling, tenant_id=None,
        action=action, actor_id=None,
    )
    db_session.commit()
    notification = _notification(db_session, connection_id=dangling, tenant_id=uuid.uuid4())

    assert [o.source_row_id for o in _notification_orphans(db_session)] == [notification.id]
