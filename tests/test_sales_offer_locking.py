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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.sales.models.offer import OfferStatus, SalesOffer
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
