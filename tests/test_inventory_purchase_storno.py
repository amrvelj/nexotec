"""KAN-146 (ADR-052, PRD-Stock FR-I-05; Anto, 2026-11-05): «Einkauf
stornieren». Stock reverses a booked purchase — Wagenbuch counter-entry,
purchase facts cleared, the car back in pipeline with its VIN — and publishes
`inventory.stock_item.storno`; Sales consumes it and deletes its purchase
replica, so the contract derives «nicht fakturierbar» until the car is bought
again. Before KAN-146 nothing emitted or consumed the event: a reversed
purchase would have stayed invoiceable in Sales. On Postgres, over HTTP where
the route is what is under test.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.core.reconciliation import ReconciliationAlarm
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_ledger import LedgerCategory, LedgerDirection, StockItemLedger
from app.inventory.schemas.purchase import PurchaseStornoRequest
from app.inventory.services.purchase import storno_purchase
from app.sales import reconciliation as sales_reconciliation
from app.sales.consumers import handle_stock_item_purchased_message, handle_stock_item_storno_message
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.services.contract import cancel_contract, confirm_contract, request_invoice
from tests.sales_contract_projection_support import feed_projection, worker_applies_after_the_first_read
from tests.test_inventory_purchase_promotes import VIN, _auth, _create, _events, _purchase, _token
from tests.test_sales_invoiceable_replica import (
    _contract_on,
    _dealership,
    _session_factory,
    _stock_item,
)
from tests.test_sales_invoiceable_replica import (
    _purchase as _book_purchase,
)


def _storno(client, token: str, item: dict, reason: str = "Lieferant hat den Kauf rückgängig gemacht."):
    return client.post(
        f"/v1/inventory/stock-items/{item['id']}/purchase/storno",
        json={"reason": reason},
        headers=_auth(token, **{"If-Match": str(item["version"])}),
    )


def _ledger(db_session, item_id: str) -> list[StockItemLedger]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(StockItemLedger)
            .where(StockItemLedger.stock_item_id == uuid.UUID(item_id))
            .order_by(StockItemLedger.created_at, StockItemLedger.id)
        )
    )


def _audit_actions(db_session, item_id: str) -> list[str]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.entity_id == uuid.UUID(item_id))
            .order_by(AuditEvent.id)
        )
    )


def _bought(client, db_session, token: str) -> dict:
    item = _create(client, db_session, token)
    response = _purchase(client, token, item, vin=VIN)
    assert response.status_code == 200, response.text
    return response.json()


# --- the booking: one Einstandspreis, audited, never booked twice -------------


def test_booking_a_purchase_posts_the_einstandspreis_and_is_audited(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)

    entries = _ledger(db_session, item["id"])
    assert [(e.category, e.amount, e.is_auto) for e in entries] == [
        (LedgerCategory.EINSTANDSPREIS, Decimal("8000.00"), True)
    ]
    assert entries[0].source_ref.startswith("purchase:")
    # Dated the purchase date's start in Switzerland (2026-10-30 00:00 CET).
    assert entries[0].occurred_at == dt.datetime(2026, 10, 29, 23, 0, tzinfo=dt.UTC)
    assert "purchase_booked" in _audit_actions(db_session, item["id"])


def test_a_second_booking_is_refused_until_the_purchase_is_stornoed(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)

    response = _purchase(client, token, item, purchasePrice="9000.00")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "purchase_already_booked", "stockItemId": item["id"]}
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).purchase_price == Decimal("8000.00")
    assert len(_ledger(db_session, item["id"])) == 1


# --- the storno ---------------------------------------------------------------


def test_storno_reverses_the_purchase_returns_the_car_to_pipeline_and_publishes_the_event(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)

    response = _storno(client, token, item)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lifecycleStatus"] == "pipeline"
    assert body["isInvoiceable"] is False
    assert body["purchasePrice"] is None
    assert body["supplierName"] is None
    assert body["inStockAt"] is None
    # Anto, 2026-11-05: the stornoed car keeps its VIN and its vehicle-mdm record.
    assert body["vin"] == VIN
    assert body["vehicleId"] == item["vehicleId"]
    assert body["version"] == item["version"] + 1

    entries = _ledger(db_session, item["id"])
    assert [(e.category, e.amount, e.is_auto) for e in entries] == [
        (LedgerCategory.EINSTANDSPREIS, Decimal("8000.00"), True),
        (LedgerCategory.EINSTANDSPREIS, Decimal("-8000.00"), True),
    ]
    assert entries[1].source_ref == f"storno:{entries[0].id}"

    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(item["id"]), AuditEvent.action == "purchase_storno")
    ).one()
    assert audit.reason == "Lieferant hat den Kauf rückgängig gemacht."
    assert audit.before["purchasePrice"] == "8000.00"
    assert audit.after["purchasePrice"] is None
    assert _events(db_session, item["id"]).count("inventory.stock_item.storno") == 1


def test_purchase_storno_purchase_ends_bought_again(client, db_session):
    """Exit criterion 3, in Stock: booked again without a new VIN (Anto,
    2026-11-05), the car is back in stock and invoiceable, with one live
    Einstandspreis in its book."""

    token = _token(client)
    item = _bought(client, db_session, token)
    stornoed = _storno(client, token, item).json()

    again = _purchase(client, token, stornoed, purchasePrice="7500.00")

    assert again.status_code == 200, again.text
    body = again.json()
    assert body["lifecycleStatus"] == "in_stock"
    assert body["isInvoiceable"] is True
    assert body["vin"] == VIN
    assert body["inStockAt"] is not None
    assert sum(e.amount for e in _ledger(db_session, item["id"])) == Decimal("7500.00")
    events = _events(db_session, item["id"])
    assert events.count("inventory.stock_item.purchased") == 2
    # Nothing promoted twice: the car kept its vehicle-mdm record.
    assert events.count("inventory.pipeline_vehicle.vin_assigned") == 1


def test_a_stornoed_car_is_booked_again_only_under_its_own_vin(client, db_session):
    token = _token(client)
    stornoed = _storno(client, token, _bought(client, db_session, token)).json()

    response = _purchase(client, token, stornoed, vin="WVWZZZ1KZAW004479")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "purchase_vin_mismatch"


def test_a_stornoed_car_still_blocks_its_vin_in_stock(client, db_session):
    """FR-I-12: a car whose purchase is cancelled is still the dealership's car."""

    token = _token(client)
    stornoed = _storno(client, token, _bought(client, db_session, token)).json()
    other = _create(client, db_session, token)

    response = _purchase(client, token, other, vin=VIN)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_already_in_stock"
    assert response.json()["error"]["details"]["stockItemId"] == stornoed["id"]


def test_storno_without_a_booked_purchase_is_refused(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token)

    response = _storno(client, token, item)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "purchase_not_booked", "stockItemId": item["id"]}


def test_storno_of_a_car_that_left_stock_is_refused(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    stored.left_stock_at = utcnow()
    db_session.commit()

    response = _storno(client, token, item)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "purchase_storno_left_stock", "stockItemId": item["id"]}
    assert _events(db_session, item["id"]).count("inventory.stock_item.storno") == 0


@pytest.mark.parametrize("reason", ["", "   "])
def test_storno_needs_a_reason(client, db_session, reason):
    token = _token(client)
    item = _bought(client, db_session, token)

    assert _storno(client, token, item, reason=reason).status_code == 422


def test_storno_with_a_stale_version_is_refused(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)

    response = _storno(client, token, {**item, "version": item["version"] - 1})

    assert response.status_code == 409, response.text
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).purchase_price == Decimal("8000.00")


def test_a_sales_user_may_not_storno(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    sales = create_access_token(
        user_id=uuid.uuid4(), tenant_id=stored.tenant_id, group_id=uuid.uuid4(), roles=frozenset({AccessRole.SALES})
    )

    assert _storno(client, sales, item).status_code == 403


def test_storno_replays_on_the_same_idempotency_key(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)
    headers = _auth(token, **{"If-Match": str(item["version"]), "Idempotency-Key": "storno-once"})
    url = f"/v1/inventory/stock-items/{item['id']}/purchase/storno"

    first = client.post(url, json={"reason": "Doppelt erfasst."}, headers=headers)
    second = client.post(url, json={"reason": "Doppelt erfasst."}, headers=headers)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert _events(db_session, item["id"]).count("inventory.stock_item.storno") == 1
    assert len(_ledger(db_session, item["id"])) == 2


# --- the manual Wagenbuch path cannot book what the purchase posts -------------


def test_the_einstandspreis_is_never_booked_by_hand(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)

    response = client.post(
        f"/v1/inventory/stock-items/{item['id']}/ledger-entries",
        json={"category": "einstandspreis", "amount": "8000.00", "occurredAt": "2026-10-30T10:00:00Z", "sourceRef": str(uuid.uuid4())},
        headers=_auth(token),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "category_automatic_only"


@pytest.mark.parametrize("prefix", ["purchase:", "storno:"])
def test_a_hand_booking_cannot_take_the_purchase_keys(client, db_session, prefix):
    """The counter-entry is keyed `storno:<the entry's id>`; a hand booking
    that took that key first would make the storno's reversal collide."""

    token = _token(client)
    item = _bought(client, db_session, token)
    purchase_entry = _ledger(db_session, item["id"])[0]

    response = client.post(
        f"/v1/inventory/stock-items/{item['id']}/ledger-entries",
        json={"category": "aufbereitung", "amount": "250.00", "occurredAt": "2026-10-30T10:00:00Z",
              "sourceRef": f"{prefix}{purchase_entry.id}"},
        headers=_auth(token),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "source_ref_reserved"


# --- Sales: the replica follows the storno (exit criteria 2 and 3) -------------


def _message(db_session, event_type: str, item_id: uuid.UUID, nth: int = 0) -> OutboxMessage:
    return list(
        db_session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == item_id)
            .order_by(OutboxMessage.id)
        )
    )[nth]


def _deliver(db_session, message: OutboxMessage) -> bool:
    consumers = {
        "inventory.stock_item.purchased": ("sales.stock_item_purchased", handle_stock_item_purchased_message),
        "inventory.stock_item.storno": ("sales.stock_item_storno", handle_stock_item_storno_message),
    }
    name, handler = consumers[message.event_type]
    return consume_once(db_session, message=message, consumer_name=name, handler=handler)


def _storno_item(db_session, item) -> None:
    db_session.refresh(item)
    storno_purchase(db_session, item=item, data=PurchaseStornoRequest(reason="Kauf rückgängig."), actor_id=uuid.uuid4())


def _signed_contract_on_bought_car(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    _book_purchase(db_session, item)
    _deliver(db_session, _message(db_session, "inventory.stock_item.purchased", item.id))
    db_session.refresh(contract)
    assert contract.is_invoiceable is True
    return item, contract


def test_a_storno_makes_the_contract_not_invoiceable_and_keeps_its_reservation(db_session, engine):
    """Anto, 2026-11-05: a reserved car may be stornoed; the reservation stays
    and Sales derives «nicht fakturierbar» from the storno event."""

    item, contract = _signed_contract_on_bought_car(db_session, engine)
    db_session.refresh(item)
    assert item.reservation_state.value == "reserved"

    _storno_item(db_session, item)
    assert _deliver(db_session, _message(db_session, "inventory.stock_item.storno", item.id)) is True

    db_session.refresh(contract)
    db_session.refresh(item)
    assert contract.is_invoiceable is False
    assert item.reservation_state.value == "reserved"
    assert item.reserved_by_contract_id == contract.id
    assert db_session.scalar(select(SalesStockItemPurchase).where(SalesStockItemPurchase.stock_item_id == item.id)) is None
    with pytest.raises(ConflictError) as excinfo:
        request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=uuid.uuid4())
    assert excinfo.value.details == {"reason": "vehicle_not_purchased"}


def test_purchase_storno_purchase_ends_invoiceable_in_sales(db_session, engine):
    item, contract = _signed_contract_on_bought_car(db_session, engine)
    _storno_item(db_session, item)
    _deliver(db_session, _message(db_session, "inventory.stock_item.storno", item.id))
    db_session.refresh(item)
    _book_purchase(db_session, item)

    _deliver(db_session, _message(db_session, "inventory.stock_item.purchased", item.id, nth=1))

    db_session.refresh(contract)
    assert contract.is_invoiceable is True


def test_a_storno_delivered_after_the_next_purchase_leaves_the_car_invoiceable(db_session, engine):
    """At-least-once, unordered after a failure: P1, then the storno's first
    delivery fails, P2 arrives, then the storno. Stock says bought; so must
    Sales."""

    item, contract = _signed_contract_on_bought_car(db_session, engine)
    _storno_item(db_session, item)
    db_session.refresh(item)
    _book_purchase(db_session, item)

    _deliver(db_session, _message(db_session, "inventory.stock_item.purchased", item.id, nth=1))
    _deliver(db_session, _message(db_session, "inventory.stock_item.storno", item.id))

    db_session.refresh(contract)
    assert contract.is_invoiceable is True


def _refused_as_invoiced(db_session, item, contract) -> None:
    db_session.refresh(item)
    with pytest.raises(ConflictError) as excinfo:
        storno_purchase(db_session, item=item, data=PurchaseStornoRequest(reason="Zu spät."), actor_id=uuid.uuid4())

    assert excinfo.value.details == {
        "reason": "purchase_storno_invoice_requested", "contractNumber": contract.contract_number, "stockItemId": str(item.id),
    }
    db_session.rollback()
    db_session.refresh(item)
    assert item.purchase_price is not None
    assert item.is_invoiceable is True


def test_storno_after_the_invoice_was_requested_is_refused(db_session, engine):
    """«Rechnung anfordern» handed the sale to Finance: for the business the
    car is invoiced, so its purchase is no longer stornoed. KAN-630: Stock
    answers from its projection, fed here as the worker feeds it — the fact
    is published and applied, so only the projection can refuse."""

    item, contract = _signed_contract_on_bought_car(db_session, engine)
    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=uuid.uuid4())
    assert feed_projection(db_session) > 0

    _refused_as_invoiced(db_session, item, contract)


def test_storno_while_the_invoice_request_is_still_in_the_outbox_is_refused(db_session, engine):
    """KAN-630's timing window: Sales committed the request (and its fact)
    but Stock's consumer has not applied it yet. The projection alone would
    let the storno through; the fact in flight refuses it, as the direct
    read of Sales did."""

    item, contract = _signed_contract_on_bought_car(db_session, engine)
    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=uuid.uuid4())

    _refused_as_invoiced(db_session, item, contract)


def test_storno_while_the_invoice_request_is_applied_between_the_two_reads_is_refused(db_session, engine):
    """KAN-630 review: the worker applies the request (projection row and
    processed-event row in one commit) after the storno's first read of the
    projection or the outbox. Projection first, outbox second hides the
    committed request from both reads; outbox first cannot."""

    item, contract = _signed_contract_on_bought_car(db_session, engine)
    feed_projection(db_session)
    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=uuid.uuid4())

    with worker_applies_after_the_first_read(engine) as applied:
        _refused_as_invoiced(db_session, item, contract)

    assert applied == [1], "the worker applied the invoice request while the storno read"


def test_storno_after_the_contract_with_the_request_was_cancelled_goes_through(db_session, engine):
    """A cancelled contract does not hold the car as invoiced (the gate's
    rule, KAN-146): once the projection knows it is cancelled, the storno is
    allowed again."""

    item, contract = _signed_contract_on_bought_car(db_session, engine)
    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=uuid.uuid4())
    db_session.refresh(contract)
    cancel_contract(
        db_session, contract=contract, reason="Kunde tritt zurück.", actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    feed_projection(db_session)

    _storno_item(db_session, item)

    db_session.refresh(item)
    assert item.purchase_price is None


# --- reconciliation: the storno's outbox lag is not a finding (KAN-145 comment) -


def test_reconciliation_leaves_a_replica_whose_storno_is_within_outbox_lag_alone(db_session, engine):
    item, _contract = _signed_contract_on_bought_car(db_session, engine)
    _storno_item(db_session, item)  # not delivered yet: the replica row still stands

    labels = _finding_labels(db_session)

    assert "purchase replica whose stock item is not purchased in Stock for its dealership" not in labels


def test_reconciliation_reports_a_replica_whose_storno_is_older_than_the_grace(db_session, engine):
    item, _contract = _signed_contract_on_bought_car(db_session, engine)
    _storno_item(db_session, item)
    storno = _message(db_session, "inventory.stock_item.storno", item.id)
    storno.occurred_at = utcnow() - dt.timedelta(hours=2)
    db_session.commit()

    labels = _finding_labels(db_session)

    assert "purchase replica whose stock item is not purchased in Stock for its dealership" in labels


def _finding_labels(db_session) -> set[str]:
    try:
        sales_reconciliation.run(db_session)
    except ReconciliationAlarm as alarm:
        return {orphan.check_label for orphan in alarm.orphans}
    return set()



def test_a_hand_booked_einstandspreis_from_before_kan_146_is_reversed_too(client, db_session):
    """Until KAN-146 the purchase posted nothing and the Einstandspreis was
    booked by hand. A storno leaves the car's Einstandspreis at zero either
    way (FR-I-05)."""

    token = _token(client)
    item = _bought(client, db_session, token)
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    db_session.add(
        StockItemLedger(
            tenant_id=stored.tenant_id, stock_item_id=stored.id, category=LedgerCategory.EINSTANDSPREIS,
            direction=LedgerDirection.COST, amount=Decimal("8000.00"),
            occurred_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC), source_ref=str(uuid.uuid4()), is_auto=False,
        )
    )
    db_session.commit()

    assert _storno(client, token, item).status_code == 200

    entries = _ledger(db_session, item["id"])
    assert sum(e.amount for e in entries if e.category == LedgerCategory.EINSTANDSPREIS) == Decimal("0.00")
    reversed_ids = {e.source_ref.removeprefix("storno:") for e in entries if e.source_ref.startswith("storno:")}
    assert reversed_ids == {str(e.id) for e in entries if not e.source_ref.startswith("storno:")}


def test_storno_of_another_dealerships_car_is_404(client, db_session):
    token = _token(client)
    item = _bought(client, db_session, token)
    other = _token(client)

    response = _storno(client, other, item)

    assert response.status_code == 404, response.text
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).purchase_price == Decimal("8000.00")


def test_reconciliation_reports_an_invoice_requested_for_a_car_stock_does_not_hold_as_purchased(db_session, engine):
    """The race the storno's refusal cannot close (request_invoice reads the
    replica before the storno is consumed): monitored, rule 10."""

    item, contract = _signed_contract_on_bought_car(db_session, engine)
    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=uuid.uuid4())
    label = "contract whose invoice was requested for a stock item Stock does not hold as purchased"
    assert label not in _finding_labels(db_session)

    db_session.refresh(item)
    item.is_invoiceable = False  # what a storno that won the race leaves behind
    db_session.commit()

    assert label in _finding_labels(db_session)
