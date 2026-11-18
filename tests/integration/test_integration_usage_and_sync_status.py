"""WP-6 PR-7, KAN-222: the usage endpoint (dealer-facing «Nutzung anzeigen»), per provider
counter and period.
The fleet-wide catalogue sync-status endpoint it once tested beside it was
retired by KAN-497 (Anto, 2026-10-31, option A) with the full catalogue
download it reported on (KAN-485); the test at the end pins that it is gone.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from app.core.auth import AccessRole, create_access_token
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from tests.token_support import dealer_group_of


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
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _platform_admin_token() -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=dealer_group_of(str(tid)),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=False,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- usage -------------------------------------------------------------------


def _connection(db_session, tenant_id):
    provider = _make_provider(db_session)
    return connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )


def _log(db_session, connection, operation: str, *, at: dt.datetime | None = None, status=CallStatus.SUCCESS):
    row = IntegrationCallLog(
        connection_id=connection.id, tenant_id=connection.tenant_id, capability=operation,
        status=status, duration_ms=5, correlation_id=uuid.uuid4(),
    )
    if at is not None:
        row.created_at = at
    db_session.add(row)
    db_session.commit()


def test_usage_endpoint_reports_zero_with_no_calls(client, db_session):
    tenant_id = uuid.uuid4()
    connection = _connection(db_session, tenant_id)

    response = client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=_bearer(_token(tenant_id)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totalCalls"] == 0 and body["counters"] == []
    assert body["indicative"] is True
    today = dt.datetime.now(ZoneInfo("Europe/Zurich")).date()
    assert body["periodTo"] == today.isoformat()
    assert body["periodFrom"] == (today - dt.timedelta(days=29)).isoformat()


def test_usage_counts_requests_per_provider_counter_verbatim(client, db_session):
    """KAN-222 (FR-A-23): the provider's own counter names, counted — a
    failed request is a request the provider counted too."""

    tenant_id = uuid.uuid4()
    connection = _connection(db_session, tenant_id)
    for _ in range(3):
        _log(db_session, connection, "caption_code")
    _log(db_session, connection, "oauth_token")
    _log(db_session, connection, "FtpUpload", status=CallStatus.ERROR)

    body = client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=_bearer(_token(tenant_id))).json()

    assert body["totalCalls"] == 5
    assert body["counters"] == [
        {"counter": "caption_code", "count": 3},
        {"counter": "FtpUpload", "count": 1},
        {"counter": "oauth_token", "count": 1},
    ]
    assert "cost" not in str(body).lower() and "chf" not in str(body).lower()  # Q1 A: counts only


def test_usage_honours_the_period_in_swiss_calendar_days(client, db_session):
    tenant_id = uuid.uuid4()
    connection = _connection(db_session, tenant_id)
    zurich = ZoneInfo("Europe/Zurich")
    # 2026-03-01 00:30 in Zurich is still 2026-02-28 in UTC: it belongs to March.
    _log(db_session, connection, "caption_code", at=dt.datetime(2026, 3, 1, 0, 30, tzinfo=zurich))
    _log(db_session, connection, "caption_code", at=dt.datetime(2026, 2, 28, 23, 30, tzinfo=zurich))
    _log(db_session, connection, "caption_code", at=dt.datetime(2026, 4, 1, 0, 0, tzinfo=zurich))

    body = client.get(
        f"/v1/integrations/connections/{connection.id}/usage?from=2026-03-01&to=2026-03-31",
        headers=_bearer(_token(tenant_id)),
    ).json()

    assert body["periodFrom"] == "2026-03-01" and body["periodTo"] == "2026-03-31"
    assert body["totalCalls"] == 1


def test_usage_refuses_a_reversed_or_overlong_period(client, db_session):
    tenant_id = uuid.uuid4()
    connection = _connection(db_session, tenant_id)
    url = f"/v1/integrations/connections/{connection.id}/usage"

    reversed_ = client.get(f"{url}?from=2026-03-31&to=2026-03-01", headers=_bearer(_token(tenant_id)))
    too_long = client.get(f"{url}?from=2025-01-01&to=2026-03-01", headers=_bearer(_token(tenant_id)))

    assert reversed_.status_code == 422 and reversed_.json()["error"]["details"]["reason"] == "usage_period_reversed"
    assert too_long.status_code == 422 and too_long.json()["error"]["details"]["reason"] == "usage_period_too_long"


def test_usage_of_another_betriebs_connection_is_a_404(client, db_session):
    connection = _connection(db_session, uuid.uuid4())

    response = client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=_bearer(_token(uuid.uuid4())))

    assert response.status_code == 404


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


# --- catalogue sync status (retired, KAN-497) ----------------------------------


def test_the_catalogue_sync_status_board_and_its_table_are_gone(client):
    """KAN-497: no full catalogue download exists since KAN-485 (ADR-023 as
    amended 2026-10-29), so the board that reported on it could only show a
    false «stale» alarm. The endpoint is not served to anyone, platform staff
    included, it is not in the published contract, and its model is gone (the migration drops the table;
    `test_migrated_schema_matches_models.py` holds the two together)."""

    from app.db import Base
    from app.main import app

    response = client.get("/v1/vehicle-mdm/catalogue-sync-status", headers=_bearer(_platform_admin_token()))

    # What answers now is vehicle-mdm's `/{vehicle_id}` route, refusing a
    # path segment that is no UUID: the board's route is not mounted.
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["errors"][0]["loc"] == ["path", "vehicle_id"]
    assert "/v1/vehicle-mdm/catalogue-sync-status" not in app.openapi()["paths"]
    assert "vehicle_provider_sync_state" not in Base.metadata.tables
