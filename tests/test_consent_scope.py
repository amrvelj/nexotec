"""FR-23 §1 (KAN-52) — per-channel consent carries a scope, the source is a
closed enum, and marketing selection reads granted AND scope=marketing."""

import types
import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.customer.models.customer import ConsentScope
from app.customer.public import channel_authorises_marketing


def _row(*, granted, scope):
    return types.SimpleNamespace(consent_granted=granted, consent_scope=scope)


class TestConsumerRule:
    def test_granted_marketing_authorises(self):
        assert channel_authorises_marketing(_row(granted=True, scope=ConsentScope.MARKETING)) is True

    def test_granted_null_scope_behaves_as_marketing(self):
        assert channel_authorises_marketing(_row(granted=True, scope=None)) is True

    @pytest.mark.parametrize("scope", [ConsentScope.INVOICING, ConsentScope.SERVICE])
    def test_a_non_marketing_grant_never_authorises_a_campaign(self, scope):
        assert channel_authorises_marketing(_row(granted=True, scope=scope)) is False

    def test_not_granted_never_authorises(self):
        assert channel_authorises_marketing(_row(granted=False, scope=ConsentScope.MARKETING)) is False


# --- API ---------------------------------------------------------------


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def _dealer_and_token(client):
    admin = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=False,
    )
    dealer = client.post(
        "/v1/dealerships",
        json={
            "legalName": "Consent AG", "dealerLicenseNumber": f"ZH-{uuid.uuid4().hex[:5]}", "licenseState": "ZH",
            "franchiseType": "independent",
            "address": {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
            "phone": "+41441234567", "taxId": "CHE-123.456.789",
        },
        headers=_bearer(admin),
    )
    assert dealer.status_code == 201, dealer.text
    did = dealer.json()["id"]
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.UUID(did),
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, did),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=True,
    )
    return did, token


def _customer(client, token):
    r = client.post(
        "/v1/customers",
        json={
            "firstName": "Ada", "lastName": "Byron", "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": f"{uuid.uuid4().hex[:8]}@example.ch"}],
        },
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_consent_captured_at_channel_creation_round_trips(client):
    _, token = _dealer_and_token(client)
    cid = _customer(client, token)
    r = client.post(
        f"/v1/customers/{cid}/phones",
        json={
            "phoneType": "work", "phoneE164": "+41441112233",
            "consentGranted": True, "consentScope": "invoicing", "consentSource": "form",
        },
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["consentScope"] == "invoicing"
    assert r.json()["consentSource"] == "form"
    assert r.json()["consentTimestamp"] is not None


def test_grant_without_a_scope_is_rejected(client):
    _, token = _dealer_and_token(client)
    cid = _customer(client, token)
    r = client.post(
        f"/v1/customers/{cid}/phones",
        json={"phoneType": "work", "phoneE164": "+41441112244", "consentGranted": True, "consentSource": "form"},
        headers=_bearer(token),
    )
    assert r.status_code == 422, r.text


def test_a_source_outside_the_enum_is_rejected(client):
    _, token = _dealer_and_token(client)
    cid = _customer(client, token)
    r = client.post(
        f"/v1/customers/{cid}/phones",
        json={
            "phoneType": "work", "phoneE164": "+41441112255",
            "consentGranted": True, "consentScope": "marketing", "consentSource": "advisor",
        },
        headers=_bearer(token),
    )
    assert r.status_code == 422, r.text


def test_a_consent_change_is_audit_logged_with_before_and_after(client):
    _, token = _dealer_and_token(client)
    cid = _customer(client, token)
    email = client.get(f"/v1/customers/{cid}/emails", headers=_bearer(token)).json()["items"][0]

    client.patch(
        f"/v1/customers/{cid}/emails/{email['id']}",
        json={"consentGranted": True, "consentScope": "marketing", "consentSource": "counter"},
        headers=_bearer(token),
    )

    events = client.get(f"/v1/customers/{cid}/audit-log", headers=_bearer(token)).json()["items"]
    update = next(e for e in events if e["action"] == "email_update")
    assert update["before"]["consentGranted"] is False
    assert update["after"]["consentGranted"] is True
    assert update["after"]["consentScope"] == "marketing"
    assert update["after"]["consentSource"] == "counter"
