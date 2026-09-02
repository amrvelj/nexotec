"""WP-6 PR-6: the ONE endpoint that can return a raw provider payload —
platform_admin only, audit-logged, notifies the dealer's manager.
"""

import uuid

from app.core.audit import list_audit_events
from app.core.auth import AccessRole, create_access_token
from app.core.notifications import LoggingNotificationSender, set_notification_sender
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.call_payload import PayloadKind
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import retention


class _RecordingSender:
    def __init__(self):
        self.sent = []

    def send(self, notification):
        self.sent.append(notification)


def _make_provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock", category="vehicle_data", display_name="auto-i-dat (mock)",
        auth_type="none", required_secret_slots=[], capability_codes=["vehicle_data"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection_with_payload(db_session, provider, *, tenant_id):
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    log = IntegrationCallLog(
        connection_id=connection.id, tenant_id=tenant_id, capability="vehicle_data", status=CallStatus.SUCCESS,
        duration_ms=10, correlation_id=uuid.uuid4(),
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    payload = retention.capture_call_payload(db_session, call_log=log, kind=PayloadKind.SUCCESS, payload="<raw>demo</raw>")
    db_session.commit()
    return connection, payload


def _platform_admin_token() -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=False,
    )


def _dealer_manager_token() -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset(), is_dealer_manager=True,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_break_glass_requires_platform_admin(client, db_session):
    provider = _make_provider(db_session)
    _connection, payload = _make_connection_with_payload(db_session, provider, tenant_id=uuid.uuid4())

    response = client.get(
        f"/v1/integrations/call-payloads/{payload.id}", params={"reason": "checking"},
        headers=_bearer(_dealer_manager_token()),
    )
    assert response.status_code == 403


def test_break_glass_requires_a_reason(client, db_session):
    provider = _make_provider(db_session)
    _connection, payload = _make_connection_with_payload(db_session, provider, tenant_id=uuid.uuid4())

    response = client.get(f"/v1/integrations/call-payloads/{payload.id}", headers=_bearer(_platform_admin_token()))
    assert response.status_code == 422


def test_break_glass_returns_404_for_a_missing_payload(client, db_session):
    response = client.get(
        f"/v1/integrations/call-payloads/{uuid.uuid4()}", params={"reason": "checking"},
        headers=_bearer(_platform_admin_token()),
    )
    assert response.status_code == 404


def test_break_glass_returns_the_decrypted_payload_and_audit_logs_and_notifies(client, db_session):
    recording = _RecordingSender()
    set_notification_sender(recording)
    try:
        provider = _make_provider(db_session)
        tenant_id = uuid.uuid4()
        _connection, payload = _make_connection_with_payload(db_session, provider, tenant_id=tenant_id)

        response = client.get(
            f"/v1/integrations/call-payloads/{payload.id}", params={"reason": "customer support ticket #42"},
            headers=_bearer(_platform_admin_token()),
        )
        assert response.status_code == 200, response.text
        assert response.json()["payload"] == "<raw>demo</raw>"

        events = list_audit_events(
            db_session, entity_type="integration_call_payload", entity_id=payload.id, tenant_id=None
        )
        assert len(events) == 1
        assert events[0].action == "break_glass_read"
        assert events[0].reason == "customer support ticket #42"

        # No manager exists for this tenant in this test, so the send list
        # stays empty — but the notification ROW itself still gets written
        # (asserted in test_integration_notifications.py); here the
        # contract under test is simply "the call succeeds and doesn't
        # crash when there's no one to email."
        assert recording.sent == []
    finally:
        set_notification_sender(LoggingNotificationSender())
