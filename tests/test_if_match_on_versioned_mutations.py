"""KAN-396: the three mutations of a versioned entity that took no If-Match
now do (CLAUDE.md, API conventions; Anto 2026-10-20, option A: required).

Per route: a save built on a stale read is a 409 and changes nothing (the
lost update the ticket reproduced: two saves from the same read both landed);
a missing header is the convention's 400; the current version lands. The
architecture guard that keeps a new route from forgetting it is
tests/architecture/test_versioned_mutations_take_if_match.py.
"""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.integration.models.connection import IntegrationConnection
from tests.test_integration_gateway import _make_provider


def _token(*roles: AccessRole, manager: bool = False) -> dict[str, str]:
    tenant_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(roles),
        is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


def _stock_item(client, headers: dict[str, str]) -> dict:
    response = client.post(
        "/v1/inventory/stock-items", json={"vehicleLabel": "Škoda Octavia", "condition": "new"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _options(code: str, price: str) -> dict:
    return {"basePrice": "30000.00", "options": [{"code": code, "label": code, "price": price}]}


# --- PUT /v1/inventory/stock-items/{id}/options --------------------------------------


def test_options_put_from_a_stale_read_is_a_409_not_a_lost_update(client):
    headers = _token(AccessRole.INVENTORY)
    item = _stock_item(client, headers)
    path = f"/v1/inventory/stock-items/{item['id']}/options"
    read_version = str(item["version"])

    first = client.put(path, json=_options("PA1", "1500.00"), headers={**headers, "If-Match": read_version})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == item["version"] + 1

    second = client.put(path, json=_options("PB2", "900.00"), headers={**headers, "If-Match": read_version})
    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"] == {
        "currentVersion": item["version"] + 1,
        "ifMatchVersion": item["version"],
    }

    options = client.get(path, headers=headers).json()
    assert [o["code"] for o in options] == ["PA1"]


def test_options_put_without_if_match_is_refused(client):
    headers = _token(AccessRole.INVENTORY)
    item = _stock_item(client, headers)
    response = client.put(
        f"/v1/inventory/stock-items/{item['id']}/options", json=_options("PA1", "1.00"), headers=headers
    )
    assert response.status_code == 400, response.text
    assert client.get(f"/v1/inventory/stock-items/{item['id']}/options", headers=headers).json() == []


# --- PATCH /v1/inventory/stock-items/{id}/publishing/{channel} ------------------------


def test_listing_text_patch_from_a_stale_read_is_a_409_not_a_lost_update(client):
    headers = _token(AccessRole.INVENTORY)
    item = _stock_item(client, headers)
    path = f"/v1/inventory/stock-items/{item['id']}/publishing/autoscout24"
    row = client.get(path, headers=headers).json()
    read_version = str(row["version"])

    first = client.patch(path, json={"zusatztitel": "Anna's title"}, headers={**headers, "If-Match": read_version})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == row["version"] + 1

    second = client.patch(path, json={"zusatztitel": "Beat's title"}, headers={**headers, "If-Match": read_version})
    assert second.status_code == 409, second.text

    assert client.get(path, headers=headers).json()["zusatztitel"] == "Anna's title"


def test_listing_text_patch_without_if_match_is_refused(client):
    headers = _token(AccessRole.INVENTORY)
    item = _stock_item(client, headers)
    path = f"/v1/inventory/stock-items/{item['id']}/publishing/carmarket"
    response = client.patch(path, json={"zusatztitel": "x"}, headers=headers)
    assert response.status_code == 400, response.text
    assert client.get(path, headers=headers).json()["zusatztitel"] is None


def test_listing_text_versions_are_per_channel(client):
    """If-Match names the channel's own row: a save on one channel never
    stales another's."""

    headers = _token(AccessRole.INVENTORY)
    item = _stock_item(client, headers)
    base = f"/v1/inventory/stock-items/{item['id']}/publishing"
    as24 = client.get(f"{base}/autoscout24", headers=headers).json()
    autolina = client.get(f"{base}/autolina", headers=headers).json()
    assert (
        client.patch(
            f"{base}/autoscout24", json={"bemerkungen": "a"}, headers={**headers, "If-Match": str(as24["version"])}
        ).status_code
        == 200
    )
    response = client.patch(
        f"{base}/autolina", json={"bemerkungen": "b"}, headers={**headers, "If-Match": str(autolina["version"])}
    )
    assert response.status_code == 200, response.text


# --- DELETE /v1/integrations/connections/{id} -----------------------------------------


def _connection(client, db_session, headers: dict[str, str]) -> dict:
    provider = _make_provider(db_session)
    response = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "auto-i-dat", "environment": "sandbox"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_connection_delete_from_a_stale_read_is_a_409_and_keeps_the_connection(client, db_session):
    headers = _token(manager=True)
    connection = _connection(client, db_session, headers)
    stale = str(connection["version"])
    disabled = client.post(
        f"/v1/integrations/connections/{connection['id']}/disable", headers={**headers, "If-Match": stale}
    )
    assert disabled.status_code == 200, disabled.text

    response = client.request(
        "DELETE",
        f"/v1/integrations/connections/{connection['id']}",
        json={"confirm": True},
        headers={**headers, "If-Match": stale},
    )
    assert response.status_code == 409, response.text
    db_session.expire_all()
    assert db_session.get(IntegrationConnection, uuid.UUID(connection["id"])) is not None

    current = str(disabled.json()["version"])
    response = client.request(
        "DELETE",
        f"/v1/integrations/connections/{connection['id']}",
        json={"confirm": True},
        headers={**headers, "If-Match": current},
    )
    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(IntegrationConnection, uuid.UUID(connection["id"])) is None


def test_connection_delete_without_if_match_is_refused(client, db_session):
    headers = _token(manager=True)
    connection = _connection(client, db_session, headers)
    response = client.request(
        "DELETE", f"/v1/integrations/connections/{connection['id']}", json={"confirm": True}, headers=headers
    )
    assert response.status_code == 400, response.text
    db_session.expire_all()
    assert db_session.get(IntegrationConnection, uuid.UUID(connection["id"])) is not None
