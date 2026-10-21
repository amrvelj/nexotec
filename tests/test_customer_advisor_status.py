"""KAN-410 — a customer's advisor must be a user of the Betrieb who may own
an open record: active or invited, the status rule Sales' responsible seller
uses (`platform.public.may_own_open_records`). Create and PATCH refuse a
suspended or deactivated advisor (422 naming `advisorId`); a PATCH that
leaves an already-deactivated advisor unchanged still saves.
"""

import uuid

import pytest

from app.platform.models.user import User, UserStatus
from tests.test_customer_fr17_fields import _bearer, _create_dealer, _create_user, _token


def _set_status(db_session, user: dict, status: UserStatus) -> None:
    # Set on the row: the paths that leave a deactivated or suspended user
    # behind (a deactivation, a suspension PATCH) are not what this tests.
    db_session.get(User, uuid.UUID(user["id"])).status = status
    db_session.commit()


def _setup(client) -> tuple[str, dict, str]:
    dealer_id = _create_dealer(client)
    acting = _create_user(client, dealer_id, firstName="Acting", lastName="User")
    return dealer_id, acting, _token(tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(acting["id"]))


def _create_body(advisor_id: str | None = None) -> dict:
    body = {
        "firstName": "Ada",
        "lastName": "Byron",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    if advisor_id is not None:
        body["advisorId"] = advisor_id
    return body


@pytest.mark.parametrize("status", [UserStatus.DEACTIVATED, UserStatus.SUSPENDED])
def test_create_refuses_an_advisor_who_is_not_active_or_invited(client, db_session, status):
    dealer_id, _acting, token = _setup(client)
    advisor = _create_user(client, dealer_id, firstName="Left", lastName="Already")
    _set_status(db_session, advisor, status)

    resp = client.post("/v1/customers", json=_create_body(advisor["id"]), headers=_bearer(token))

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["details"]["advisorId"] == advisor["id"]


@pytest.mark.parametrize("status", [UserStatus.DEACTIVATED, UserStatus.SUSPENDED])
def test_patch_refuses_an_advisor_who_is_not_active_or_invited(client, db_session, status):
    dealer_id, _acting, token = _setup(client)
    created = client.post("/v1/customers", json=_create_body(), headers=_bearer(token)).json()
    advisor = _create_user(client, dealer_id, firstName="Left", lastName="Already")
    _set_status(db_session, advisor, status)

    resp = client.patch(
        f"/v1/customers/{created['id']}",
        json={"advisorId": advisor["id"]},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["details"]["advisorId"] == advisor["id"]


@pytest.mark.parametrize("status", [UserStatus.ACTIVE, UserStatus.INVITED])
def test_an_active_or_invited_advisor_is_accepted_on_create_and_patch(client, db_session, status):
    dealer_id, _acting, token = _setup(client)
    first = _create_user(client, dealer_id, firstName="First", lastName="Advisor")
    second = _create_user(client, dealer_id, firstName="Second", lastName="Advisor")
    _set_status(db_session, first, status)
    _set_status(db_session, second, status)

    created = client.post("/v1/customers", json=_create_body(first["id"]), headers=_bearer(token))
    assert created.status_code == 201, created.text
    assert created.json()["advisorLabel"] == "First Advisor"

    patched = client.patch(
        f"/v1/customers/{created.json()['id']}",
        json={"advisorId": second["id"]},
        headers={**_bearer(token), "If-Match": str(created.json()["version"])},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["advisorId"] == second["id"]
    assert patched.json()["advisorLabel"] == "Second Advisor"


def test_an_unchanged_deactivated_advisor_does_not_block_another_fields_patch(client, db_session):
    dealer_id, _acting, token = _setup(client)
    advisor = _create_user(client, dealer_id, firstName="Mia", lastName="Frei")
    created = client.post("/v1/customers", json=_create_body(advisor["id"]), headers=_bearer(token)).json()
    _set_status(db_session, advisor, UserStatus.DEACTIVATED)

    # Once without advisorId, once naming the same advisor: neither changes it.
    first = client.patch(
        f"/v1/customers/{created['id']}",
        json={"nextFollowUp": "2026-12-01"},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        f"/v1/customers/{created['id']}",
        json={"advisorId": advisor["id"], "nextFollowUp": "2026-12-02"},
        headers={**_bearer(token), "If-Match": str(first.json()["version"])},
    )
    assert second.status_code == 200, second.text
    assert second.json()["advisorId"] == advisor["id"]
    assert second.json()["advisorLabel"] == "Mia Frei"
