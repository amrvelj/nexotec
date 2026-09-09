"""KAN-45 — the Customer 360 "Offers & contracts" tab needs an optional
``customer_id`` filter on the offer and contract list endpoints (FR-06 tab 3,
ADR-050). The tab is dealership-scoped: these endpoints keep scoping on
``principal.tenant_id`` and the filter narrows within that.
"""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.sorting import SortField
from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.services.contract import create_contract, list_contracts
from app.sales.services.offer import create_offer, list_offers

_SORT = [SortField(api_name="updatedAt", column=SalesOffer.updated_at, direction="desc", nullable=False)]
_CONTRACT_SORT = [SortField(api_name="updatedAt", column=SalesContract.updated_at, direction="desc", nullable=False)]


def _token(role: AccessRole | None = AccessRole.SALES) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(),
        is_dealer_manager=False,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _params(limit: int = 50, cursor=None, *, contract: bool = False) -> SortPageParams:
    return SortPageParams(limit=limit, cursor=cursor, sort_fields=_CONTRACT_SORT if contract else _SORT)


# --- offers -----------------------------------------------------------------


def test_list_offers_filters_by_customer_id(db_session):
    tenant_id = uuid.uuid4()
    customer_a, customer_b = uuid.uuid4(), uuid.uuid4()

    for _ in range(2):
        offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
        offer.customer_id = customer_a
    other = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    other.customer_id = customer_b
    db_session.flush()

    rows_a, _cursor, total_a, _est = list_offers(
        db_session, tenant_id=tenant_id, customer_id=customer_a, params=_params()
    )
    assert total_a == 2
    assert {r.customer_id for r in rows_a} == {customer_a}

    _rows_b, _cursor, total_b, _est = list_offers(
        db_session, tenant_id=tenant_id, customer_id=customer_b, params=_params()
    )
    assert total_b == 1


def test_list_offers_unfiltered_is_unchanged(db_session):
    """Regression guard: the Sales overview passes no customer_id and must
    still see every offer for the tenant."""

    tenant_id = uuid.uuid4()
    for _ in range(3):
        offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
        offer.customer_id = uuid.uuid4()
    db_session.flush()

    _rows, _cursor, total, _est = list_offers(db_session, tenant_id=tenant_id, params=_params())
    assert total == 3


def test_list_offers_customer_filter_respects_tenant(db_session):
    shared_customer = uuid.uuid4()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    a = create_offer(db_session, tenant_id=tenant_a, actor_id=uuid.uuid4())
    a.customer_id = shared_customer
    b = create_offer(db_session, tenant_id=tenant_b, actor_id=uuid.uuid4())
    b.customer_id = shared_customer
    db_session.flush()

    _rows, _cursor, total, _est = list_offers(
        db_session, tenant_id=tenant_a, customer_id=shared_customer, params=_params()
    )
    assert total == 1


def test_list_offers_customer_filter_paginates(db_session):
    tenant_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    for _ in range(3):
        offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
        offer.customer_id = customer_id
    db_session.flush()

    page1, cursor1, total, _est = list_offers(
        db_session, tenant_id=tenant_id, customer_id=customer_id, params=_params(limit=2)
    )
    assert total == 3
    assert len(page1) == 2
    assert cursor1 is not None

    page2, cursor2, _total, _est = list_offers(
        db_session,
        tenant_id=tenant_id,
        customer_id=customer_id,
        params=_params(limit=2, cursor=decode_sort_cursor(cursor1)),
    )
    assert len(page2) == 1
    assert cursor2 is None
    assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


# --- contracts ------------------------------------------------------------


def test_list_contracts_filters_by_customer_id(db_session):
    tenant_id = uuid.uuid4()
    customer_a, customer_b = uuid.uuid4(), uuid.uuid4()

    for _ in range(2):
        contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())
        contract.customer_id = customer_a
    other = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())
    other.customer_id = customer_b
    db_session.flush()

    rows_a, _cursor, total_a, _est = list_contracts(
        db_session, tenant_id=tenant_id, customer_id=customer_a, params=_params(contract=True)
    )
    assert total_a == 2
    assert {r.customer_id for r in rows_a} == {customer_a}


def test_list_contracts_unfiltered_is_unchanged(db_session):
    tenant_id = uuid.uuid4()
    for _ in range(2):
        create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())
    db_session.flush()

    _rows, _cursor, total, _est = list_contracts(db_session, tenant_id=tenant_id, params=_params(contract=True))
    assert total == 2


# --- API surface ---------------------------------------------------------


def test_offers_endpoint_accepts_customer_id(client):
    token = _token()
    client.post("/v1/sales/offers", headers=_bearer(token))  # a draft offer, no customer

    match = client.get(f"/v1/sales/offers?customer_id={uuid.uuid4()}", headers=_bearer(token))
    assert match.status_code == 200, match.text
    assert match.json()["items"] == []

    unfiltered = client.get("/v1/sales/offers", headers=_bearer(token))
    assert unfiltered.status_code == 200
    assert unfiltered.json()["total"] == 1


def test_contracts_endpoint_accepts_customer_id(client):
    token = _token()
    client.post("/v1/sales/contracts", json={}, headers=_bearer(token))

    match = client.get(f"/v1/sales/contracts?customer_id={uuid.uuid4()}", headers=_bearer(token))
    assert match.status_code == 200, match.text
    assert match.json()["items"] == []


def test_offers_endpoint_rejects_a_non_uuid_customer_id(client):
    token = _token()
    response = client.get("/v1/sales/offers?customer_id=not-a-uuid", headers=_bearer(token))
    assert response.status_code == 422, response.text
