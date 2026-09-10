"""D-21 (KAN-54) — Customer.preferred_channel vocabulary is
email / phone / post / whatsapp. `message` is a retained legacy member
(pre-D-21 rows still load) with no clean target."""

import uuid

import pytest

from app.customer.models.customer import PreferredChannel
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate, CustomerUpdate


def _base(**over):
    data = {
        "customer_type": "individual",
        "language": "de",
        "first_name": "Ada",
        "last_name": "Byron",
        "emails": [CustomerEmailCreate(email_type="personal", email_address=f"{uuid.uuid4().hex[:8]}@example.ch")],
    }
    data.update(over)
    return CustomerCreate(**data)


def test_enum_is_the_d21_vocabulary_plus_the_legacy_member():
    assert {c.value for c in PreferredChannel} == {"email", "phone", "post", "whatsapp", "message"}


@pytest.mark.parametrize("value", ["email", "phone", "post", "whatsapp"])
def test_the_four_current_values_are_accepted_on_create_and_update(value):
    assert _base(preferred_channel=value).preferred_channel == PreferredChannel(value)
    assert CustomerUpdate(preferred_channel=value).preferred_channel == PreferredChannel(value)


@pytest.mark.parametrize("old", ["mail", "call", "letter"])
def test_the_pre_d21_values_are_rejected(old):
    with pytest.raises(ValueError):
        _base(preferred_channel=old)


def test_preferred_channel_round_trips_through_the_api(client):
    from app.core.auth import AccessRole, create_access_token

    tenant = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant)),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )
    # a real dealership is needed for the create endpoint's session check
    admin = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=False,
    )
    dealer = client.post(
        "/v1/dealerships",
        json={
            "legalName": "D21 AG", "dealerLicenseNumber": f"ZH-{uuid.uuid4().hex[:5]}", "licenseState": "ZH",
            "franchiseType": "independent",
            "address": {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
            "phone": "+41441234567", "taxId": "CHE-123.456.789",
        },
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert dealer.status_code == 201, dealer.text
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(dealer.json()["id"]),
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, dealer.json()["id"]),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )
    created = client.post(
        "/v1/customers",
        json={
            "firstName": "Ada", "lastName": "Byron", "language": "de", "preferredChannel": "whatsapp",
            "emails": [{"emailType": "personal", "emailAddress": "ada-d21@example.ch"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["preferredChannel"] == "whatsapp"

    patched = client.patch(
        f"/v1/customers/{created.json()['id']}",
        json={"preferredChannel": "post"},
        headers={"Authorization": f"Bearer {token}", "If-Match": str(created.json()["version"])},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["preferredChannel"] == "post"
