"""KAN-198 (D-A-04, D-A-10): module entitlements — the table, the endpoints,
the JWT claim and the gate that runs before every role check."""

import uuid

import pytest
from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token
from app.core.permissions import ModuleCode
from app.platform.models.dealership import Dealership
from app.platform.models.module_entitlement import ModuleEntitlement
from tests.dealership_support import valid_uid

VALID_ADDRESS = {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"}
ALL = ["aftersales", "finance", "parts", "reporting", "sales"]


def _token(role=None, *, tenant_id, is_dealer_manager=False, entitlements=None, entitlements_version=0):
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({role}) if role else frozenset(),
        is_dealer_manager=is_dealer_manager,
        entitlements=entitlements,
        entitlements_version=entitlements_version,
    )


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _platform_admin():
    tid = uuid.uuid4()
    return _bearer(_token(AccessRole.PLATFORM_ADMIN, tenant_id=tid))


def _create_dealership(client) -> dict:
    response = client.post(
        "/v1/dealerships",
        json={
            "legalName": "Garage Musterbetrieb AG",
            "franchiseType": "independent",
            "address": VALID_ADDRESS,
            "phone": "+41441234567",
            "uid": valid_uid(),
        },
        headers=_platform_admin(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set(client, dealership_id, modules, *, if_match):
    return client.put(
        f"/v1/dealerships/{dealership_id}/entitlements",
        json={"modules": modules},
        headers={**_platform_admin(), "If-Match": str(if_match)},
    )


# --- the endpoints ---------------------------------------------------------------


def test_a_new_dealership_starts_with_no_entitlements_and_version_zero(client):
    body = _create_dealership(client)
    response = client.get(f"/v1/dealerships/{body['id']}/entitlements", headers=_platform_admin())
    assert response.status_code == 200, response.text
    assert response.json() == {"modules": [], "entitlementsVersion": 0, "dealershipVersion": 1}


def test_platform_admin_sets_the_full_set_and_bumps_the_version(client, db_session):
    body = _create_dealership(client)
    response = _set(client, body["id"], ["sales", "aftersales"], if_match=1)
    assert response.status_code == 200, response.text
    assert response.json() == {"modules": ["aftersales", "sales"], "entitlementsVersion": 1, "dealershipVersion": 2}

    # Withdrawing keeps the row with valid_to; re-granting reopens it.
    response = _set(client, body["id"], ["sales"], if_match=2)
    assert response.json()["modules"] == ["sales"]
    assert response.json()["entitlementsVersion"] == 2
    rows = db_session.scalars(
        select(ModuleEntitlement).where(ModuleEntitlement.dealership_id == uuid.UUID(body["id"]))
    ).all()
    assert {r.module.value: (r.valid_to is None) for r in rows} == {"sales": True, "aftersales": False}

    response = _set(client, body["id"], ["sales", "aftersales"], if_match=3)
    assert response.json()["modules"] == ["aftersales", "sales"]
    db_session.expire_all()  # the rows above are cached in this session; the API wrote through another
    rows = db_session.scalars(
        select(ModuleEntitlement).where(ModuleEntitlement.dealership_id == uuid.UUID(body["id"]))
    ).all()
    assert len(rows) == 2 and all(r.valid_to is None for r in rows)


def test_an_unchanged_set_does_not_bump_the_version(client):
    body = _create_dealership(client)
    _set(client, body["id"], ["sales"], if_match=1)
    response = _set(client, body["id"], ["sales"], if_match=2)
    assert response.status_code == 200
    assert response.json()["entitlementsVersion"] == 1


def test_setting_entitlements_is_audit_logged_with_before_and_after(client):
    body = _create_dealership(client)
    _set(client, body["id"], ["sales", "finance"], if_match=1)
    log = client.get(f"/v1/dealerships/{body['id']}/audit-log", headers=_platform_admin()).json()["items"]
    event = next(e for e in log if e["action"] == "entitlements_set")
    assert event["before"] == {"modules": []}
    assert event["after"] == {"modules": ["finance", "sales"]}


def test_the_manager_reads_but_cannot_write_entitlements(client):
    body = _create_dealership(client)
    tid = uuid.UUID(body["id"])
    manager = _bearer(_token(tenant_id=tid, is_dealer_manager=True))
    assert client.get(f"/v1/dealerships/{body['id']}/entitlements", headers=manager).status_code == 200
    response = client.put(
        f"/v1/dealerships/{body['id']}/entitlements",
        json={"modules": ["sales"]},
        headers={**manager, "If-Match": "1"},
    )
    assert response.status_code == 403


def test_a_plain_role_cannot_read_entitlements_and_another_tenant_gets_404(client):
    body = _create_dealership(client)
    tid = uuid.UUID(body["id"])
    assert (
        client.get(f"/v1/dealerships/{body['id']}/entitlements", headers=_bearer(_token(AccessRole.SALES, tenant_id=tid))).status_code
        == 403
    )
    other = _bearer(_token(tenant_id=uuid.uuid4(), is_dealer_manager=True))
    assert client.get(f"/v1/dealerships/{body['id']}/entitlements", headers=other).status_code == 404


def test_an_unknown_module_is_a_422(client):
    body = _create_dealership(client)
    response = _set(client, body["id"], ["marketing"], if_match=1)
    assert response.status_code == 422


# --- the gate ---------------------------------------------------------------------


def _sales_routes():
    return ["/v1/sales/offers", "/v1/inventory/stock-items", "/v1/valuations", "/v1/sales/deals"]


@pytest.mark.parametrize("path", _sales_routes())
def test_an_unlicensed_module_is_404_for_everyone_including_the_manager_and_platform_staff(client, path):
    body = _create_dealership(client)
    tid = uuid.UUID(body["id"])
    without_sales = frozenset({"aftersales"})
    for headers in (
        _bearer(_token(AccessRole.SALES, tenant_id=tid, entitlements=without_sales)),
        _bearer(_token(tenant_id=tid, is_dealer_manager=True, entitlements=without_sales)),
        _bearer(_token(AccessRole.PLATFORM_ADMIN, tenant_id=tid, entitlements=without_sales)),
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] == "not_found"


def test_the_entitlement_check_runs_before_the_role_check(client):
    """A user with no role at all: 404 when the module is unlicensed (never a
    403 that would confirm the module exists), 403 once it is licensed."""

    body = _create_dealership(client)
    tid = uuid.UUID(body["id"])
    no_role_unlicensed = _bearer(_token(tenant_id=tid, entitlements=frozenset()))
    assert client.post("/v1/sales/offers", json={}, headers=no_role_unlicensed).status_code == 404
    no_role_licensed = _bearer(_token(tenant_id=tid, entitlements=frozenset({"sales"})))
    assert client.post("/v1/sales/offers", json={}, headers=no_role_licensed).status_code == 403


def test_a_licensed_module_lets_the_request_through_to_the_role_check(client):
    body = _create_dealership(client)
    tid = uuid.UUID(body["id"])
    response = client.get(
        "/v1/sales/offers", headers=_bearer(_token(AccessRole.SALES, tenant_id=tid, entitlements=frozenset({"sales"})))
    )
    assert response.status_code == 200, response.text


def test_a_token_minted_before_the_entitlements_changed_is_refused_so_the_user_signs_in_again(client):
    body = _create_dealership(client)
    tid = uuid.UUID(body["id"])
    _set(client, body["id"], ["sales"], if_match=1)  # version 1
    old_token = _bearer(_token(AccessRole.SALES, tenant_id=tid, entitlements=frozenset({"sales"}), entitlements_version=1))
    assert client.get("/v1/sales/offers", headers=old_token).status_code == 200

    _set(client, body["id"], ["sales", "aftersales"], if_match=2)  # version 2 — the old token is stale
    response = client.get("/v1/sales/offers", headers=old_token)
    assert response.status_code == 401, response.text
    assert "sign in again" in response.json()["error"]["message"]

    fresh = _bearer(_token(AccessRole.SALES, tenant_id=tid, entitlements=frozenset({"sales"}), entitlements_version=2))
    assert client.get("/v1/sales/offers", headers=fresh).status_code == 200


def test_routes_outside_the_sales_entitlement_are_not_gated(client):
    body = _create_dealership(client)
    tid = uuid.UUID(body["id"])
    token = _bearer(_token(AccessRole.AFTERSALES, tenant_id=tid, entitlements=frozenset({"aftersales"})))
    assert client.get("/v1/customers", headers=token).status_code == 200
    assert client.get("/v1/vehicles", headers=token).status_code == 200


# --- the session carries the set -------------------------------------------------------


def test_the_login_response_and_token_carry_the_dealerships_entitlements(client, oidc_fake, db_session):
    from tests.fake_oidc import ZitadelIdentity

    body = _create_dealership(client)
    _set(client, body["id"], ["sales", "parts"], if_match=1)
    user = client.post(
        f"/v1/dealerships/{body['id']}/users",
        json={
            "firstName": "Max",
            "lastName": "Muster",
            "email": f"max-{uuid.uuid4().hex[:6]}@example.ch",
            "role": "sales",
            "accessRoles": ["sales"],
            "isDealerManager": True,
            "authIdentityId": f"stub-sub-{uuid.uuid4()}",
        },
        headers=_platform_admin(),
    )
    assert user.status_code == 201, user.text
    oidc_fake.enqueue_identity(ZitadelIdentity(sub=user.json()["authIdentityId"], email=user.json()["email"], name=None))
    callback = client.get("/v1/auth/oidc/callback", follow_redirects=False)
    cookie = callback.cookies.get("dms_session")
    assert cookie
    client.cookies.set("dms_session", cookie)
    me = client.get("/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["entitlements"] == ["parts", "sales"]
    # and the gate honours the minted claim + version
    assert client.get("/v1/sales/offers").status_code == 200
    assert client.get("/v1/inventory/stock-items").status_code == 200

    dealership = db_session.get(Dealership, uuid.UUID(body["id"]))
    assert dealership.entitlements_version == 1
    assert {m.value for m in ModuleCode} == set(ALL)
