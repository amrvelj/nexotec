"""KAN-50 — the FR-17 / FR-18 stored fields (Phase B2).

Covers the fourteen stored fields, their validation, the advisor
three-column pattern and D-24 default, tag replace-semantics, provenance
`dealershipId`, and a guard that the six DERIVED fields (Phase C) are not
writable here.
"""

import uuid
from decimal import Decimal

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.errors import UnprocessableEntityError
from app.core.validators import iban_is_valid
from app.customer.models.customer import Customer, CustomerTag, Gender
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate, CustomerUpdate
from app.customer.services.customer import create_customer, update_customer

# A real, mod-97-valid Swiss IBAN (the canonical example from the spec).
VALID_IBAN = "CH93 0076 2011 6238 5295 7"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(*, tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )


def _create_dealer(client) -> str:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        is_dealer_manager=False,
    )
    resp = client.post(
        "/v1/dealerships",
        json={
            "legalName": "Garage Musterbetrieb AG",
            "dealerLicenseNumber": f"ZH-{uuid.uuid4().hex[:5]}",
            "licenseState": "ZH",
            "franchiseType": "independent",
            "address": {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
            "phone": "+41441234567",
            "taxId": "CHE-123.456.789",
        },
        headers=_bearer(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        is_dealer_manager=False,
    )
    payload = {
        "firstName": "Sam",
        "lastName": "Sales",
        "email": f"sam-{uuid.uuid4().hex[:8]}@example.ch",
        "role": "sales",
        "accessRoles": ["sales"],
        "isDealerManager": False,
        "authIdentityId": f"stub-sub-{uuid.uuid4()}",
    }
    payload.update(overrides)
    resp = client.post(f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make(db_session, group_id, **create_kwargs) -> Customer:
    data_kwargs = {
        "customer_type": "individual",
        "language": "de",
        "first_name": "Ada",
        "last_name": "Byron",
        "emails": [CustomerEmailCreate(email_type="personal", email_address=f"{uuid.uuid4().hex[:8]}@example.ch")],
    }
    data_kwargs.update(create_kwargs)
    return create_customer(
        db_session,
        group_id=group_id,
        data=CustomerCreate(**data_kwargs),
        actor_id=uuid.uuid4(),
        dealership_id=uuid.uuid4(),
    )


# --- IBAN ----------------------------------------------------------------


def test_iban_mod97_valid_is_stored_without_spaces(db_session):
    customer = _make(db_session, uuid.uuid4(), iban=VALID_IBAN)
    assert customer.iban == "CH9300762011623852957"


def test_iban_mod97_invalid_is_rejected(db_session):
    with pytest.raises(ValueError):
        CustomerCreate(
            customer_type="individual",
            language="de",
            first_name="Ada",
            last_name="Byron",
            emails=[CustomerEmailCreate(email_type="personal", email_address="a@example.ch")],
            iban="CH93 0076 2011 6238 5295 8",  # last digit tampered
        )


def test_iban_is_valid_helper():
    assert iban_is_valid("CH9300762011623852957")
    assert not iban_is_valid("CH9300762011623852958")
    assert not iban_is_valid("not-an-iban")


# --- gender / title ----------------------------------------------------


def test_gender_defaults_to_unspecified_and_is_never_inferred(db_session):
    customer = _make(db_session, uuid.uuid4(), salutation="frau", first_name="Grace")
    assert customer.gender is Gender.UNSPECIFIED


def test_title_and_gender_are_individual_only_on_create():
    with pytest.raises(ValueError):
        CustomerCreate(
            customer_type="business",
            language="de",
            company_name="Byron AG",
            emails=[CustomerEmailCreate(email_type="work", email_address="b@example.ch")],
            title="Dr.",
        )
    with pytest.raises(ValueError):
        CustomerCreate(
            customer_type="business",
            language="de",
            company_name="Byron AG",
            emails=[CustomerEmailCreate(email_type="work", email_address="b@example.ch")],
            gender="female",
        )


def test_title_is_free_text_not_an_enum(db_session):
    customer = _make(db_session, uuid.uuid4(), title="lic. iur. HSG")
    assert customer.title == "lic. iur. HSG"


def test_gender_cannot_be_cleared_to_null_on_update():
    with pytest.raises(ValueError):
        CustomerUpdate(gender=None)


# --- website ----------------------------------------------------------


def test_website_scheme_is_normalised(db_session):
    customer = _make(db_session, uuid.uuid4(), website="byron.example")
    assert customer.website == "https://byron.example"


def test_website_keeps_an_explicit_https_scheme(db_session):
    customer = _make(db_session, uuid.uuid4(), website="http://byron.example/path")
    assert customer.website == "http://byron.example/path"


def test_website_rejects_a_non_http_scheme():
    with pytest.raises(ValueError):
        CustomerCreate(
            customer_type="individual",
            language="de",
            first_name="Ada",
            last_name="Byron",
            emails=[CustomerEmailCreate(email_type="personal", email_address="a@example.ch")],
            website="ftp://byron.example",
        )


# --- credit limit (advisory) / vat_registered ----------------------------


def test_credit_limit_is_stored_as_a_plain_advisory_decimal(db_session):
    customer = _make(db_session, uuid.uuid4(), credit_limit=Decimal("25000.00"))
    assert customer.credit_limit == Decimal("25000.00")
    # Advisory only — there is no enforcement hook anywhere on the model.
    assert not hasattr(customer, "credit_limit_enforced")


# --- advisor (P-2 three-column, D-24) -----------------------------------


def test_advisor_defaults_to_the_acting_user_on_create(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id, firstName="Rey", lastName="Ortiz")
    token = _token(tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"]))

    body = {
        "firstName": "Ada",
        "lastName": "Byron",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": "ada@example.ch"}],
    }
    resp = client.post("/v1/customers", json=body, headers=_bearer(token))
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["advisorId"] == user["id"]
    assert created["advisorLabel"] == "Rey Ortiz"
    assert created["advisorLabelRefreshedAt"] is not None


def test_advisor_can_be_set_explicitly_and_cleared_and_is_not_re_defaulted(client):
    dealer_id = _create_dealer(client)
    acting = _create_user(client, dealer_id, firstName="Acting", lastName="User")
    advisor = _create_user(client, dealer_id, firstName="Chosen", lastName="Advisor")
    token = _token(tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(acting["id"]))

    created = client.post(
        "/v1/customers",
        json={
            "firstName": "Ada",
            "lastName": "Byron",
            "language": "de",
            "advisorId": advisor["id"],
            "emails": [{"emailType": "personal", "emailAddress": "ada2@example.ch"}],
        },
        headers=_bearer(token),
    ).json()
    assert created["advisorId"] == advisor["id"]
    assert created["advisorLabel"] == "Chosen Advisor"

    # Clear it — all three columns go null, and a subsequent unrelated
    # PATCH does NOT re-default it (D-24).
    cleared = client.patch(
        f"/v1/customers/{created['id']}",
        json={"advisorId": None},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    ).json()
    assert cleared["advisorId"] is None
    assert cleared["advisorLabel"] is None
    assert cleared["advisorLabelRefreshedAt"] is None

    again = client.patch(
        f"/v1/customers/{created['id']}",
        json={"nextFollowUp": "2026-12-01"},
        headers={**_bearer(token), "If-Match": str(cleared["version"])},
    ).json()
    assert again["advisorId"] is None


def test_advisor_id_that_is_not_a_dealership_user_is_rejected(db_session):
    with pytest.raises(UnprocessableEntityError):
        _make(db_session, uuid.uuid4(), advisor_id=uuid.uuid4())


def test_advisor_options_lists_active_dealership_users_and_is_reachable_by_a_plain_advisor(client):
    dealer_id = _create_dealer(client)
    u1 = _create_user(client, dealer_id, firstName="Bea", lastName="Adams")
    u2 = _create_user(client, dealer_id, firstName="Cyrus", lastName="Bell")
    token = create_access_token(
        user_id=uuid.UUID(u1["id"]),
        tenant_id=uuid.UUID(dealer_id),
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, dealer_id),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=False,
    )
    resp = client.get("/v1/customers/advisor-options", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    labels = {row["label"] for row in resp.json()["items"]}
    assert {"Bea Adams", "Cyrus Bell"} <= labels
    ids = {row["id"] for row in resp.json()["items"]}
    assert {u1["id"], u2["id"]} <= ids


# --- tags -------------------------------------------------------------


def test_tags_are_trimmed_de_duplicated_and_stored_as_child_rows(db_session):
    customer = _make(db_session, uuid.uuid4(), tags=["  Flottenkunde ", "Oldtimer", "Flottenkunde", ""])
    rows = db_session.query(CustomerTag).filter(CustomerTag.customer_id == customer.id).all()
    assert sorted(r.tag for r in rows) == ["Flottenkunde", "Oldtimer"]
    assert all(r.group_id == customer.group_id for r in rows)


def test_tags_update_is_a_full_replace(db_session):
    group_id = uuid.uuid4()
    customer = _make(db_session, group_id, tags=["Alpha", "Beta"])
    update_customer(
        db_session,
        customer=customer,
        data=CustomerUpdate(tags=["Beta", "Gamma"]),
        actor_id=uuid.uuid4(),
        dealership_id=uuid.uuid4(),
    )
    rows = db_session.query(CustomerTag).filter(CustomerTag.customer_id == customer.id).all()
    assert sorted(r.tag for r in rows) == ["Beta", "Gamma"]


# --- provenance / dealership_id ---------------------------------------


def test_dealership_id_is_set_on_create_and_not_changed_by_an_update(db_session):
    dealership_id = uuid.uuid4()
    customer = create_customer(
        db_session,
        group_id=uuid.uuid4(),
        data=CustomerCreate(
            customer_type="individual",
            language="de",
            first_name="Ada",
            last_name="Byron",
            emails=[CustomerEmailCreate(email_type="personal", email_address="prov@example.ch")],
        ),
        actor_id=uuid.uuid4(),
        dealership_id=dealership_id,
    )
    assert customer.dealership_id == dealership_id

    update_customer(
        db_session,
        customer=customer,
        data=CustomerUpdate(first_name="Augusta"),
        actor_id=uuid.uuid4(),
        dealership_id=uuid.uuid4(),  # a different acting dealership
    )
    assert customer.dealership_id == dealership_id


# --- Phase C guard ---------------------------------------------------


def test_derived_fields_are_not_writable_in_phase_b2():
    for name in ("purchase_count", "lifetime_revenue", "open_deals", "vehicle_count", "last_contact_at", "service_due"):
        assert name not in CustomerCreate.model_fields, name
        assert name not in CustomerUpdate.model_fields, name
