"""KAN-311: every route that changes an offer loads it `for_update`, as the
contract routes do since KAN-288/KAN-289. Without the lock two saves sent
with the same If-Match both passed the version check, and the later one
wrote `version += 1` from its stale read: both answered 200 with the same
new version, and an edit landing just after a finalisation or a
cancellation changed an offer that was no longer a draft."""

import threading
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.contract import cancel_contract, create_contract, get_contract_or_404
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.offer import cancel_offer, get_offer_or_404
from tests.test_sales_contract_edit import (  # noqa: F401 - the fixture is used by name
    _postgres_only_row_locks,
    _token,
    _valuation,
    app_sessions_on_the_test_engine,
)
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_offer_refinements import _offer_with_vehicle, _stock_item

# route -> (method, path suffix, body). Every route the offer API has that
# changes the row.
_LOCKED_ROUTES = {
    "patch": ("patch", "", {"discountType": "amount", "discountValue": "500.00"}),
    "line_items": (
        "put", "/line-items", {"accessories": [{"code": "MATS", "label": "Fussmatten", "unitPrice": "150.00"}]},
    ),
    "trade_in": ("post", "/trade-in", {"vin": "WVWZZZ1KZAW555555", "vehicleLabel": "Opel Corsa"}),
    "trade_in_valuation": ("post", "/trade-in/valuation", "valuation"),
    "finalize": ("post", "/finalize", None),
    "cancel": ("post", "/cancel", {"reason": "Kunde verzichtet"}),
}


@_postgres_only_row_locks
@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
@pytest.mark.parametrize("route", list(_LOCKED_ROUTES))
def test_a_changing_offer_route_waits_for_a_change_in_progress_and_is_refused(client, db_session, engine, route):
    """A session holds the offer row, as a cancellation does, while the real
    route runs in a thread. The route is still blocked after 0.8 s; once the
    holder commits it is refused as a stale version (its If-Match was read
    before), and the cancellation stands. Without `for_update` the route reads
    the row unlocked, passes the version check and writes over the holder's
    commit with 200."""

    method, suffix, body = _LOCKED_ROUTES[route]
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item, group_id=group_id)
    if body == "valuation":
        body = {"valuationId": str(_valuation(db_session, dealership.id, group_id, "WVWZZZ1KZAW666666", Decimal("5000.00")).id)}
    version = offer.version
    headers = {**_token(dealership.id, group_id), "If-Match": str(version)}
    events_before = db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.aggregate_id == offer.id)
    )
    result = {}

    def send() -> None:
        result["response"] = client.request(method, f"/v1/sales/offers/{offer.id}{suffix}", json=body, headers=headers)

    holding = Session(bind=engine)
    try:
        held = get_offer_or_404(holding, dealership.id, offer.id, for_update=True)
        sender = threading.Thread(target=send)
        sender.start()
        time.sleep(0.8)
        blocked_while_held = sender.is_alive()
        cancel_offer(holding, offer=held, reason="Kunde tritt zurück", actor_id=uuid.uuid4())
        sender.join(10)
    finally:
        holding.close()

    assert blocked_while_held
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["currentVersion"] == version + 1
    db_session.expire_all()
    after = db_session.get(SalesOffer, offer.id)
    assert (after.status, after.version, after.trade_in_valuation_id) == (OfferStatus.CANCELLED, version + 1, None)
    # The offer's only new event is the cancellation's, none from the route.
    assert db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.aggregate_id == offer.id)
    ) == events_before + 1


@_postgres_only_row_locks
@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_document_generated_during_an_offer_save_projects_the_saved_offer(client, db_session, engine):
    """KAN-408 review: generating a document re-projects the grid row, so the
    route loads the offer for update. A session holds the offer while it
    saves a new car onto it (as the PATCH does); the generation waits, then
    projects the saved car. Read unlocked, the route reads the old car, and
    the holder's share lock on `sales_document` stops its insert until the
    save has committed — so it then reads the saved grid row and writes the
    old car back onto it (the reviewer's reproduction, made deterministic)."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    offer = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id), group_id=group_id)
    result = {}

    def send() -> None:
        result["response"] = client.post(f"/v1/sales/offers/{offer.id}/documents", headers=_token(dealership.id, group_id))

    holding = Session(bind=engine)
    try:
        held = get_offer_or_404(holding, dealership.id, offer.id, for_update=True)
        holding.execute(text("LOCK TABLE sales_document IN SHARE MODE"))
        held.vehicle_label = "Cupra Born 58 kWh"
        upsert_deal_projection(holding, offer=held)
        sender = threading.Thread(target=send)
        sender.start()
        time.sleep(0.8)
        blocked_while_held = sender.is_alive()
        holding.commit()
        sender.join(10)
    finally:
        holding.close()

    assert blocked_while_held
    assert result["response"].status_code == 201, result["response"].text
    db_session.expire_all()
    deal = db_session.get(SalesDeal, offer.id)
    assert (deal.vehicle_label, deal.documents_count) == ("Cupra Born 58 kWh", 1)


@_postgres_only_row_locks
@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_document_generated_during_a_contract_cancellation_leaves_the_row_to_the_freed_offer(
    client, db_session, engine
):
    """The contract side: a session holds the pending contract and cancels it
    (KAN-409 hands the row back to the offer); a contract document generated
    meanwhile waits, then sees the cancellation — it is refused (a cancelled
    contract is not complete for printing) or projects the cancelled contract,
    which leaves the row to the offer either way. Read unlocked, it would put
    the pending contract back on the row."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    offer = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id), group_id=group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    result = {}

    def send() -> None:
        result["response"] = client.post(
            f"/v1/sales/contracts/{contract.id}/documents", headers=_token(dealership.id, group_id)
        )

    holding = Session(bind=engine)
    try:
        held = get_contract_or_404(holding, dealership.id, contract.id, for_update=True)
        sender = threading.Thread(target=send)
        sender.start()
        time.sleep(0.8)
        blocked_while_held = sender.is_alive()
        cancel_contract(holding, contract=held, reason="Kunde tritt zurück", actor_id=uuid.uuid4())
        sender.join(10)
    finally:
        holding.close()

    assert blocked_while_held
    assert result["response"].status_code in (201, 409), result["response"].text
    db_session.expire_all()
    deal = db_session.get(SalesDeal, offer.id)
    assert (deal.entity_type, deal.contract_id) == ("offer", None)
