"""KAN-470: deleting a connection that has made a provider call.

Anto's ruling (2026-10-26, option A; migration plan approved 2026-10-28):
the call log outlives its connection until ADR-024 retention purges it.
`integration_call_log.connection_id` is a plain id (no foreign key), with
the connection's name copied beside it at the time of the call, so the log
still says which account a call was made on. Before the fix the DELETE hit
the foreign key: ForeignKeyViolation, 500.
"""

import datetime as dt
import uuid

from app.core.audit import list_audit_events
from app.core.auth import AccessRole, create_access_token
from app.core.notifications import LoggingNotificationSender, set_notification_sender
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import PayloadKind
from app.integration.models.connection import ConnectionEnvironment, IntegrationConnection
from app.integration.models.notification import IntegrationNotification, NotificationKind
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway, retention
from tests.test_integration_gateway import _make_provider
from tests.token_support import dealer_group_of


def _headers(*, platform_admin: bool = False) -> dict[str, str]:
    tenant_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}) if platform_admin else frozenset(),
        is_dealer_manager=not platform_admin,
    )
    return {"Authorization": f"Bearer {token}"}


def _used_connection(client, db_session, headers: dict[str, str]) -> dict:
    """A connection that has made one provider call: «Test connection»
    writes one call-log row, as it does in Dealer Administration."""
    provider = _make_provider(db_session)
    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "auto-i-dat Hauptkonto", "environment": "sandbox"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    tested = client.post(f"/v1/integrations/connections/{created.json()['id']}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    return tested.json()


def _call_logs(db_session, connection_id: uuid.UUID) -> list[IntegrationCallLog]:
    db_session.expire_all()
    return list(
        db_session.query(IntegrationCallLog).filter(IntegrationCallLog.connection_id == connection_id).all()
    )


def test_a_call_records_the_connection_name_beside_its_id(client, db_session):
    connection = _used_connection(client, db_session, _headers())

    logs = _call_logs(db_session, uuid.UUID(connection["id"]))
    assert logs
    assert {log.connection_label for log in logs} == {"auto-i-dat Hauptkonto"}
    assert all(log.connection_label_refreshed_at is not None for log in logs)


def test_deleting_a_connection_that_made_a_call_keeps_its_call_log_with_the_name_at_call_time(
    client, db_session
):
    headers = _headers()
    connection = _used_connection(client, db_session, headers)
    connection_id = uuid.UUID(connection["id"])

    # A rename after the call does not rewrite the log: the label is the
    # name the account had when the call was made.
    renamed = client.patch(
        f"/v1/integrations/connections/{connection_id}",
        json={"displayName": "auto-i-dat Neu"},
        headers={**headers, "If-Match": str(connection["version"])},
    )
    assert renamed.status_code == 200, renamed.text

    response = client.request(
        "DELETE",
        f"/v1/integrations/connections/{connection_id}",
        json={"confirm": True},
        headers={**headers, "If-Match": str(renamed.json()["version"])},
    )
    assert response.status_code == 204, response.text

    db_session.expire_all()
    assert db_session.get(IntegrationConnection, connection_id) is None
    logs = _call_logs(db_session, connection_id)
    assert logs
    assert {log.connection_label for log in logs} == {"auto-i-dat Hauptkonto"}
    # The delete itself is still confirmed and audited (rule 10).
    events = list_audit_events(
        db_session, entity_type="integration_connection", entity_id=connection_id, tenant_id=None
    )
    assert [e.action for e in events if e.action == "delete"] == ["delete"]


def test_retention_still_purges_a_deleted_connections_call_log(client, db_session):
    headers = _headers()
    connection = _used_connection(client, db_session, headers)
    connection_id = uuid.UUID(connection["id"])
    response = client.request(
        "DELETE",
        f"/v1/integrations/connections/{connection_id}",
        json={"confirm": True},
        headers={**headers, "If-Match": str(connection["version"])},
    )
    assert response.status_code == 204, response.text

    for log in _call_logs(db_session, connection_id):
        log.created_at = log.created_at - dt.timedelta(days=800)
    db_session.commit()

    retention.purge_call_log_metadata(db_session)
    db_session.commit()
    assert _call_logs(db_session, connection_id) == []


class _RecordingSender:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)


def test_break_glass_read_of_a_deleted_connections_payload_still_notifies_its_betrieb(client, db_session):
    """The raw payload outlives the connection too (its own ADR-024 tier):
    a platform admin can still read it, and the Betrieb is still told, under
    the name the connection had when the call was made."""
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat Filiale", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )
    log = gateway.record_call(
        db_session, connection=connection, capability="vehicle_data", status=gateway.CallStatus.SUCCESS,
        duration_ms=10,
    )
    payload = retention.capture_call_payload(db_session, call_log=log, kind=PayloadKind.SUCCESS, payload="<raw/>")
    db_session.commit()
    connection_service.delete_connection(db_session, connection=connection, confirm=True, actor_id=uuid.uuid4())

    recording = _RecordingSender()
    set_notification_sender(recording)
    try:
        response = client.get(
            f"/v1/integrations/call-payloads/{payload.id}", params={"reason": "support ticket #7"},
            headers=_headers(platform_admin=True),
        )
    finally:
        set_notification_sender(LoggingNotificationSender())
    assert response.status_code == 200, response.text
    assert response.json()["payload"] == "<raw/>"

    db_session.expire_all()
    [notification] = db_session.query(IntegrationNotification).filter(
        IntegrationNotification.tenant_id == tenant_id,
        IntegrationNotification.kind == NotificationKind.BREAK_GLASS_ACCESS,
    ).all()
    assert "auto-i-dat Filiale" in notification.summary
    # The connection is gone: the notification names no connection row
    # (the reconciliation check on integration_notification.connection_id
    # would report one that points nowhere).
    assert notification.connection_id is None
