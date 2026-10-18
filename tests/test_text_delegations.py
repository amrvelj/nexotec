"""KAN-199 (FR-A-28, D-A-23): standard-text delegations — granted per
category to a named user, audited with the granter, ended with deactivation,
and honoured by the core gate `require_write_or_delegated` through the
checker platform registers (proven on a probe route: no standard-text
endpoint exists yet)."""

import uuid

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.auth import AccessRole, Principal
from app.core.errors import register_error_handlers
from app.core.permissions import require_write_or_delegated
from app.db import get_db
from tests.membership_support import (
    audit_actions,
    bearer,
    create_dealership,
    create_user,
    manager_of,
    platform_admin,
    token,
)


def _put(client, dealership, user, categories, *, headers=None, if_match):
    return client.put(
        f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations",
        json={"categories": categories},
        headers={**(headers or platform_admin()), "If-Match": str(if_match)},
    )


def test_a_manager_grants_categories_the_set_is_replaced_and_every_change_is_audited(client):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    user = create_user(client, dealership, is_dealer_manager=False)

    response = _put(
        client, dealership, user, ["email", "workshop"], headers=manager_of(dealership, manager), if_match=1
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"categories": ["email", "workshop"], "userVersion": 2}

    response = _put(client, dealership, user, ["workshop"], if_match=2)
    assert response.json()["categories"] == ["workshop"]
    # A no-op replacement changes nothing, the version included.
    assert _put(client, dealership, user, ["workshop"], if_match=3).json()["userVersion"] == 3
    listed = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations", headers=platform_admin())
    assert listed.json() == {"categories": ["workshop"], "userVersion": 3}

    events = [item for item in audit_actions(client, dealership, user["id"]) if item["action"] == "text_delegation_set"]
    assert [(e["before"]["categories"], e["after"]["categories"]) for e in events] == [
        ([], ["email", "workshop"]),
        (["email", "workshop"], ["workshop"]),
    ]
    assert events[0]["actorId"] == manager["id"]

    ended = client.delete(
        f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations",
        headers={**platform_admin(), "If-Match": "3"},
    )
    assert ended.status_code == 204
    assert (
        client.get(
            f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations", headers=platform_admin()
        ).json()["categories"]
        == []
    )


def test_an_unknown_category_is_422_and_a_plain_role_or_stale_version_is_refused(client):
    dealership = create_dealership(client)
    user = create_user(client, dealership, is_dealer_manager=False)
    assert _put(client, dealership, user, ["offer_texts"], if_match=1).status_code == 422
    plain = bearer(token(AccessRole.SALES, tenant_id=dealership))
    assert _put(client, dealership, user, ["email"], headers=plain, if_match=1).status_code == 403
    assert (
        client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations", headers=plain).status_code
        == 403
    )
    assert _put(client, dealership, user, ["email"], if_match=7).status_code == 409


def test_a_dealer_manager_cannot_write_delegations_on_a_staff_account(client):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    staff = create_user(client, dealership, accessRoles=["platform_admin"], isDealerManager=False)
    assert (
        _put(client, dealership, staff, ["email"], headers=manager_of(dealership, manager), if_match=1).status_code
        == 403
    )
    assert (
        client.delete(
            f"/v1/dealerships/{dealership}/users/{staff['id']}/text-delegations",
            headers={**manager_of(dealership, manager), "If-Match": "1"},
        ).status_code
        == 403
    )


def test_a_suspension_keeps_the_delegations_and_a_deactivated_user_gets_none(client):
    dealership = create_dealership(client)
    create_user(client, dealership)
    user = create_user(client, dealership, is_dealer_manager=False)
    assert _put(client, dealership, user, ["email"], if_match=1).status_code == 200
    assert (
        client.patch(
            f"/v1/dealerships/{dealership}/users/{user['id']}",
            json={"status": "suspended"},
            headers={**platform_admin(), "If-Match": "2"},
        ).status_code
        == 200
    )
    listed = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations", headers=platform_admin())
    assert listed.json()["categories"] == ["email"]
    assert (
        client.patch(
            f"/v1/dealerships/{dealership}/users/{user['id']}",
            json={"status": "deactivated"},
            headers={**platform_admin(), "If-Match": "3"},
        ).status_code
        == 200
    )
    assert _put(client, dealership, user, ["email"], if_match=4).status_code == 409


def test_deactivation_ends_every_delegation(client):
    dealership = create_dealership(client)
    create_user(client, dealership)
    user = create_user(client, dealership, is_dealer_manager=False)
    assert _put(client, dealership, user, ["email", "sms"], if_match=1).status_code == 200
    response = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"status": "deactivated"},
        headers={**platform_admin(), "If-Match": "2"},
    )
    assert response.status_code == 200, response.text
    listed = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations", headers=platform_admin())
    assert listed.json()["categories"] == []
    events = [item for item in audit_actions(client, dealership, user["id"]) if item["action"] == "text_delegation_set"]
    assert events[-1]["after"]["categories"] == [] and events[-1]["reason"] == "deactivated"


# --- the gate ----------------------------------------------------------------------


@pytest.fixture
def probe_client(engine):
    """A route per standard-text category behind the delegation-aware gate —
    what a standard-text endpoint will look like once it exists."""

    probe = FastAPI()
    register_error_handlers(probe)
    router = APIRouter()

    @router.put("/probe/standard-texts/{category}")
    def write_text(category: str, principal: Principal = Depends(require_write_or_delegated("standard_texts"))):
        return {"category": category, "user": str(principal.user_id)}

    @router.put("/probe/standard-texts/by-id/{text_id}")
    def write_text_by_id(text_id: str, principal: Principal = Depends(require_write_or_delegated("standard_texts"))):
        return {"textId": text_id, "user": str(principal.user_id)}

    probe.include_router(router)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    probe.dependency_overrides[get_db] = _override_get_db
    return TestClient(probe)


def test_a_delegation_grants_write_on_its_category_only(client, probe_client):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    user = create_user(client, dealership, is_dealer_manager=False)
    assert _put(client, dealership, user, ["email"], if_match=1).status_code == 200

    delegate = bearer(token(AccessRole.SALES, tenant_id=dealership, user_id=user["id"]))
    assert probe_client.put("/probe/standard-texts/email", headers=delegate).status_code == 200
    assert probe_client.put("/probe/standard-texts/document", headers=delegate).status_code == 403
    assert probe_client.put("/probe/standard-texts/not-a-category", headers=delegate).status_code == 403

    # The manager writes every category; a plain user none; platform staff all.
    assert (
        probe_client.put("/probe/standard-texts/document", headers=manager_of(dealership, manager)).status_code == 200
    )
    nobody = bearer(token(AccessRole.SALES, tenant_id=dealership))
    assert probe_client.put("/probe/standard-texts/email", headers=nobody).status_code == 403
    assert probe_client.put("/probe/standard-texts/sms", headers=platform_admin()).status_code == 200

    # A delegation never travels into a sister dealership the user switched to.
    elsewhere = bearer(token(AccessRole.SALES, tenant_id=uuid.uuid4(), user_id=user["id"]))
    assert probe_client.put("/probe/standard-texts/email", headers=elsewhere).status_code == 403

    # The category comes from the path only: a query parameter, or a route
    # without the path parameter, opens nothing (the gate is then require_write).
    assert probe_client.put("/probe/standard-texts/document?category=email", headers=delegate).status_code == 403
    assert probe_client.put("/probe/standard-texts/by-id/abc?category=email", headers=delegate).status_code == 403
    assert (
        probe_client.put("/probe/standard-texts/by-id/abc", headers=manager_of(dealership, manager)).status_code == 200
    )

    # Withdrawn: the write stops.
    assert _put(client, dealership, user, [], if_match=2).status_code == 200
    assert probe_client.put("/probe/standard-texts/email", headers=delegate).status_code == 403
