"""KAN-741: a provider without test access (`supports_sandbox` false —
AutoScout24, DAT, every AI provider) holds no sandbox connection. The create
is refused with a reason the SPA translates, and a provider cannot be flipped
to «no sandbox» while sandbox connections to it still exist — the two ways
such an unusable connection could come to be.
"""

import uuid

from app.integration.models.connection import IntegrationConnection
from tests.integration.test_integration_gateway import _make_provider
from tests.integration.test_integration_idempotency import (
    _connection_body,
    _count,
    _headers,
    _manager_token,
    _platform_admin_token,
)


def test_a_sandbox_connection_to_a_provider_without_one_is_refused(client, db_session):
    provider = _make_provider(db_session, provider_code="autoscout24_t", display_name="AutoScout24", supports_sandbox=False)
    tenant_id = uuid.uuid4()

    response = client.post(
        "/v1/integrations/connections", json=_connection_body(provider, "sandbox"), headers=_headers(_manager_token(tenant_id))
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "provider_has_no_sandbox", "providerCode": "autoscout24_t"}
    assert _count(db_session, IntegrationConnection, IntegrationConnection.tenant_id == tenant_id) == 0


def test_the_same_provider_connects_as_production(client, db_session):
    provider = _make_provider(db_session, provider_code="dat_t", display_name="DAT", supports_sandbox=False)

    response = client.post(
        "/v1/integrations/connections", json=_connection_body(provider, "production"), headers=_headers(_manager_token())
    )

    assert response.status_code == 201, response.text
    assert response.json()["environment"] == "production"


def test_a_provider_with_a_sandbox_still_takes_a_sandbox_connection(client, db_session):
    provider = _make_provider(db_session)

    response = client.post(
        "/v1/integrations/connections", json=_connection_body(provider, "sandbox"), headers=_headers(_manager_token())
    )

    assert response.status_code == 201, response.text


def _patch_provider(client, provider, body: dict):
    return client.patch(
        f"/v1/integrations/providers/{provider.id}",
        json=body,
        headers=_headers(_platform_admin_token(), **{"If-Match": str(provider.version)}),
    )


def test_a_provider_is_not_flipped_to_no_sandbox_while_sandbox_connections_exist(client, db_session):
    provider = _make_provider(db_session)
    created = client.post(
        "/v1/integrations/connections", json=_connection_body(provider, "sandbox"), headers=_headers(_manager_token())
    )
    assert created.status_code == 201, created.text

    response = _patch_provider(client, provider, {"supportsSandbox": False})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "provider_sandbox_connections_exist", "count": 1}
    db_session.refresh(provider)
    assert provider.supports_sandbox is True


def test_a_provider_with_only_production_connections_is_flipped(client, db_session):
    provider = _make_provider(db_session)
    created = client.post(
        "/v1/integrations/connections", json=_connection_body(provider, "production"), headers=_headers(_manager_token())
    )
    assert created.status_code == 201, created.text

    response = _patch_provider(client, provider, {"supportsSandbox": False})

    assert response.status_code == 200, response.text
    assert response.json()["supportsSandbox"] is False
