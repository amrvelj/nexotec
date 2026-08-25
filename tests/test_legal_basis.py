"""WP-3 PR-4 (ADR-030): recording a legal_basis, the platform_admin
group-read flag flip and its precondition, and the group-read helper's own
compliance gate (proven at the service layer — no consuming endpoint exists
yet, see app.customer.services.legal_basis's own docstring for why).
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.errors import NotFoundError

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
    group_id: uuid.UUID | None = None,
) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=_tid,
        # This file is entirely about matching REAL dealer_group ids
        # against recorded legal_basis rows, so — unlike other test
        # files' tenant_id-derived shadow value — every call site here
        # must pass the dealership's real dealerGroupId explicitly.
        group_id=group_id or uuid.uuid5(uuid.NAMESPACE_OID, str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer_full(client, **overrides) -> dict:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "dealerLicenseNumber": "ZH-12345",
        "licenseState": "ZH",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    payload.update(overrides)
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_customer(client, dealer, **overrides) -> dict:
    token = _token(
        is_dealer_manager=True,
        tenant_id=uuid.UUID(dealer["id"]),
        group_id=uuid.UUID(dealer["dealerGroupId"]),
    )
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "private", "emailAddress": "anna@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- POST /v1/customers/{id}/legal-basis ------------------------------------------


def test_platform_admin_can_record_a_legal_basis(client):
    dealer = _create_dealer_full(client)
    customer = _create_customer(client, dealer)
    token = _token(
        AccessRole.PLATFORM_ADMIN,
        tenant_id=uuid.UUID(dealer["id"]),
        group_id=uuid.UUID(dealer["dealerGroupId"]),
    )

    response = client.post(
        f"/v1/customers/{customer['id']}/legal-basis",
        json={
            "basis": "joint_controller_agreement",
            "scope": "customer contact data, group-wide",
            "sourceDocument": "Joint Controller Agreement, signed 2026-08-25",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customerId"] == customer["id"]
    assert body["withdrawnAt"] is None


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY])
def test_non_platform_admin_cannot_record_a_legal_basis(client, role):
    dealer = _create_dealer_full(client)
    customer = _create_customer(client, dealer)
    token = _token(role, tenant_id=uuid.UUID(dealer["id"]))

    response = client.post(
        f"/v1/customers/{customer['id']}/legal-basis",
        json={"basis": "joint_controller_agreement", "scope": "x", "sourceDocument": "y"},
        headers=_bearer(token),
    )
    assert response.status_code == 403


# --- POST /v1/dealer-groups/{id}/enable-group-read --------------------------------


def test_enabling_group_read_with_no_recorded_basis_is_rejected(client):
    dealer = _create_dealer_full(client)
    token = _token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.UUID(dealer["id"]))

    response = client.post(f"/v1/dealer-groups/{dealer['dealerGroupId']}/enable-group-read", headers=_bearer(token))
    assert response.status_code == 400


def test_enabling_group_read_succeeds_once_a_basis_is_recorded(client):
    dealer = _create_dealer_full(client)
    customer = _create_customer(client, dealer)
    admin_token = _token(
        AccessRole.PLATFORM_ADMIN,
        tenant_id=uuid.UUID(dealer["id"]),
        group_id=uuid.UUID(dealer["dealerGroupId"]),
    )

    client.post(
        f"/v1/customers/{customer['id']}/legal-basis",
        json={"basis": "joint_controller_agreement", "scope": "x", "sourceDocument": "y"},
        headers=_bearer(admin_token),
    )

    response = client.post(
        f"/v1/dealer-groups/{dealer['dealerGroupId']}/enable-group-read", headers=_bearer(admin_token)
    )
    assert response.status_code == 200
    assert response.json()["groupReadEnabled"] is True


def test_non_platform_admin_cannot_enable_group_read(client):
    dealer = _create_dealer_full(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer["id"]))

    response = client.post(f"/v1/dealer-groups/{dealer['dealerGroupId']}/enable-group-read", headers=_bearer(token))
    assert response.status_code == 403


# --- the group-read helper's own gate (service-level — see module docstring) ------


def test_group_read_returns_nothing_with_no_recorded_basis(client, db_session):
    from app.customer.services.legal_basis import get_customer_group_read_or_404

    dealer = _create_dealer_full(client)
    customer = _create_customer(client, dealer)

    with pytest.raises(NotFoundError):
        get_customer_group_read_or_404(
            db_session,
            group_read_enabled=True,
            customer_id=uuid.UUID(customer["id"]),
            group_id=uuid.UUID(dealer["dealerGroupId"]),
        )


def test_group_read_returns_nothing_when_flag_is_off_even_with_a_live_basis(client, db_session):
    from app.customer.services.legal_basis import get_customer_group_read_or_404, record_legal_basis

    dealer = _create_dealer_full(client)
    customer = _create_customer(client, dealer)
    record_legal_basis(
        db_session,
        customer_id=uuid.UUID(customer["id"]),
        group_id=uuid.UUID(dealer["dealerGroupId"]),
        basis="joint_controller_agreement",
        scope="x",
        source_document="y",
        actor_id=uuid.uuid4(),
    )

    with pytest.raises(NotFoundError):
        get_customer_group_read_or_404(
            db_session,
            group_read_enabled=False,
            customer_id=uuid.UUID(customer["id"]),
            group_id=uuid.UUID(dealer["dealerGroupId"]),
        )


def test_group_read_succeeds_with_flag_on_and_a_live_basis(client, db_session):
    from app.customer.services.legal_basis import get_customer_group_read_or_404, record_legal_basis

    dealer = _create_dealer_full(client)
    customer = _create_customer(client, dealer)
    record_legal_basis(
        db_session,
        customer_id=uuid.UUID(customer["id"]),
        group_id=uuid.UUID(dealer["dealerGroupId"]),
        basis="joint_controller_agreement",
        scope="x",
        source_document="y",
        actor_id=uuid.uuid4(),
    )

    result = get_customer_group_read_or_404(
        db_session,
        group_read_enabled=True,
        customer_id=uuid.UUID(customer["id"]),
        group_id=uuid.UUID(dealer["dealerGroupId"]),
    )
    assert str(result.id) == customer["id"]


def test_group_read_returns_nothing_once_the_only_basis_is_withdrawn(client, db_session):
    """The read-time check, not just the flag-flip precondition, is what
    catches a withdrawal — nothing else re-checks the flag after it's on.
    """

    from app.customer.services.legal_basis import (
        get_customer_group_read_or_404,
        record_legal_basis,
        withdraw_legal_basis,
    )

    dealer = _create_dealer_full(client)
    customer = _create_customer(client, dealer)
    customer_id = uuid.UUID(customer["id"])
    group_id = uuid.UUID(dealer["dealerGroupId"])
    actor_id = uuid.uuid4()

    record_legal_basis(
        db_session,
        customer_id=customer_id,
        group_id=group_id,
        basis="joint_controller_agreement",
        scope="x",
        source_document="y",
        actor_id=actor_id,
    )
    withdraw_legal_basis(db_session, customer_id=customer_id, group_id=group_id, actor_id=actor_id)

    with pytest.raises(NotFoundError):
        get_customer_group_read_or_404(
            db_session, group_read_enabled=True, customer_id=customer_id, group_id=group_id
        )
