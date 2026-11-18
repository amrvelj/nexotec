"""KAN-712 — the dealership PATCH no longer makes activation or offboarding
optional. `status` is platform staff's, and only to suspend a Betrieb or lift
the suspension (Platform Administration: "create, suspend"); a manager naming
it is 422 `status_not_patchable` and nothing changes. Activation (D-A-21,
KAN-214) and offboarding (FR-A-45, KAN-218) are their own procedures.
"""

import uuid

import pytest

from app.core.errors import UnprocessableEntityError
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.schemas.dealership import DealershipUpdate
from app.platform.organisation.services import dealership as dealership_service
from tests.identity_support import platform_admin_headers
from tests.membership_support import bearer, create_dealership, token


def _manager(dealership_id: str) -> dict[str, str]:
    return bearer(token(tenant_id=dealership_id, is_dealer_manager=True))


def _set_status(db_session, dealership_id: str, status: DealershipStatus) -> None:
    db_session.get(Dealership, uuid.UUID(dealership_id)).status = status
    db_session.commit()


def _read(client, dealership_id: str) -> dict:
    response = client.get(f"/v1/dealerships/{dealership_id}", headers=platform_admin_headers())
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("value", ["active", "offboarded", "suspended", "pending_onboarding"])
def test_a_manager_patch_naming_status_is_422_and_changes_nothing(client, value):
    dealership_id = create_dealership(client)
    before = _read(client, dealership_id)

    response = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"status": value, "tradeName": "Neu"},
        headers={**_manager(dealership_id), "If-Match": str(before["version"])},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "status_not_patchable"
    after = _read(client, dealership_id)
    assert after["status"] == before["status"] == "pending_onboarding"
    assert after["tradeName"] == before["tradeName"]
    assert after["version"] == before["version"]


def test_a_manager_patch_without_status_still_saves(client):
    dealership_id = create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"tradeName": "Neu"},
        headers={**_manager(dealership_id), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tradeName"] == "Neu"


def test_staff_cannot_activate_through_the_patch(client):
    dealership_id = create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"status": "active"},
        headers={**platform_admin_headers(), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "use_activation_procedure"
    assert _read(client, dealership_id)["status"] == "pending_onboarding"


def test_staff_cannot_offboard_through_the_patch(client, db_session):
    dealership_id = create_dealership(client)
    _set_status(db_session, dealership_id, DealershipStatus.ACTIVE)
    response = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"status": "offboarded"},
        headers={**platform_admin_headers(), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "use_offboarding_procedure"
    assert _read(client, dealership_id)["status"] == "active"


def test_staff_suspend_and_lift_the_suspension(client, db_session):
    dealership_id = create_dealership(client)
    _set_status(db_session, dealership_id, DealershipStatus.ACTIVE)
    headers = platform_admin_headers()

    suspended = client.patch(
        f"/v1/dealerships/{dealership_id}", json={"status": "suspended"}, headers={**headers, "If-Match": "1"}
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"

    lifted = client.patch(
        f"/v1/dealerships/{dealership_id}", json={"status": "active"}, headers={**headers, "If-Match": "2"}
    )
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()["status"] == "active"


def test_staff_cannot_suspend_a_betrieb_still_in_onboarding(client):
    dealership_id = create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"status": "suspended"},
        headers={**platform_admin_headers(), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "status_transition_not_allowed"


def test_staff_patch_with_a_null_status_is_422_and_changes_nothing(client, db_session):
    """`status` is NOT NULL: the body's validator refuses a null (KAN-312)."""

    dealership_id = create_dealership(client)
    _set_status(db_session, dealership_id, DealershipStatus.ACTIVE)
    response = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"status": None, "tradeName": "Neu"},
        headers={**platform_admin_headers(), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text
    assert "status cannot be null" in response.text
    after = _read(client, dealership_id)
    assert after["status"] == "active" and after["tradeName"] != "Neu" and after["version"] == 1


def test_the_service_refuses_a_null_status_even_past_the_body_validator(client, db_session):
    """A caller that builds the update without the validator (a script, a
    later route) gets 422 `status_required` — never a NULL written into the
    NOT NULL column, which surfaced as the 409 UID conflict of the flush."""

    dealership_id = create_dealership(client)
    _set_status(db_session, dealership_id, DealershipStatus.ACTIVE)
    dealership = db_session.get(Dealership, uuid.UUID(dealership_id))
    data = DealershipUpdate.model_construct(_fields_set={"status"}, status=None)

    with pytest.raises(UnprocessableEntityError) as refused:
        dealership_service.update_dealership(
            db_session, dealership=dealership, data=data, actor_id=uuid.uuid4(), actor_is_staff=True
        )

    assert refused.value.details["reason"] == "status_required"
    db_session.rollback()
    assert _read(client, dealership_id)["status"] == "active"
