"""WP-6 PR-7: the usage-aggregation endpoint (dealer-facing "View usage")
and the fleet-wide catalogue sync-status endpoint (platform view's own
health board).
"""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.services import catalogue_sync


def _make_provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock", category="vehicle_data", display_name="auto-i-dat (mock)",
        auth_type="none", required_secret_slots=[], capability_codes=["vehicle_data"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _token(tenant_id: uuid.UUID, *, is_dealer_manager: bool = True) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _platform_admin_token() -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=False,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- usage -------------------------------------------------------------------


def test_usage_endpoint_reports_zero_with_no_calls(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    token = _token(tenant_id)

    response = client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["callsThisPeriod"] == 0
    assert body["indicative"] is True


def test_usage_endpoint_counts_recent_calls(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    for _ in range(3):
        db_session.add(
            IntegrationCallLog(
                connection_id=connection.id, tenant_id=tenant_id, capability="vehicle_data",
                status=CallStatus.SUCCESS, duration_ms=5, correlation_id=uuid.uuid4(),
            )
        )
    db_session.commit()
    token = _token(tenant_id)

    response = client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=_bearer(token))
    assert response.json()["callsThisPeriod"] == 3


def test_usage_endpoint_requires_manager_flag(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    non_manager = _token(tenant_id, is_dealer_manager=False)

    response = client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=_bearer(non_manager))
    assert response.status_code == 403


# --- catalogue sync status ----------------------------------------------------


def test_catalogue_sync_status_requires_platform_admin(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    response = client.get("/v1/vehicle-mdm/catalogue-sync-status", headers=_bearer(_token(tenant_id)))
    assert response.status_code == 403


def test_catalogue_sync_status_lists_every_tenant_and_flags_staleness(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    response = client.get("/v1/vehicle-mdm/catalogue-sync-status", headers=_bearer(_platform_admin_token()))
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["tenantId"] == str(tenant_id)
    assert rows[0]["stale"] is False  # just seeded, today's watermark
