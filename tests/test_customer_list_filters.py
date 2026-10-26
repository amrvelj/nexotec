"""List filters (FR-02): `?customerType=`, `?language=`, `?canton=` on the
customer list endpoint, on top of the existing `lifecycleStatus`/`updatedSince`
filters. Combined-filter and zero-result cases matter most here — each filter
clause is a trivial `.where()`, so the risk is in how they compose, not in any
one clause individually.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.auth import AccessRole, create_access_token
from tests.dealership_support import valid_uid

ZH_ADDRESS = {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"}
VD_ADDRESS = {"street": "Rue de Bourg", "houseNumber": "1", "postalCode": "1000", "locality": "Lausanne", "canton": "VD"}


def _as_customer_addresses(flat: dict, address_type: str = "domicile") -> list[dict]:
    """Customer's own nested `addresses` shape (WP-3 PR-5, ADR-067) — field
    names match CustomerAddress's own attributes, unlike Dealership's flat
    ZH_ADDRESS/VD_ADDRESS shape above. Canton is never sent; it's derived
    server-side from the postal code.
    """

    return [
        {
            "addressType": address_type,
            "addressStreet": flat["street"],
            "addressHouseNumber": flat["houseNumber"],
            "addressPostalCode": flat["postalCode"],
            "addressLocality": flat["locality"],
        }
    ]


def _token(role: AccessRole | None = None, tenant_id: uuid.UUID | None = None, *, is_dealer_manager: bool = False) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=_tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent", "address": ZH_ADDRESS, "phone": "+41441234567",
        "uid": valid_uid(),
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna", "lastName": "Muster", "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _list(client, dealer_id: str, **query):
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params=query, headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


def _business_email() -> dict:
    return {"emailType": "work", "emailAddress": f"biz-{uuid.uuid4().hex[:8]}@example.ch"}


# --- customerType --------------------------------------------------------------------


def test_filter_by_customer_type_individual(client):
    dealer_id = _create_dealer(client)
    individual = _create_customer(client, dealer_id, lastName="Solo")
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None,
        companyName="Alpha AG", emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="individual")
    ids = [c["id"] for c in body["items"]]
    assert ids == [individual["id"]]


def test_filter_by_customer_type_business(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lastName="Solo")
    business = _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None,
        companyName="Alpha AG", emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="business")
    ids = [c["id"] for c in body["items"]]
    assert ids == [business["id"]]


# --- language --------------------------------------------------------------------


def test_filter_by_language(client):
    dealer_id = _create_dealer(client)
    de_customer = _create_customer(client, dealer_id, lastName="Deutsch", language="de")
    _create_customer(client, dealer_id, lastName="Français", language="fr")

    body = _list(client, dealer_id, language="de")
    ids = [c["id"] for c in body["items"]]
    assert ids == [de_customer["id"]]


# --- canton (derived from address postal code) ------------------------------------------


def test_filter_by_canton(client):
    dealer_id = _create_dealer(client)
    zh_customer = _create_customer(client, dealer_id, lastName="Zurich", addresses=_as_customer_addresses(ZH_ADDRESS))
    _create_customer(client, dealer_id, lastName="Vaud", addresses=_as_customer_addresses(VD_ADDRESS))

    body = _list(client, dealer_id, canton="ZH")
    ids = [c["id"] for c in body["items"]]
    assert ids == [zh_customer["id"]]


# --- combined filters -----------------------------------------------------------------


def test_combined_filters_narrow_correctly(client):
    dealer_id = _create_dealer(client)
    target = _create_customer(client, dealer_id, lastName="Match", language="fr", addresses=_as_customer_addresses(VD_ADDRESS))
    _create_customer(client, dealer_id, lastName="WrongLanguage", language="de", addresses=_as_customer_addresses(VD_ADDRESS))
    _create_customer(client, dealer_id, lastName="WrongCanton", language="fr", addresses=_as_customer_addresses(ZH_ADDRESS))
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None, companyName="Beta AG",
        language="fr", addresses=_as_customer_addresses(VD_ADDRESS), emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="individual", language="fr", canton="VD")
    ids = [c["id"] for c in body["items"]]
    assert ids == [target["id"]]


def test_combined_filters_with_no_matches_returns_empty(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lastName="Deutsch", language="de", addresses=_as_customer_addresses(ZH_ADDRESS))

    body = _list(client, dealer_id, customer_type="business", language="it", canton="GE")
    assert body["items"] == []
    assert body["total"] == 0


# --- updated more than N days ago (KAN-458) -----------------------------------------------


def _backdate(db_session, customer_id: str, when: dt.datetime) -> None:
    db_session.execute(text("UPDATE customer SET updated_at = :when WHERE id = :id"), {"when": when, "id": customer_id})
    db_session.commit()


def test_filter_by_updated_more_than_days_ago(client, db_session):
    """The grid's «Changed: more than 90 days ago» — the server resolves it
    against its own clock, so it can no longer be offered and dropped."""
    dealer_id = _create_dealer(client)
    stale = _create_customer(client, dealer_id, lastName="Stale")
    recent = _create_customer(client, dealer_id, lastName="Recent")
    _create_customer(client, dealer_id, lastName="Fresh")
    now = dt.datetime.now(dt.UTC)
    _backdate(db_session, stale["id"], now - dt.timedelta(days=120))
    _backdate(db_session, recent["id"], now - dt.timedelta(days=30))

    body = _list(client, dealer_id, updated_more_than_days_ago=90)
    assert [c["id"] for c in body["items"]] == [stale["id"]]
    assert body["total"] == 1


def test_updated_more_than_days_ago_counts_whole_swiss_calendar_days():
    """N days ago means before the start of that calendar day in Zürich — at
    00:30 Swiss time (still the previous day in UTC) the cut-off is Swiss
    midnight N days back, not UTC midnight."""
    from app.customer.services.customer import _start_of_day_days_ago

    zurich = ZoneInfo("Europe/Zurich")
    now = dt.datetime(2026, 10, 26, 0, 30, tzinfo=zurich)
    assert _start_of_day_days_ago(0, now) == dt.datetime(2026, 10, 26, tzinfo=zurich)
    assert _start_of_day_days_ago(90, now) == dt.datetime(2026, 7, 28, tzinfo=zurich)


def test_updated_more_than_days_ago_refuses_a_negative_count(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params={"updated_more_than_days_ago": -1}, headers=_bearer(token))
    assert response.status_code == 422, response.text
