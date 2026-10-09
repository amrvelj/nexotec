"""KAN-266 step 5 (sales): every sales POST honours Idempotency-Key.

Per route: the same key twice makes one record (or runs the action once) and
answers the same; the same key with a different request is a 409 for the
key. A route without a body has nothing to differ in, so its 409 case is the
same key sent to another target. The mechanism itself is pinned in
tests/test_idempotent_route.py.

The If-Match transitions (trade-in, trade-in valuation, finalize, the two
cancels, confirm, request-invoice) are retried with the If-Match they were
first sent with: after the first success it is stale, so a retry without the
route class is a 409 version conflict instead of the original success.

The legacy /transactions writes are retired (WP-8 PR-7): they refuse every
call, so under a key they refuse again and leave no record behind.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.core.idempotency_model import IdempotencyRecord
from app.core.outbox_model import OutboxMessage
from app.db import SessionLocal
from app.inventory.models.stock_item import ReservationState, StockItem
from app.sales.models.contract import SalesContract
from app.sales.models.document import SalesDocument
from app.sales.models.offer import SalesOffer
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.stock_item_purchase import record_stock_item_purchased
from app.valuation.models.valuation import ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from app.vehicle.models.vehicle_mdm import VehicleMdm
from tests.test_sales_lifecycle_reservation import _customer, _dealership, _session_factory
from tests.test_sales_offer_refinements import _offer_with_vehicle, _stock_item
from tests.test_transaction import _seed_transaction_directly, _setup, _transaction_payload


def _token(tenant_id: uuid.UUID | None = None, group_id: uuid.UUID | None = None, **flags) -> str:
    tenant_id = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=group_id or uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({AccessRole.SALES}),
        **flags,
    )


def _headers(token: str, key: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", **extra}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def _assert_key_conflict(response, key: str) -> None:
    """A 409 for the reused key, not for some other conflict."""

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["idempotencyKey"] == key


def _twice(client, path: str, token: str, body: dict | None, **extra: str):
    key = str(uuid.uuid4())
    first = client.post(path, json=body, headers=_headers(token, key, **extra))
    second = client.post(path, json=body, headers=_headers(token, key, **extra))
    assert first.status_code in (200, 201), first.text
    assert second.status_code == first.status_code, second.text
    assert second.json() == first.json()
    return key, first.json()


def _offer(client, token: str) -> dict:
    response = client.post("/v1/sales/offers", headers=_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


def _contract(client, token: str) -> dict:
    response = client.post("/v1/sales/contracts", json={}, headers=_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


def _if_match(record: dict) -> str:
    return str(record["version"])


def _confirmable_contract(db_session, dealership_id: uuid.UUID, group_id: uuid.UUID) -> SalesContract:
    """A pending contract with a priced stock car and a customer with an
    address: everything confirmation asks for."""

    item = _stock_item(db_session, dealership_id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership_id, item, group_id=group_id)
    return create_contract(db_session, tenant_id=dealership_id, offer=offer, actor_id=uuid.uuid4())


@pytest.fixture()
def app_sessions_on_the_test_engine(monkeypatch, engine):
    """confirm_contract reserves the car on a session of its own (ADR-047),
    from app.db.SessionLocal; over HTTP nothing overrides it, so point it at
    the test engine."""

    monkeypatch.setitem(SessionLocal.kw, "bind", engine)


def _with_address(db_session, contract: SalesContract, group_id: uuid.UUID) -> SalesContract:
    contract.customer_id = _customer(db_session, group_id).id
    db_session.commit()
    return contract


# --- POST /v1/sales/offers (no body) -------------------------------------------


def test_create_offer_twice_under_one_key_makes_one_offer(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)

    _twice(client, "/v1/sales/offers", token, None)

    assert _count(db_session, SalesOffer, SalesOffer.tenant_id == tenant_id) == 1


def test_an_offer_create_key_reused_for_a_contract_create_is_a_409(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    key, _ = _twice(client, "/v1/sales/offers", token, None)

    response = client.post("/v1/sales/contracts", json={}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, SalesContract, SalesContract.tenant_id == tenant_id) == 0


# --- POST /v1/sales/offers/{id}/copy (no body) -----------------------------------


def test_copy_offer_twice_under_one_key_makes_one_copy(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    offer = _offer(client, token)

    _, copy = _twice(client, f"/v1/sales/offers/{offer['id']}/copy", token, None)

    assert copy["id"] != offer["id"]
    assert _count(db_session, SalesOffer, SalesOffer.tenant_id == tenant_id) == 2


def test_copy_offer_with_a_reused_key_on_another_offer_is_a_409(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    first, other = _offer(client, token), _offer(client, token)
    key, _ = _twice(client, f"/v1/sales/offers/{first['id']}/copy", token, None)

    response = client.post(f"/v1/sales/offers/{other['id']}/copy", headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, SalesOffer, SalesOffer.tenant_id == tenant_id) == 3


# --- POST /v1/sales/offers/{id}/trade-in (If-Match) ------------------------------


def _trade_in_body(vin: str) -> dict:
    return {"vin": vin, "vehicleLabel": "Skoda Octavia Combi 1.5 TSI"}


def test_a_retried_trade_in_replays_its_success_instead_of_a_version_conflict(client, db_session):
    token = _token()
    offer = _offer(client, token)
    vin = "WVWZZZ1KZAW100001"

    _, updated = _twice(
        client, f"/v1/sales/offers/{offer['id']}/trade-in", token, _trade_in_body(vin), **{"If-Match": _if_match(offer)}
    )

    assert updated["version"] == offer["version"] + 1
    assert _count(db_session, VehicleMdm, VehicleMdm.vin == vin) == 1


def test_a_trade_in_with_a_reused_key_and_another_vin_is_a_409(client, db_session):
    token = _token()
    offer = _offer(client, token)
    key, updated = _twice(
        client,
        f"/v1/sales/offers/{offer['id']}/trade-in",
        token,
        _trade_in_body("WVWZZZ1KZAW100002"),
        **{"If-Match": _if_match(offer)},
    )

    response = client.post(
        f"/v1/sales/offers/{offer['id']}/trade-in",
        json=_trade_in_body("WVWZZZ1KZAW100003"),
        headers=_headers(token, key, **{"If-Match": _if_match(updated)}),
    )

    _assert_key_conflict(response, key)
    assert _count(db_session, VehicleMdm, VehicleMdm.vin == "WVWZZZ1KZAW100003") == 0


# --- POST /v1/sales/offers/{id}/trade-in/valuation (If-Match) --------------------


def _valuation(db_session, tenant_id: uuid.UUID, vin: str):
    return create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        data=ValuationCreate(
            vin=vin, vehicle_make="VW", vehicle_model="Golf", source=ValuationSource.MANUAL, final_offer=Decimal("12000.00")
        ),
        actor_id=uuid.uuid4(),
    )


def test_a_retried_valuation_attach_replays_its_success_instead_of_a_version_conflict(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    offer = _offer(client, token)
    valuation = _valuation(db_session, tenant_id, "WVWZZZ1KZAW200001")

    _, updated = _twice(
        client,
        f"/v1/sales/offers/{offer['id']}/trade-in/valuation",
        token,
        {"valuationId": str(valuation.id)},
        **{"If-Match": _if_match(offer)},
    )

    assert updated["tradeInValuationId"] == str(valuation.id)
    assert updated["version"] == offer["version"] + 1


def test_a_valuation_attach_with_a_reused_key_and_another_valuation_is_a_409(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    offer = _offer(client, token)
    first = _valuation(db_session, tenant_id, "WVWZZZ1KZAW200002")
    other = _valuation(db_session, tenant_id, "WVWZZZ1KZAW200003")
    key, updated = _twice(
        client,
        f"/v1/sales/offers/{offer['id']}/trade-in/valuation",
        token,
        {"valuationId": str(first.id)},
        **{"If-Match": _if_match(offer)},
    )

    response = client.post(
        f"/v1/sales/offers/{offer['id']}/trade-in/valuation",
        json={"valuationId": str(other.id)},
        headers=_headers(token, key, **{"If-Match": _if_match(updated)}),
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(offer["id"])).trade_in_valuation_id == first.id


# --- POST /v1/sales/offers/{id}/finalize (If-Match, no body) ---------------------


def test_a_retried_finalize_replays_its_success_instead_of_a_version_conflict(client, db_session):
    dealership = _dealership(db_session)
    offer = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id, with_option=False))
    token = _token(dealership.id)

    _, finalized = _twice(
        client, f"/v1/sales/offers/{offer.id}/finalize", token, None, **{"If-Match": str(offer.version)}
    )

    assert finalized["status"] == "open"
    assert finalized["version"] == offer.version + 1


def test_finalize_with_a_reused_key_on_another_offer_is_a_409(client, db_session):
    dealership = _dealership(db_session)
    first = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id, with_option=False))
    other = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id, with_option=False))
    token = _token(dealership.id)
    key, _ = _twice(client, f"/v1/sales/offers/{first.id}/finalize", token, None, **{"If-Match": str(first.version)})

    response = client.post(
        f"/v1/sales/offers/{other.id}/finalize", headers=_headers(token, key, **{"If-Match": str(other.version)})
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(SalesOffer, other.id).status.value == "draft"


# --- POST /v1/sales/offers/{id}/cancel (If-Match) --------------------------------


def test_a_retried_offer_cancel_replays_its_success_instead_of_a_version_conflict(client):
    token = _token()
    offer = _offer(client, token)

    _, cancelled = _twice(
        client,
        f"/v1/sales/offers/{offer['id']}/cancel",
        token,
        {"reason": "Kunde hat abgesagt."},
        **{"If-Match": _if_match(offer)},
    )

    assert cancelled["status"] == "cancelled"


def test_an_offer_cancel_with_a_reused_key_and_another_reason_is_a_409(client):
    token = _token()
    first, other = _offer(client, token), _offer(client, token)
    key, _ = _twice(
        client,
        f"/v1/sales/offers/{first['id']}/cancel",
        token,
        {"reason": "Kunde hat abgesagt."},
        **{"If-Match": _if_match(first)},
    )

    response = client.post(
        f"/v1/sales/offers/{other['id']}/cancel",
        json={"reason": "Fahrzeug verkauft."},
        headers=_headers(token, key, **{"If-Match": _if_match(other)}),
    )

    _assert_key_conflict(response, key)
    assert client.get(f"/v1/sales/offers/{other['id']}", headers=_headers(token)).json()["status"] == "draft"


# --- POST /v1/sales/offers/{id}/documents (no body) -------------------------------


def test_generate_offer_document_twice_under_one_key_makes_one_document(client, db_session):
    dealership = _dealership(db_session)
    token = _token(dealership.id)
    offer = _offer(client, token)

    _, document = _twice(client, f"/v1/sales/offers/{offer['id']}/documents", token, None)

    assert document["version"] == 1
    assert _count(db_session, SalesDocument, SalesDocument.owner_id == uuid.UUID(offer["id"])) == 1


def test_generate_offer_document_with_a_reused_key_on_another_offer_is_a_409(client, db_session):
    dealership = _dealership(db_session)
    token = _token(dealership.id)
    first, other = _offer(client, token), _offer(client, token)
    key, _ = _twice(client, f"/v1/sales/offers/{first['id']}/documents", token, None)

    response = client.post(f"/v1/sales/offers/{other['id']}/documents", headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, SalesDocument, SalesDocument.owner_id == uuid.UUID(other["id"])) == 0


# --- POST /v1/sales/contracts ------------------------------------------------------


def test_create_contract_twice_under_one_key_makes_one_contract(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)

    _twice(client, "/v1/sales/contracts", token, {})

    assert _count(db_session, SalesContract, SalesContract.tenant_id == tenant_id) == 1


def test_create_contract_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    offer = _offer(client, token)
    key, _ = _twice(client, "/v1/sales/contracts", token, {})

    response = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, SalesContract, SalesContract.offer_id == uuid.UUID(offer["id"])) == 0


# --- POST /v1/sales/contracts/{id}/confirm (If-Match, no body) --------------------


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_retried_confirm_replays_its_success_instead_of_a_version_conflict(client, db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _with_address(db_session, _confirmable_contract(db_session, dealership.id, group_id), group_id)
    token = _token(dealership.id, group_id)

    _, confirmed = _twice(
        client, f"/v1/sales/contracts/{contract.id}/confirm", token, None, **{"If-Match": str(contract.version)}
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["reservationId"] is not None
    assert _count(db_session, OutboxMessage, OutboxMessage.event_type == "sales.contract.confirmed",
                  OutboxMessage.aggregate_id == contract.id) == 1


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_confirm_with_a_reused_key_on_another_contract_is_a_409(client, db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    first = _with_address(db_session, _confirmable_contract(db_session, dealership.id, group_id), group_id)
    other = _with_address(db_session, _confirmable_contract(db_session, dealership.id, group_id), group_id)
    token = _token(dealership.id, group_id)
    key, _ = _twice(client, f"/v1/sales/contracts/{first.id}/confirm", token, None, **{"If-Match": str(first.version)})

    response = client.post(
        f"/v1/sales/contracts/{other.id}/confirm", headers=_headers(token, key, **{"If-Match": str(other.version)})
    )

    _assert_key_conflict(response, key)
    assert _count(db_session, StockItem, StockItem.id == other.stock_item_id,
                  StockItem.reservation_state == ReservationState.RESERVED) == 0


# --- POST /v1/sales/contracts/{id}/request-invoice (If-Match, no body) -------------


def _invoiceable_contract(db_session, engine, dealership_id: uuid.UUID, group_id: uuid.UUID) -> SalesContract:
    contract = _with_address(db_session, _confirmable_contract(db_session, dealership_id, group_id), group_id)
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )
    record_stock_item_purchased(
        db_session, tenant_id=dealership_id, stock_item_id=contract.stock_item_id, event_id=uuid.uuid4(),
        stock_item_label="S-000001",
    )
    db_session.commit()
    db_session.refresh(contract)
    return contract


def test_a_retried_invoice_request_publishes_one_event(client, db_session, engine):
    """request-invoice changes no version: without the key, a retry under the
    same If-Match ran again and published a second invoice request."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    token = _token(dealership.id, group_id)

    _twice(client, f"/v1/sales/contracts/{contract.id}/request-invoice", token, None,
           **{"If-Match": str(contract.version)})

    assert _count(db_session, OutboxMessage, OutboxMessage.event_type == "sales.contract.invoice_requested",
                  OutboxMessage.aggregate_id == contract.id) == 1


def test_request_invoice_with_a_reused_key_on_another_contract_is_a_409(client, db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    first = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    other = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    token = _token(dealership.id, group_id)
    key, _ = _twice(client, f"/v1/sales/contracts/{first.id}/request-invoice", token, None,
                    **{"If-Match": str(first.version)})

    response = client.post(
        f"/v1/sales/contracts/{other.id}/request-invoice",
        headers=_headers(token, key, **{"If-Match": str(other.version)}),
    )

    _assert_key_conflict(response, key)
    assert _count(db_session, OutboxMessage, OutboxMessage.event_type == "sales.contract.invoice_requested",
                  OutboxMessage.aggregate_id == other.id) == 0


# --- POST /v1/sales/contracts/{id}/cancel (If-Match) -------------------------------


def test_a_retried_contract_cancel_replays_its_success_instead_of_a_version_conflict(client):
    token = _token()
    contract = _contract(client, token)

    _, cancelled = _twice(
        client,
        f"/v1/sales/contracts/{contract['id']}/cancel",
        token,
        {"reason": "Kunde storniert."},
        **{"If-Match": _if_match(contract)},
    )

    assert cancelled["status"] == "cancelled"


def test_a_contract_cancel_with_a_reused_key_and_another_reason_is_a_409(client):
    token = _token()
    first, other = _contract(client, token), _contract(client, token)
    key, _ = _twice(
        client,
        f"/v1/sales/contracts/{first['id']}/cancel",
        token,
        {"reason": "Kunde storniert."},
        **{"If-Match": _if_match(first)},
    )

    response = client.post(
        f"/v1/sales/contracts/{other['id']}/cancel",
        json={"reason": "Finanzierung abgelehnt."},
        headers=_headers(token, key, **{"If-Match": _if_match(other)}),
    )

    _assert_key_conflict(response, key)
    assert client.get(f"/v1/sales/contracts/{other['id']}", headers=_headers(token)).json()["status"] == "pending"


# --- POST /v1/sales/contracts/{id}/documents (no body) -----------------------------


def test_generate_contract_document_twice_under_one_key_makes_one_document(client, db_session):
    dealership = _dealership(db_session)
    token = _token(dealership.id)
    contract = _contract(client, token)

    _, document = _twice(client, f"/v1/sales/contracts/{contract['id']}/documents", token, None)

    assert document["version"] == 1
    assert _count(db_session, SalesDocument, SalesDocument.owner_id == uuid.UUID(contract["id"])) == 1


def test_generate_contract_document_with_a_reused_key_on_another_contract_is_a_409(client, db_session):
    dealership = _dealership(db_session)
    token = _token(dealership.id)
    first, other = _contract(client, token), _contract(client, token)
    key, _ = _twice(client, f"/v1/sales/contracts/{first['id']}/documents", token, None)

    response = client.post(f"/v1/sales/contracts/{other['id']}/documents", headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, SalesDocument, SalesDocument.owner_id == uuid.UUID(other["id"])) == 0


# --- the retired /v1/transactions writes ---------------------------------------------


def _refused_twice_under_one_key(client, db_session, path: str, token: str, body: dict | None, **extra: str) -> None:
    """Both calls get the retirement 409, not a 409 for the key: a refused
    request releases its claim, and no record is left for the key."""

    key = str(uuid.uuid4())
    for _ in range(2):
        response = client.post(path, json=body, headers=_headers(token, key, **extra))
        assert response.status_code == 409, response.text
        assert "successor" in response.json()["error"]["details"]
    assert _count(db_session, IdempotencyRecord, IdempotencyRecord.idempotency_key == key) == 0


def test_a_transaction_create_under_a_key_is_still_refused_and_leaves_no_record(client, db_session):
    dealer_id, user, customer, vehicle = _setup(client)
    token = _token(uuid.UUID(dealer_id), is_dealer_manager=True)

    _refused_twice_under_one_key(
        client, db_session, "/v1/transactions", token, _transaction_payload(user, customer, vehicle)
    )


def test_a_transaction_complete_under_a_key_is_still_refused_and_leaves_no_record(client, db_session, engine):
    dealer_id, user, customer, vehicle = _setup(client)
    transaction = _seed_transaction_directly(engine, dealer_id=dealer_id, user=user, customer=customer, vehicle=vehicle)
    token = _token(uuid.UUID(dealer_id), is_dealer_manager=True)

    _refused_twice_under_one_key(
        client, db_session, f"/v1/transactions/{transaction.id}/complete", token, None, **{"If-Match": "1"}
    )


def test_a_transaction_cancel_under_a_key_is_still_refused_and_leaves_no_record(client, db_session, engine):
    dealer_id, user, customer, vehicle = _setup(client)
    transaction = _seed_transaction_directly(engine, dealer_id=dealer_id, user=user, customer=customer, vehicle=vehicle)
    token = _token(uuid.UUID(dealer_id), is_dealer_manager=True)

    _refused_twice_under_one_key(
        client, db_session, f"/v1/transactions/{transaction.id}/cancel", token, {"reason": "irrelevant now"},
        **{"If-Match": "1"},
    )
