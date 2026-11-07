"""KAN-570 (Anto, 2026-11-05, option A): the stock number an offer or a
contract carries since KAN-157 (`stock_item_label`, rule 2's label) is on
their reads (`OfferRead`/`ContractRead.stockItemLabel`, additive), on the
Sales grid's row (`sales_deal.stock_item_label`, written by the one
projection writer from the live entity, sortable), compared by the nightly
deal-row StateChecks, and filled for rows written before by
`scripts/backfill_sales_deal_projection.py`. Postgres lane.
"""

import uuid

from sqlalchemy import update

from app.core.auth import AccessRole, create_access_token
from app.core.reconciliation import find_rows_in_state
from app.sales.models.deal import SalesDeal
from app.sales.reconciliation import deal_row_checks
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract
from app.sales.services.offer import update_offer
from scripts.backfill_sales_deal_projection import reproject
from tests.test_sales_offer_refinements import _make_dealership, _offer_with_vehicle, _stock_item


def _headers(dealership) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealership.dealer_group_id,
        roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def _contract_from_car(db_session, dealership, item):
    return create_contract(
        db_session, tenant_id=dealership.id, offer=None, actor_id=uuid.uuid4(), stock_item_id=item.id,
        vehicle_label=item.vehicle_label,
    )


def _deal_row(db_session, deal_id: uuid.UUID) -> SalesDeal:
    db_session.expire_all()
    row = db_session.get(SalesDeal, deal_id)
    assert row is not None
    return row


def _findings(db_session) -> set[uuid.UUID]:
    db_session.expire_all()
    return {row for check in deal_row_checks() for row, _ in find_rows_in_state(db_session, check)}


# --- 1. the reads carry it ----------------------------------------------------


def test_the_offer_and_contract_reads_carry_the_stock_number(client, db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    contract = _contract_from_car(db_session, dealership, _stock_item(db_session, dealership.id, with_option=False))
    headers = _headers(dealership)

    offer_read = client.get(f"/v1/sales/offers/{offer.id}", headers=headers)
    contract_read = client.get(f"/v1/sales/contracts/{contract.id}", headers=headers)

    assert offer_read.status_code == 200, offer_read.text
    assert offer_read.json()["stockItemLabel"] == item.stock_number
    assert contract_read.status_code == 200, contract_read.text
    assert contract.stock_item_label is not None
    assert contract_read.json()["stockItemLabel"] == contract.stock_item_label


def test_a_deal_without_a_stock_car_reads_null(client, db_session):
    dealership = _make_dealership(db_session)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=None, actor_id=uuid.uuid4())

    read = client.get(f"/v1/sales/contracts/{contract.id}", headers=_headers(dealership))

    assert read.status_code == 200, read.text
    assert read.json()["stockItemLabel"] is None


# --- 3. the deal row: projection, grid, sort ----------------------------------


def test_the_projection_writes_the_live_entitys_stock_number_and_clears_it_with_the_car(db_session):
    dealership = _make_dealership(db_session)
    first = _stock_item(db_session, dealership.id, with_option=False)
    second = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, first)
    assert _deal_row(db_session, offer.id).stock_item_label == first.stock_number

    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=second.id, vehicle_label=second.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    assert _deal_row(db_session, offer.id).stock_item_label == second.stock_number

    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="manual", stock_item_id=None, vehicle_label="Handeingabe"),
        actor_id=uuid.uuid4(),
    )
    assert _deal_row(db_session, offer.id).stock_item_label is None


def test_a_contract_from_the_offer_keeps_the_number_on_the_same_row(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())

    row = _deal_row(db_session, offer.id)
    assert (row.entity_type, row.contract_id, row.stock_item_label) == ("contract", contract.id, item.stock_number)


def test_the_grid_returns_and_sorts_by_the_stock_number(client, db_session):
    dealership = _make_dealership(db_session)
    items = [_stock_item(db_session, dealership.id, with_option=False) for _ in range(3)]
    # Created out of order, so a sort by creation would not pass by accident.
    contracts = [_contract_from_car(db_session, dealership, items[i]) for i in (1, 2, 0)]
    manual = create_contract(db_session, tenant_id=dealership.id, offer=None, actor_id=uuid.uuid4())
    headers = _headers(dealership)

    ascending = client.get("/v1/sales/deals?sort=stockItemLabel:asc", headers=headers)
    descending = client.get("/v1/sales/deals?sort=stockItemLabel:desc", headers=headers)

    assert ascending.status_code == 200, ascending.text
    numbers = [item.stock_number for item in items]
    by_label = {row["id"]: row["stockItemLabel"] for row in ascending.json()["items"]}
    assert by_label[str(manual.id)] is None
    assert {by_label[str(c.id)] for c in contracts} == set(numbers)
    labelled_asc = [row["stockItemLabel"] for row in ascending.json()["items"] if row["stockItemLabel"]]
    labelled_desc = [row["stockItemLabel"] for row in descending.json()["items"] if row["stockItemLabel"]]
    assert labelled_asc == sorted(numbers)
    assert labelled_desc == sorted(numbers, reverse=True)


# --- the nightly check and the backfill ----------------------------------------


def test_the_nightly_check_reports_a_row_whose_stock_number_differs(db_session):
    dealership = _make_dealership(db_session)
    offer = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id, with_option=False))
    contract = _contract_from_car(db_session, dealership, _stock_item(db_session, dealership.id, with_option=False))
    assert _findings(db_session) == set()

    db_session.execute(update(SalesDeal).where(SalesDeal.id == offer.id).values(stock_item_label="S-999999"))
    db_session.execute(update(SalesDeal).where(SalesDeal.id == contract.id).values(stock_item_label=None))
    db_session.commit()

    assert _findings(db_session) == {offer.id, contract.id}


def test_the_backfill_fills_the_rows_written_before_the_column(db_session):
    dealership = _make_dealership(db_session)
    offer = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id, with_option=False))
    contract = _contract_from_car(db_session, dealership, _stock_item(db_session, dealership.id, with_option=False))
    expected = {offer.id: offer.stock_item_label, contract.id: contract.stock_item_label}
    # As the migration leaves them: the new column empty on every row.
    db_session.execute(update(SalesDeal).values(stock_item_label=None))
    db_session.commit()

    done, remaining = reproject(db_session, commit=False)
    assert {r.deal_id for r in done} == set(expected) and remaining == []
    assert _deal_row(db_session, offer.id).stock_item_label is None  # the dry run wrote nothing

    done, remaining = reproject(db_session, commit=True)
    assert {r.deal_id for r in done} == set(expected) and remaining == []
    for deal_id, number in expected.items():
        assert _deal_row(db_session, deal_id).stock_item_label == number
    assert reproject(db_session, commit=True) == ([], [])
