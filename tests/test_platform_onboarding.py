"""KAN-210 — onboarding a Betrieb (Dealer Administration D-A-06, FR-A-24/25):
one platform_admin request writes the group, the legal entity (UID unique),
the entitlements, the first manager with an invitation and the seeds, in one
transaction, with one `platform.dealership.onboarded` outbox row. Nexotec
sets nothing commercial.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.outbox_model import OutboxMessage
from app.platform.models.auth_token import AuthToken
from app.platform.models.dealership import DealerGroup, Dealership, DealershipStatus, Location
from app.platform.models.dealership_bank_account import DealershipBankAccount
from app.platform.models.dealership_settings import DealershipSettings
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.number_range import NumberRange
from app.platform.models.user import User, UserStatus
from tests.dealership_support import seed_business_reference_lists, valid_uid
from tests.identity_support import PasswordLogin, cookie_headers, create_dealership, create_user, platform_admin_headers


@pytest.fixture(autouse=True)
def _business_lists(db_session):
    seed_business_reference_lists(db_session)


def _request(**overrides) -> dict:
    body = {
        "group": {"newGroupName": "Garage Muster Gruppe"},
        "legalEntity": {
            "legalName": "Garage Muster AG",
            "tradeName": "Garage Muster",
            "franchiseType": "independent",
            "legalForm": "ag",
            "uid": valid_uid(),
            "address": {
                "street": "Bahnhofstrasse",
                "houseNumber": "1",
                "postalCode": "8001",
                "locality": "Zürich",
                "canton": "ZH",
            },
            "phone": "+41441234567",
        },
        "entitlements": ["sales"],
        "manager": {
            "firstName": "Martina",
            "lastName": "Muster",
            "email": f"martina-{uuid.uuid4().hex[:8]}@example.ch",
            "role": "gm",
            "accessRoles": ["sales"],
        },
    }
    for key, value in overrides.items():
        body[key] = value
    return body


def _counts(db_session) -> tuple[int, int, int]:
    db_session.expire_all()
    return (
        db_session.scalar(select(func.count()).select_from(DealerGroup)),
        db_session.scalar(select(func.count()).select_from(Dealership)),
        db_session.scalar(select(func.count()).select_from(User)),
    )


def test_onboarding_writes_the_betrieb_its_manager_and_seeds_in_one_go(client, mailbox, db_session):
    body = _request()
    response = client.post("/v1/platform/onboarding", json=body, headers=platform_admin_headers())
    assert response.status_code == 201, response.text
    result = response.json()
    dealership_id = uuid.UUID(result["dealershipId"])

    db_session.expire_all()
    dealership = db_session.get(Dealership, dealership_id)
    assert dealership.status == DealershipStatus.PENDING_ONBOARDING
    assert dealership.legal_form == "ag"
    assert db_session.get(DealerGroup, dealership.dealer_group_id).name == "Garage Muster Gruppe"
    assert result["status"] == "pending_onboarding"
    assert result["entitlements"] == ["sales"]
    # The first location is the seat.
    location = db_session.scalars(select(Location).where(Location.tenant_id == dealership_id)).one()
    assert str(location.id) == result["locationId"]
    assert location.address_locality == "Zürich"
    # Number ranges seeded, entitlements written.
    assert db_session.scalar(select(func.count()).select_from(NumberRange).where(NumberRange.tenant_id == dealership_id)) > 0
    modules = db_session.scalars(
        select(ModuleEntitlement.module).where(ModuleEntitlement.dealership_id == dealership_id)
    ).all()
    assert [m.value for m in modules] == ["sales"]
    # The manager: invited, manager flag, the seat as primary location, one invitation.
    manager = db_session.get(User, uuid.UUID(result["managerUserId"]))
    assert manager.is_dealer_manager is True
    assert manager.status == UserStatus.INVITED
    assert manager.tenant_id == dealership_id
    assert result["managerStatus"] == "invited"
    assert db_session.scalar(select(func.count()).select_from(AuthToken).where(AuthToken.user_id == manager.id)) == 1
    assert [mail.to for mail in mailbox] == [body["manager"]["email"].lower()]
    # One outbox fact.
    events = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "platform.dealership.onboarded", OutboxMessage.aggregate_id == dealership_id
        )
    ).all()
    assert len(events) == 1
    assert events[0].payload["managerUserId"] == result["managerUserId"]
    assert events[0].payload["entitlements"] == ["sales"]
    # MFA enforced by default.
    config = client.get(f"/v1/dealerships/{dealership_id}/auth-config", headers=platform_admin_headers())
    assert config.json()["mfaEnforced"] is True


def test_nexotec_sets_nothing_commercial(client, mailbox, db_session):
    response = client.post("/v1/platform/onboarding", json=_request(), headers=platform_admin_headers())
    assert response.status_code == 201, response.text
    dealership_id = uuid.UUID(response.json()["dealershipId"])
    db_session.expire_all()
    assert db_session.get(Dealership, dealership_id).vat_rate is None
    assert db_session.get(Dealership, dealership_id).vat_number is None
    assert (
        db_session.scalar(
            select(func.count()).select_from(DealershipBankAccount).where(DealershipBankAccount.dealership_id == dealership_id)
        )
        == 0
    )
    settings = db_session.scalars(select(DealershipSettings).where(DealershipSettings.dealership_id == dealership_id)).one()
    assert settings.default_payment_terms_days == DealershipSettings.__table__.c.default_payment_terms_days.default.arg


def test_a_betrieb_without_sales_is_a_valid_onboarding(client, mailbox, db_session):
    response = client.post(
        "/v1/platform/onboarding", json=_request(entitlements=["aftersales"]), headers=platform_admin_headers()
    )
    assert response.status_code == 201, response.text
    assert response.json()["entitlements"] == ["aftersales"]


def test_a_betrieb_joins_an_existing_group(client, mailbox, db_session):
    sister = create_dealership(client)
    group_id = str(db_session.get(Dealership, uuid.UUID(sister)).dealer_group_id)
    groups = client.get("/v1/platform/dealer-groups", headers=platform_admin_headers())
    assert groups.status_code == 200
    assert group_id in [g["id"] for g in groups.json()["items"]]

    response = client.post(
        "/v1/platform/onboarding", json=_request(group={"dealerGroupId": group_id}), headers=platform_admin_headers()
    )
    assert response.status_code == 201, response.text
    assert response.json()["dealerGroupId"] == group_id


def test_a_uid_already_onboarded_is_409_and_writes_nothing(client, mailbox, db_session):
    first = _request()
    assert client.post("/v1/platform/onboarding", json=first, headers=platform_admin_headers()).status_code == 201
    before = _counts(db_session)
    mails = len(mailbox)

    again = _request(legalEntity={**first["legalEntity"], "legalName": "Andere AG"})
    response = client.post("/v1/platform/onboarding", json=again, headers=platform_admin_headers())

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"reason": "uid_already_onboarded"}
    assert "Garage Muster" not in response.text
    assert _counts(db_session) == before
    assert len(mailbox) == mails


def test_a_failing_seed_leaves_nothing_behind(client, mailbox, db_session, monkeypatch):
    import app.platform.services.number_range as number_range

    def broken(*args, **kwargs):
        raise RuntimeError("seed failed")

    monkeypatch.setattr(number_range, "seed_number_ranges", broken)
    before = _counts(db_session)

    with pytest.raises(RuntimeError, match="seed failed"):
        client.post("/v1/platform/onboarding", json=_request(), headers=platform_admin_headers())

    assert _counts(db_session) == before
    assert mailbox == []


def test_a_manager_address_already_taken_rolls_back_the_betrieb(client, mailbox, db_session):
    existing = create_dealership(client)
    taken = create_user(client, existing)
    before = _counts(db_session)
    mails = len(mailbox)

    body = _request()
    body["manager"]["email"] = taken["email"]
    response = client.post("/v1/platform/onboarding", json=body, headers=platform_admin_headers())

    assert response.status_code == 409
    assert _counts(db_session) == before
    assert len(mailbox) == mails


@pytest.mark.parametrize(
    "group",
    [{}, {"dealerGroupId": str(uuid.uuid4()), "newGroupName": "Beides"}],
)
def test_the_group_step_names_exactly_one_group(client, mailbox, group):
    response = client.post("/v1/platform/onboarding", json=_request(group=group), headers=platform_admin_headers())
    assert response.status_code == 422


def test_an_unknown_existing_group_is_404(client, mailbox, db_session):
    before = _counts(db_session)
    response = client.post(
        "/v1/platform/onboarding",
        json=_request(group={"dealerGroupId": str(uuid.uuid4())}),
        headers=platform_admin_headers(),
    )
    assert response.status_code == 404
    assert _counts(db_session) == before


def test_only_platform_staff_with_a_factor_onboard(client, mailbox, db_session):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    manager_headers = cookie_headers(PasswordLogin(client, mailbox).session_cookie(manager))

    assert client.post("/v1/platform/onboarding", json=_request(), headers=manager_headers).status_code == 403
    assert client.get("/v1/platform/dealer-groups", headers=manager_headers).status_code == 403
    staff_without_factor = platform_admin_headers(mfa=False)
    assert client.post("/v1/platform/onboarding", json=_request(), headers=staff_without_factor).status_code == 403


def test_an_unknown_legal_form_is_422_and_writes_nothing(client, mailbox, db_session):
    body = _request()
    body["legalEntity"]["legalForm"] = "kolchose"
    before = _counts(db_session)
    response = client.post("/v1/platform/onboarding", json=body, headers=platform_admin_headers())
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "unknown_reference_code"
    assert _counts(db_session) == before


def test_the_old_create_route_is_deprecated_in_openapi(client):
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/v1/dealerships"]["post"]["deprecated"] is True
