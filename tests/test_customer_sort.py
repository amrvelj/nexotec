"""Server-side sort (U-02): `?sort=field:dir[,field:dir]` on the customer
list endpoint. Heaviest coverage is on pagination continuation under a
non-default sort — that's where a keyset-predicate bug would actually bite
(duplicate or missing rows across pages), not in a single unpaginated call.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.errors import UnprocessableEntityError
from app.core.sorting import parse_sort
from app.customer.models.customer import Customer

VALID_ADDRESS = {
    "street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH",
}


def _token(role: AccessRole | None = None, tenant_id: uuid.UUID | None = None, *, is_dealer_manager: bool = False) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG", "dealerLicenseNumber": "ZH-12345", "licenseState": "ZH",
        "franchiseType": "independent", "address": VALID_ADDRESS, "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna", "lastName": "Muster", "language": "de",
        "emails": [{"emailType": "private", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
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


def _list_all_pages(client, dealer_id: str, *, sort: str, limit: int) -> list[dict]:
    items: list[dict] = []
    cursor = None
    for _ in range(50):  # hard stop so a broken cursor can't loop forever
        page = _list(client, dealer_id, sort=sort, limit=limit, **({"cursor": cursor} if cursor else {}))
        items.extend(page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    else:
        pytest.fail("Pagination did not terminate within 50 pages.")
    return items


# --- parse_sort unit tests ----------------------------------------------------------


def test_parse_sort_empty_returns_empty():
    assert parse_sort(None, allowed={"lastName": Customer.last_name}) == []
    assert parse_sort("", allowed={"lastName": Customer.last_name}) == []


def test_parse_sort_valid_multi_field():
    fields = parse_sort(
        "lastName:asc,updatedAt:desc",
        allowed={"lastName": Customer.last_name, "updatedAt": Customer.updated_at},
    )
    assert [(f.api_name, f.direction) for f in fields] == [("lastName", "asc"), ("updatedAt", "desc")]


def test_parse_sort_unknown_field_raises():
    with pytest.raises(UnprocessableEntityError):
        parse_sort("nonsense:asc", allowed={"lastName": Customer.last_name})


def test_parse_sort_invalid_direction_raises():
    with pytest.raises(UnprocessableEntityError):
        parse_sort("lastName:sideways", allowed={"lastName": Customer.last_name})


def test_parse_sort_malformed_spec_raises():
    with pytest.raises(UnprocessableEntityError):
        parse_sort("lastName", allowed={"lastName": Customer.last_name})


def test_parse_sort_duplicate_field_raises():
    with pytest.raises(UnprocessableEntityError):
        parse_sort("lastName:asc,lastName:desc", allowed={"lastName": Customer.last_name})


# --- API: single field, both directions ---------------------------------------------


def test_default_sort_is_updated_at_desc(client):
    dealer_id = _create_dealer(client)
    first = _create_customer(client, dealer_id, lastName="First")
    second = _create_customer(client, dealer_id, lastName="Second")

    body = _list(client, dealer_id)
    ids_in_order = [c["id"] for c in body["items"]]
    assert ids_in_order.index(second["id"]) < ids_in_order.index(first["id"])


def test_sort_by_customer_number_ascending(client):
    dealer_id = _create_dealer(client)
    for _ in range(3):
        _create_customer(client, dealer_id)
    body = _list(client, dealer_id, sort="customerNumber:asc")
    numbers = [c["customerNumber"] for c in body["items"]]
    assert numbers == sorted(numbers)


def test_sort_by_customer_number_descending(client):
    dealer_id = _create_dealer(client)
    for _ in range(3):
        _create_customer(client, dealer_id)
    body = _list(client, dealer_id, sort="customerNumber:desc")
    numbers = [c["customerNumber"] for c in body["items"]]
    assert numbers == sorted(numbers, reverse=True)


# --- unknown / invalid sort specs return 422, never a silent ignore -----------------


def test_unknown_sort_field_is_422(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params={"sort": "nonsense:asc"}, headers=_bearer(token))
    assert response.status_code == 422, response.text


def test_invalid_sort_direction_is_422(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params={"sort": "customerNumber:up"}, headers=_bearer(token))
    assert response.status_code == 422, response.text


# --- nulls always sort last, in both directions --------------------------------------


def test_nulls_sort_last_ascending(client):
    dealer_id = _create_dealer(client)
    # companyName is null for every individual customer; only business
    # customers set it.
    _create_customer(client, dealer_id, lastName="NoCompany")
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None, companyName="Alpha AG",
        emails=[{"emailType": "business", "emailAddress": f"alpha-{uuid.uuid4().hex[:8]}@example.ch"}],
    )

    body = _list(client, dealer_id, sort="companyName:asc")
    company_names = [c["companyName"] for c in body["items"]]
    non_null = [c for c in company_names if c is not None]
    assert company_names[len(non_null):] == [None] * (len(company_names) - len(non_null))


def test_nulls_sort_last_descending(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lastName="NoCompany")
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None, companyName="Alpha AG",
        emails=[{"emailType": "business", "emailAddress": f"alpha-{uuid.uuid4().hex[:8]}@example.ch"}],
    )

    body = _list(client, dealer_id, sort="companyName:desc")
    company_names = [c["companyName"] for c in body["items"]]
    non_null = [c for c in company_names if c is not None]
    # Nulls last even in descending order — not first, which naive DESC would give.
    assert company_names[len(non_null):] == [None] * (len(company_names) - len(non_null))


# --- pagination continuation under a non-default sort --------------------------------


def test_pagination_with_sort_visits_every_row_exactly_once(client):
    dealer_id = _create_dealer(client)
    created = [_create_customer(client, dealer_id, lastName=f"Name{i:02d}") for i in range(12)]

    collected = _list_all_pages(client, dealer_id, sort="lastName:asc", limit=5)

    assert len(collected) == len(created)
    assert {c["id"] for c in collected} == {c["id"] for c in created}
    last_names = [c["lastName"] for c in collected]
    assert last_names == sorted(last_names)


def test_pagination_with_ties_on_sort_field_is_stable_via_id_tiebreak(client):
    """Multiple customers can share the same lastName — the id tiebreak
    must still make pagination deterministic and gap/duplicate-free.
    """

    dealer_id = _create_dealer(client)
    created = [_create_customer(client, dealer_id, lastName="Sameperson") for _ in range(8)]

    collected = _list_all_pages(client, dealer_id, sort="lastName:asc", limit=3)

    assert len(collected) == len(created)
    assert {c["id"] for c in collected} == {c["id"] for c in created}


def test_pagination_with_nulls_and_ties_across_pages(client):
    """companyName is null for every individual in this test — exercises
    the null-tail continuation path across a page boundary, not just
    within a single unpaginated response.
    """

    dealer_id = _create_dealer(client)
    created = [_create_customer(client, dealer_id, lastName=f"Ind{i}") for i in range(7)]

    collected = _list_all_pages(client, dealer_id, sort="companyName:asc", limit=3)

    assert len(collected) == len(created)
    assert {c["id"] for c in collected} == {c["id"] for c in created}


def test_pagination_with_multi_field_sort(client):
    """Matches the UI/UX doc's own saved-view example:
    sort=[{field:"lastName", direction:"asc"}, {field:"updatedAt", direction:"desc"}]
    """

    dealer_id = _create_dealer(client)
    created = [_create_customer(client, dealer_id, lastName="Duplicate") for _ in range(4)]
    created += [_create_customer(client, dealer_id, lastName=f"Unique{i}") for i in range(4)]

    collected = _list_all_pages(client, dealer_id, sort="lastName:asc,updatedAt:desc", limit=3)

    assert len(collected) == len(created)
    assert {c["id"] for c in collected} == {c["id"] for c in created}
    last_names = [c["lastName"] for c in collected]
    assert last_names == sorted(last_names)
