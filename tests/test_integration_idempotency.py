"""KAN-266 step 7 (integration): every integration POST honours Idempotency-Key.

Per route: the same key twice makes one record (or runs the action once) and
answers the same; the same key with a different request is a 409 for the
key. Test, enable and disable have no body, so their 409 case is the same
key on another connection. The mechanism itself is pinned in
tests/test_idempotent_route.py.

"Test connection" is a logged, potentially billed provider call: a retried
test replays its first answer instead of calling the provider again. Enable
and disable are If-Match transitions, retried with the If-Match they were
first sent with.
"""

import uuid

from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from tests.test_integration_gateway import _make_provider


def _manager_token(tenant_id: uuid.UUID | None = None) -> str:
    tenant_id = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(),
        is_dealer_manager=True,
    )


def _platform_admin_token() -> str:
    tenant_id = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
    )


def _headers(token: str, key: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", **extra}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def _assert_key_conflict(response, key: str) -> None:
    """A 409 for the reused key, not for some other conflict."""

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["idempotencyKey"] == key


def _twice(client, path: str, token: str, body: dict | None, **extra: str):
    key = str(uuid.uuid4())
    first = client.post(path, json=body, headers=_headers(token, key, **extra))
    second = client.post(path, json=body, headers=_headers(token, key, **extra))
    assert first.status_code in (200, 201), first.text
    assert second.status_code == first.status_code, second.text
    assert second.json() == first.json()
    return key, first.json()


def _connection_body(provider: IntegrationProvider, environment: str = "sandbox", name: str = "auto-i-dat") -> dict:
    return {"providerId": str(provider.id), "displayName": name, "environment": environment}


def _connection(client, token: str, provider: IntegrationProvider, environment: str = "sandbox") -> dict:
    response = client.post(
        "/v1/integrations/connections", json=_connection_body(provider, environment), headers=_headers(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- POST /v1/integrations/connections ------------------------------------------------


def test_create_connection_twice_under_one_key_makes_one_connection(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    token = _manager_token(tenant_id)

    _twice(client, "/v1/integrations/connections", token, _connection_body(provider))

    assert _count(db_session, IntegrationConnection, IntegrationConnection.tenant_id == tenant_id) == 1


def test_create_connection_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    token = _manager_token(tenant_id)
    key, _ = _twice(client, "/v1/integrations/connections", token, _connection_body(provider))

    response = client.post(
        "/v1/integrations/connections",
        json=_connection_body(provider, environment="production"),
        headers=_headers(token, key),
    )

    _assert_key_conflict(response, key)
    assert _count(db_session, IntegrationConnection, IntegrationConnection.tenant_id == tenant_id) == 1


# --- POST /v1/integrations/connections/{id}/test (no body) -----------------------------


def test_a_retried_connection_test_calls_the_provider_once(client, db_session):
    provider = _make_provider(db_session)
    token = _manager_token()
    connection = _connection(client, token, provider)

    def calls() -> int:
        return _count(db_session, IntegrationCallLog, IntegrationCallLog.connection_id == uuid.UUID(connection["id"]))

    _, tested = _twice(client, f"/v1/integrations/connections/{connection['id']}/test", token, None)
    keyed_pair = calls()
    client.post(f"/v1/integrations/connections/{connection['id']}/test", headers=_headers(token))
    one_more_test = calls() - keyed_pair

    assert tested["status"] == "connected"
    assert one_more_test > 0
    # The keyed pair made the provider calls of one test, not two: the
    # replay called nothing.
    assert keyed_pair == one_more_test


def test_a_connection_test_with_a_reused_key_on_another_connection_is_a_409(client, db_session):
    provider = _make_provider(db_session)
    token = _manager_token()
    first = _connection(client, token, provider, "sandbox")
    other = _connection(client, token, provider, "production")
    key, _ = _twice(client, f"/v1/integrations/connections/{first['id']}/test", token, None)

    response = client.post(f"/v1/integrations/connections/{other['id']}/test", headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, IntegrationCallLog, IntegrationCallLog.connection_id == uuid.UUID(other["id"])) == 0


# --- POST /v1/integrations/connections/{id}/disable and /enable (If-Match, no body) -----


def test_a_retried_disable_replays_its_success_instead_of_a_version_conflict(client, db_session):
    provider = _make_provider(db_session)
    token = _manager_token()
    connection = _connection(client, token, provider)

    _, disabled = _twice(
        client, f"/v1/integrations/connections/{connection['id']}/disable", token, None,
        **{"If-Match": str(connection["version"])},
    )

    assert disabled["enabled"] is False
    assert disabled["version"] == connection["version"] + 1


def test_a_disable_with_a_reused_key_on_another_connection_is_a_409(client, db_session):
    provider = _make_provider(db_session)
    token = _manager_token()
    first = _connection(client, token, provider, "sandbox")
    other = _connection(client, token, provider, "production")
    key, _ = _twice(
        client, f"/v1/integrations/connections/{first['id']}/disable", token, None,
        **{"If-Match": str(first["version"])},
    )

    response = client.post(
        f"/v1/integrations/connections/{other['id']}/disable",
        headers=_headers(token, key, **{"If-Match": str(other["version"])}),
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(IntegrationConnection, uuid.UUID(other["id"])).enabled is True


def _disabled(client, token: str, connection: dict) -> dict:
    response = client.post(
        f"/v1/integrations/connections/{connection['id']}/disable",
        headers=_headers(token, **{"If-Match": str(connection["version"])}),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_retried_enable_replays_its_success_instead_of_a_version_conflict(client, db_session):
    provider = _make_provider(db_session)
    token = _manager_token()
    connection = _disabled(client, token, _connection(client, token, provider))

    _, enabled = _twice(
        client, f"/v1/integrations/connections/{connection['id']}/enable", token, None,
        **{"If-Match": str(connection["version"])},
    )

    assert enabled["enabled"] is True
    assert enabled["version"] == connection["version"] + 1


def test_an_enable_with_a_reused_key_on_another_connection_is_a_409(client, db_session):
    provider = _make_provider(db_session)
    token = _manager_token()
    first = _disabled(client, token, _connection(client, token, provider, "sandbox"))
    other = _disabled(client, token, _connection(client, token, provider, "production"))
    key, _ = _twice(
        client, f"/v1/integrations/connections/{first['id']}/enable", token, None,
        **{"If-Match": str(first["version"])},
    )

    response = client.post(
        f"/v1/integrations/connections/{other['id']}/enable",
        headers=_headers(token, key, **{"If-Match": str(other["version"])}),
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(IntegrationConnection, uuid.UUID(other["id"])).enabled is False


# --- POST /v1/integrations/providers (platform admin) ------------------------------------


def _provider_body(code: str) -> dict:
    return {"providerCode": code, "category": "vehicle_data", "displayName": code, "authType": "none"}


def test_create_provider_twice_under_one_key_makes_one_provider(client, db_session):
    token = _platform_admin_token()
    code = f"provider_{uuid.uuid4().hex[:8]}"

    _twice(client, "/v1/integrations/providers", token, _provider_body(code))

    assert _count(db_session, IntegrationProvider, IntegrationProvider.provider_code == code) == 1


def test_create_provider_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token = _platform_admin_token()
    code, other = f"provider_{uuid.uuid4().hex[:8]}", f"provider_{uuid.uuid4().hex[:8]}"
    key, _ = _twice(client, "/v1/integrations/providers", token, _provider_body(code))

    response = client.post("/v1/integrations/providers", json=_provider_body(other), headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, IntegrationProvider, IntegrationProvider.provider_code == other) == 0
