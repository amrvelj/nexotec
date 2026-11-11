"""KAN-553 (ADR-115; Anto's ruling on KAN-33, 2026-11-03) — «Fahrzeug im
Lager geändert»: the offer's and the contract's snapshot stays frozen
(ADR-041); Stock publishes `inventory.stock_item.commercial_changed`, Sales
flags every draft or open offer and every pending contract on that car, and
the seller refreshes or dismisses it.

Exit criterion 1: a price change flags the open offer and the pending
contract only; a confirmed contract is untouched; refresh re-freezes and
re-prices with a new version; dismiss clears; a duplicate delivery is a
no-op (also `test_consumer_redelivery_idempotency.py`); reconciliation
reports a planted mismatch.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.core.reconciliation import find_rows_in_state
from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.pricing import OptionInput
from app.inventory.schemas.stock_item import StockItemUpdate
from app.inventory.services.commercial_change import EVENT_TYPE
from app.inventory.services.pricing import set_options
from app.inventory.services.stock_item import update_stock_item
from app.sales import reconciliation
from app.sales.consumers import handle_stock_item_commercial_changed_message
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.schemas.contract import ContractUpdate
from app.sales.services.contract import create_contract, update_contract
from app.sales.services.vehicle_change import dismiss_vehicle_change, refresh_vehicle
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_offer_refinements import _offer_with_vehicle, _stock_item
from tests.token_support import dealer_group_of


def _events(db_session, item_id) -> list[OutboxMessage]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == EVENT_TYPE, OutboxMessage.aggregate_id == item_id)
            .order_by(OutboxMessage.occurred_at)
        ).all()
    )


def _reprice(db_session, item, base: str = "36000.00"):
    return set_options(
        db_session,
        item=item,
        base_price=Decimal(base),
        options=[OptionInput(code="LED", label="LED-Scheinwerfer", price=Decimal("800.00"))],
        actor_id=uuid.uuid4(),
    )


def _deliver_last(db_session, item_id) -> None:
    handle_stock_item_commercial_changed_message(db_session, _events(db_session, item_id)[-1])
    db_session.commit()


def _pending_contract_on(db_session, dealership_id, item) -> SalesContract:
    contract = create_contract(
        db_session, tenant_id=dealership_id, offer=None, actor_id=uuid.uuid4(), stock_item_id=item.id,
        vehicle_label=item.vehicle_label, group_id=uuid.uuid4(),
    )
    return contract


# --- inventory publishes the fact --------------------------------------------------------


def test_a_price_change_in_the_pricing_dialog_publishes_commercial_changed(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)  # 38'000 + LED 800
    before = len(_events(db_session, item.id))

    _reprice(db_session, item, "36000.00")

    events = _events(db_session, item.id)
    assert len(events) == before + 1
    payload = events[-1].payload
    assert payload["changedFields"] == ["listPrice"]
    assert payload["prices"] == {"listPrice": {"old": "38800.00", "new": "36800.00"}}
    assert payload["stockNumber"] == item.stock_number


def test_an_edit_of_colour_odometer_and_effective_price_publishes_the_changed_fields(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    before = len(_events(db_session, item.id))

    update_stock_item(
        db_session,
        item=item,
        data=StockItemUpdate(effective_price=Decimal("37500.00"), exterior_colour="Tornadorot", odometer_km=1200),
        actor_id=uuid.uuid4(),
    )

    events = _events(db_session, item.id)
    assert len(events) == before + 1
    assert events[-1].payload["changedFields"] == ["effectivePrice", "exteriorColour", "odometerKm"]
    assert events[-1].payload["prices"] == {"effectivePrice": {"old": None, "new": "37500.00"}}


def test_an_edit_that_changes_nothing_commercial_publishes_nothing(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    before = len(_events(db_session, item.id))

    update_stock_item(db_session, item=item, data=StockItemUpdate(body_style="limousine"), actor_id=uuid.uuid4())
    _reprice(db_session, item, "38000.00")  # the same price and options again

    assert len(_events(db_session, item.id)) == before


def test_a_purchase_booking_publishes_no_commercial_change(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)  # set_options, then record_purchase
    events = _events(db_session, item.id)
    # One from the first pricing (no price → 38'800), none from the booking.
    assert [e.payload["changedFields"] for e in events] == [["listPrice", "options"]]


# --- Sales flags the open deals ---------------------------------------------------------


def test_a_price_change_flags_the_open_offer_and_the_pending_contract_only(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    contract = _pending_contract_on(db_session, dealership.id, item)
    confirmed = _pending_contract_on(db_session, dealership.id, item)
    db_session.execute(
        update(SalesContract).where(SalesContract.id == confirmed.id).values(status=ContractStatus.CONFIRMED)
    )
    cancelled = _offer_with_vehicle(db_session, dealership.id, item)
    db_session.execute(update(SalesOffer).where(SalesOffer.id == cancelled.id).values(status=OfferStatus.CANCELLED))
    db_session.commit()
    versions = {offer.id: offer.version, contract.id: contract.version}

    _reprice(db_session, item, "36000.00")
    _deliver_last(db_session, item.id)

    db_session.expire_all()
    for deal in (db_session.get(SalesOffer, offer.id), db_session.get(SalesContract, contract.id)):
        assert deal.vehicle_changed_at is not None
        assert deal.vehicle_changed_fields == ["listPrice"]
        # A flag is not an edit: an open workspace's next save keeps its version.
        assert deal.version == versions[deal.id]
        # The snapshot stays frozen (ADR-041).
        assert deal.vehicle_snapshot["listPrice"] == "38800.00"
    assert db_session.get(SalesContract, confirmed.id).vehicle_changed_at is None
    assert db_session.get(SalesOffer, cancelled.id).vehicle_changed_at is None
    assert db_session.get(SalesDeal, offer.id).vehicle_changed_at is not None
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == offer.id, AuditEvent.action == "vehicle_changed_in_stock")
    ).all()
    assert len(audit) == 1 and audit[0].actor_id is None


def test_a_second_change_merges_the_fields(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    _reprice(db_session, item, "36000.00")
    _deliver_last(db_session, item.id)
    update_stock_item(db_session, item=item, data=StockItemUpdate(exterior_colour="Schwarz"), actor_id=uuid.uuid4())
    _deliver_last(db_session, item.id)

    db_session.expire_all()
    assert db_session.get(SalesOffer, offer.id).vehicle_changed_fields == ["listPrice", "exteriorColour"]


def test_a_change_older_than_the_deals_freeze_flags_nothing(db_session):
    """Delivery is unordered: a deal priced after the change already has it."""

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    _reprice(db_session, item, "36000.00")
    offer = _offer_with_vehicle(db_session, dealership.id, item)  # frozen after the change

    _deliver_last(db_session, item.id)

    db_session.expire_all()
    assert db_session.get(SalesOffer, offer.id).vehicle_changed_at is None


# --- refresh and dismiss ----------------------------------------------------------------


def _flagged_offer(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    assert offer.gross_price == Decimal("38800.00")
    _reprice(db_session, item, "36000.00")
    _deliver_last(db_session, item.id)
    db_session.expire_all()
    return dealership, item, db_session.get(SalesOffer, offer.id)


def test_refresh_re_freezes_and_re_prices_with_a_new_version(db_session):
    _, _, offer = _flagged_offer(db_session)
    version = offer.version

    offer = refresh_vehicle(db_session, deal=offer, version_seen=version, actor_id=uuid.uuid4())

    assert offer.vehicle_snapshot["listPrice"] == "36800.00"
    assert offer.gross_price == Decimal("36800.00")
    assert offer.version == version + 1
    assert offer.vehicle_changed_at is None and offer.vehicle_changed_fields is None
    assert db_session.get(SalesDeal, offer.id).vehicle_changed_at is None
    assert db_session.get(SalesDeal, offer.id).gross_price == Decimal("36800.00")
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == offer.id, AuditEvent.action == "vehicle_refreshed")
    ).one()
    assert audit.before["grossPrice"] == "38800.00" and audit.after["grossPrice"] == "36800.00"


def test_refresh_keeps_the_sellers_deselection_of_a_factory_option(db_session):
    from app.sales.schemas.line_item import LineItemFactoryOptionPatch, LineItemsReplaceRequest
    from app.sales.services.line_items import list_line_items, replace_line_items

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    led = next(row for row in list_line_items(db_session, deal=offer) if row.code == "LED")
    offer = replace_line_items(
        db_session,
        deal=offer,
        data=LineItemsReplaceRequest(factory_options=[LineItemFactoryOptionPatch(id=led.id, included=False)]),
        actor_id=uuid.uuid4(),
    )
    _reprice(db_session, item, "36000.00")
    _deliver_last(db_session, item.id)
    db_session.expire_all()
    offer = db_session.get(SalesOffer, offer.id)

    offer = refresh_vehicle(db_session, deal=offer, version_seen=offer.version, actor_id=uuid.uuid4())

    assert [row.included for row in list_line_items(db_session, deal=offer) if row.code == "LED"] == [False]
    assert offer.gross_price == Decimal("36000.00")


def test_dismiss_clears_the_flag_and_keeps_the_frozen_price(db_session):
    _, _, offer = _flagged_offer(db_session)
    version = offer.version

    offer = dismiss_vehicle_change(db_session, deal=offer, version_seen=version, actor_id=uuid.uuid4())

    assert offer.vehicle_changed_at is None
    assert offer.vehicle_snapshot["listPrice"] == "38800.00"
    assert offer.gross_price == Decimal("38800.00")
    assert offer.version == version + 1


def test_refresh_and_dismiss_need_a_flag_and_an_editable_deal(db_session):
    _, _, offer = _flagged_offer(db_session)
    db_session.execute(update(SalesOffer).where(SalesOffer.id == offer.id).values(status=OfferStatus.OPEN))
    db_session.commit()
    db_session.expire_all()
    sent = db_session.get(SalesOffer, offer.id)
    with pytest.raises(ConflictError):
        refresh_vehicle(db_session, deal=sent, version_seen=sent.version, actor_id=uuid.uuid4())
    db_session.rollback()

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    unflagged = _offer_with_vehicle(db_session, dealership.id, item)
    with pytest.raises(ConflictError) as refused:
        dismiss_vehicle_change(db_session, deal=unflagged, version_seen=unflagged.version, actor_id=uuid.uuid4())
    assert refused.value.details["reason"] == "vehicle_not_changed"


def test_a_pending_contract_refreshes_through_its_own_route(client, db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    contract = _pending_contract_on(db_session, dealership.id, item)
    _reprice(db_session, item, "36000.00")
    _deliver_last(db_session, item.id)
    db_session.expire_all()
    contract = db_session.get(SalesContract, contract.id)
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealer_group_of(str(dealership.id)),
        roles=frozenset({AccessRole.SALES}), entitlements=frozenset({"sales"}),
    )
    headers = {"Authorization": f"Bearer {token}"}

    read = client.get(f"/v1/sales/contracts/{contract.id}", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["vehicleChangedFields"] == ["listPrice"]

    stale = client.post(
        f"/v1/sales/contracts/{contract.id}/vehicle-refresh", headers={**headers, "If-Match": str(contract.version - 1)}
    )
    assert stale.status_code == 409

    response = client.post(
        f"/v1/sales/contracts/{contract.id}/vehicle-refresh", headers={**headers, "If-Match": str(contract.version)}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["vehicleChangedAt"] is None
    assert body["grossPrice"] == "36800.00"
    assert body["version"] == contract.version + 1

    again = client.post(
        f"/v1/sales/contracts/{contract.id}/vehicle-refresh/dismiss",
        headers={**headers, "If-Match": str(body["version"])},
    )
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "vehicle_not_changed"


def test_the_deal_grid_filters_by_the_flag(client, db_session):
    dealership, _item, offer = _flagged_offer(db_session)
    other_item = _stock_item(db_session, dealership.id)
    other = _offer_with_vehicle(db_session, dealership.id, other_item)
    for o in (offer, other):
        db_session.execute(update(SalesOffer).where(SalesOffer.id == o.id).values(status=OfferStatus.OPEN))
        db_session.execute(update(SalesDeal).where(SalesDeal.id == o.id).values(status="open"))
    db_session.commit()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealer_group_of(str(dealership.id)),
        roles=frozenset({AccessRole.SALES}), entitlements=frozenset({"sales"}),
    )
    headers = {"Authorization": f"Bearer {token}"}

    flagged = client.get("/v1/sales/deals?vehicleChanged=true", headers=headers)
    assert flagged.status_code == 200, flagged.text
    assert [row["id"] for row in flagged.json()["items"]] == [str(offer.id)]
    assert flagged.json()["items"][0]["vehicleChangedAt"] is not None
    rest = client.get("/v1/sales/deals?vehicleChanged=false", headers=headers)
    assert [row["id"] for row in rest.json()["items"]] == [str(other.id)]


# --- reconciliation (rule 10) -----------------------------------------------------------


def _check(deal_type):
    return next(
        c
        for c in reconciliation.CHECKS
        if getattr(c, "label", "").startswith(f"{deal_type.__tablename__} on a stock car whose price changed")
    )


def test_reconciliation_reports_a_price_moved_without_the_flag(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    contract = _pending_contract_on(db_session, dealership.id, item)
    # Planted: Stock's price moved two hours ago and the event never arrived.
    two_hours_ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    db_session.execute(
        update(StockItem)
        .where(StockItem.id == item.id)
        .values(list_price=Decimal("35000.00"), updated_at=two_hours_ago)
    )
    for model, deal in ((SalesOffer, offer), (SalesContract, contract)):
        db_session.execute(
            update(model)
            .where(model.id == deal.id)
            .values(vehicle_snapshot_frozen_at=two_hours_ago - dt.timedelta(hours=1))
        )
    db_session.commit()

    assert [row for row, _ in find_rows_in_state(db_session, _check(SalesOffer))] == [offer.id]
    assert [row for row, _ in find_rows_in_state(db_session, _check(SalesContract))] == [contract.id]

    # Flagged (the event arrived): no finding.
    db_session.execute(
        update(SalesOffer).where(SalesOffer.id == offer.id).values(vehicle_changed_at=two_hours_ago)
    )
    db_session.commit()
    assert find_rows_in_state(db_session, _check(SalesOffer)) == []


def test_reconciliation_is_quiet_while_the_price_matches(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    _offer_with_vehicle(db_session, dealership.id, item)
    assert find_rows_in_state(db_session, _check(SalesOffer)) == []


def test_a_contract_edit_does_not_lose_the_flag(db_session):
    """The flag says the stock car changed; an unrelated edit of the deal
    (a discount) is not a refresh and keeps it."""

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    contract = _pending_contract_on(db_session, dealership.id, item)
    _reprice(db_session, item, "36000.00")
    _deliver_last(db_session, item.id)
    db_session.expire_all()
    contract = db_session.get(SalesContract, contract.id)

    contract = update_contract(
        db_session, contract=contract, group_id=uuid.uuid4(),
        data=ContractUpdate(discount_type="amount", discount_value=Decimal("100.00")), actor_id=uuid.uuid4(),
    )

    assert contract.vehicle_changed_fields == ["listPrice"]


def test_a_contract_from_a_flagged_offer_carries_the_flag(db_session):
    dealership, _item, offer = _flagged_offer(db_session)

    contract = create_contract(
        db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4(), group_id=uuid.uuid4()
    )

    assert contract.vehicle_changed_fields == ["listPrice"]
    assert contract.vehicle_changed_at == offer.vehicle_changed_at


# --- review follow-ups ------------------------------------------------------------------


def test_a_sent_offer_is_flagged_too(db_session):
    from app.sales.services.offer import finalize_offer

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = finalize_offer(db_session, offer=_offer_with_vehicle(db_session, dealership.id, item), actor_id=uuid.uuid4())
    assert offer.status == OfferStatus.OPEN

    _reprice(db_session, item, "36000.00")
    _deliver_last(db_session, item.id)

    db_session.expire_all()
    assert db_session.get(SalesOffer, offer.id).vehicle_changed_fields == ["listPrice"]


def test_a_dismissed_change_is_no_reconciliation_finding(db_session):
    _dealership, item, offer = _flagged_offer(db_session)
    offer = dismiss_vehicle_change(db_session, deal=offer, version_seen=offer.version, actor_id=uuid.uuid4())
    assert offer.vehicle_change_dismissed_prices == {"listPrice": "36800.00", "effectivePrice": None}
    # Afterwards, a write to the car that changes no price (it moves
    # `updated_at`), and all of it over an hour back.
    update_stock_item(db_session, item=db_session.get(StockItem, item.id), data=StockItemUpdate(body_style="kombi"), actor_id=uuid.uuid4())
    two_hours_ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    db_session.execute(update(StockItem).where(StockItem.id == item.id).values(updated_at=two_hours_ago))
    db_session.execute(
        update(SalesOffer)
        .where(SalesOffer.id == offer.id)
        .values(vehicle_snapshot_frozen_at=two_hours_ago - dt.timedelta(hours=1))
    )
    db_session.commit()

    assert find_rows_in_state(db_session, _check(SalesOffer)) == []

    # A price that moves away from what the seller accepted is reported
    # again when its event is lost.
    db_session.execute(
        update(StockItem).where(StockItem.id == item.id).values(list_price=Decimal("30000.00"), updated_at=two_hours_ago)
    )
    db_session.commit()
    assert [row for row, _ in find_rows_in_state(db_session, _check(SalesOffer))] == [offer.id]


def test_another_car_on_the_deal_drops_the_flag(db_session):
    from app.sales.schemas.offer import OfferUpdate
    from app.sales.services.offer import update_offer

    dealership, _item, offer = _flagged_offer(db_session)
    other = _stock_item(db_session, dealership.id)

    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=other.id, vehicle_label=other.vehicle_label),
        actor_id=uuid.uuid4(),
    )

    assert offer.vehicle_changed_at is None and offer.vehicle_changed_fields is None
    assert db_session.get(SalesDeal, offer.id).vehicle_changed_at is None


def test_the_backfill_flags_deals_priced_before_kan553_and_only_them(db_session):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "backfill_vehicle_changed_flags", Path(__file__).resolve().parents[1] / "scripts" / "backfill_vehicle_changed_flags.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    stale = _offer_with_vehicle(db_session, dealership.id, item)
    current_item = _stock_item(db_session, dealership.id)
    current = _offer_with_vehicle(db_session, dealership.id, current_item)
    # Before KAN-553: the price moved and nothing was published.
    db_session.execute(update(StockItem).where(StockItem.id == item.id).values(list_price=Decimal("35000.00")))
    db_session.commit()

    dry = script.backfill(db_session, commit=False)
    assert [(kind, row) for kind, row, _ in dry.flagged] == [("offer", stale.id)]
    db_session.expire_all()
    assert db_session.get(SalesOffer, stale.id).vehicle_changed_at is None

    done = script.backfill(db_session, commit=True)
    assert [fields for _, _, fields in done.flagged] == [["listPrice"]]
    db_session.expire_all()
    assert db_session.get(SalesOffer, stale.id).vehicle_changed_fields == ["listPrice"]
    assert db_session.get(SalesOffer, current.id).vehicle_changed_at is None
    assert script.backfill(db_session, commit=True).flagged == []
