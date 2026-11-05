"""KAN-157 (CLAUDE.md rule 2; Anto, 2026-11-03, option A): an offer or a
contract on a stock car carries the stock number as its label, with when it
was copied — once, when the car is put on it, from the pricing read the
snapshot freeze makes anyway (no new hop). A contract from an offer and a
copied offer take the offer's; a car taken off the deal takes its label with
it. `scripts/backfill_sales_stock_item_labels.py` fills the rows written
before."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract
from app.sales.services.offer import copy_offer, update_offer
from scripts import backfill_sales_stock_item_labels as backfill_script
from tests.test_sales_offer_refinements import _make_dealership, _offer_with_vehicle, _stock_item


def _put_car(db_session, offer: SalesOffer, item) -> SalesOffer:
    return update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )


def test_a_stock_car_put_on_an_offer_gives_it_the_stock_number(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)

    offer = _offer_with_vehicle(db_session, dealership.id, item)

    assert offer.stock_item_id == item.id
    assert offer.stock_item_label == item.stock_number
    assert offer.stock_item_denorm_refreshed_at is not None


def test_another_car_takes_its_own_number_and_a_manual_car_none(db_session):
    dealership = _make_dealership(db_session)
    first = _stock_item(db_session, dealership.id, with_option=False)
    second = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, first)
    assert offer.stock_item_label == first.stock_number

    offer = _put_car(db_session, offer, second)
    assert (offer.stock_item_id, offer.stock_item_label) == (second.id, second.stock_number)

    offer = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="manual", stock_item_id=None, vehicle_label="Handeingabe"),
        actor_id=uuid.uuid4(),
    )
    assert (offer.stock_item_id, offer.stock_item_label, offer.stock_item_denorm_refreshed_at) == (None, None, None)


def test_a_contract_from_the_offer_from_the_car_and_a_copied_offer_all_carry_it(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    from_offer = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    assert (from_offer.stock_item_label, from_offer.stock_item_denorm_refreshed_at) == (
        offer.stock_item_label,
        offer.stock_item_denorm_refreshed_at,
    )

    other = _stock_item(db_session, dealership.id, with_option=False)
    from_car = create_contract(
        db_session, tenant_id=dealership.id, offer=None, actor_id=uuid.uuid4(), stock_item_id=other.id,
        vehicle_label=other.vehicle_label,
    )
    assert (from_car.stock_item_id, from_car.stock_item_label) == (other.id, other.stock_number)
    assert from_car.stock_item_denorm_refreshed_at is not None

    copied = copy_offer(db_session, source=offer, actor_id=uuid.uuid4())
    assert (copied.stock_item_id, copied.stock_item_label) == (item.id, item.stock_number)


def test_the_backfill_fills_rows_written_before_dry_run_first_and_reports_what_it_cannot(engine, db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    orphan = _offer_with_vehicle(db_session, dealership.id, _stock_item(db_session, dealership.id, with_option=False))
    offer_id, contract_id, orphan_id = offer.id, contract.id, orphan.id
    versions = (offer.version, contract.version)
    missing_item = uuid.uuid4()
    # As written before KAN-157: no label — and one naming no stock item at all.
    for model in (SalesOffer, SalesContract):
        db_session.execute(update(model).values(stock_item_label=None, stock_item_denorm_refreshed_at=None))
    db_session.execute(update(SalesOffer).where(SalesOffer.id == orphan_id).values(stock_item_id=missing_item))
    db_session.commit()

    dry = backfill_script.backfill(db_session, commit=False)
    assert sorted(kind for kind, _, _ in dry.filled) == ["contract", "offer"]
    assert dry.unresolved == [("offer", orphan_id, missing_item)]
    db_session.expire_all()
    assert db_session.get(SalesOffer, offer_id).stock_item_label is None  # the dry run wrote nothing

    done = backfill_script.backfill(db_session, commit=True)
    assert {(kind, row_id, number) for kind, row_id, number in done.filled} == {
        ("offer", offer_id, item.stock_number),
        ("contract", contract_id, item.stock_number),
    }
    db_session.expire_all()
    filled_offer, filled_contract = db_session.get(SalesOffer, offer_id), db_session.get(SalesContract, contract_id)
    assert filled_offer.stock_item_label == item.stock_number
    assert filled_offer.stock_item_denorm_refreshed_at is not None
    assert filled_contract.stock_item_label == item.stock_number
    assert (filled_offer.version, filled_contract.version) == versions  # a label is a copy, not an edit
    assert db_session.get(SalesOffer, orphan_id).stock_item_label is None

    # A row another writer holds is skipped, counted and left for the next run.
    blocker = sessionmaker(bind=engine)()
    try:
        held = blocker.scalar(select(SalesOffer).where(SalesOffer.id == orphan_id).with_for_update())
        assert held is not None
        skipped = backfill_script.backfill(db_session, commit=False)
        assert skipped.skipped == {"offer": 1} and skipped.unresolved == []
        assert "SKIPPED (held by another writer — run again): 1" in backfill_script.render(skipped, committed=False)
    finally:
        blocker.rollback()
        blocker.close()

    again = backfill_script.backfill(db_session, commit=True)
    assert again.filled == [] and again.unresolved == [("offer", orphan_id, missing_item)]
    assert "UNRESOLVED (no stock item of the row's own Betrieb): 1" in backfill_script.render(again, committed=True)
