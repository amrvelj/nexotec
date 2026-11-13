"""KAN-674 step 1: `scripts/backfill_trade_in_seller_on_stock_items.py` gives a
trade-in stock item created before KAN-632 the seller its events now carry
(`trade_in_customer_id`, `trade_in_signed_at`), read from Sales exactly as the
worker's fallback reads it (`sales.public.get_trade_in_seller`)."""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.inventory.models.stock_item import StockItem
from app.inventory.services.stock_item import trade_in_seller_fields
from app.sales.public import get_trade_in_seller
from tests.test_kan189_trade_in_without_register import _bought_trade_in

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_trade_in_seller_on_stock_items.py"
_spec = importlib.util.spec_from_file_location("backfill_trade_in_seller_on_stock_items", _SCRIPT)
assert _spec is not None and _spec.loader is not None
backfill_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_script)


def _pre_kan632_item(db_session, engine):
    """A bought trade-in whose item kept no seller — created before KAN-632."""

    contract, item, _ = _bought_trade_in(db_session, engine)
    seller = (item.trade_in_customer_id, item.trade_in_signed_at)
    assert seller[0] is not None, "the fixture's item should carry the seller since KAN-632"
    item.trade_in_customer_id = None
    item.trade_in_signed_at = None
    db_session.commit()
    return contract, item.id


def _item(db_session, item_id) -> StockItem:
    db_session.expire_all()
    return db_session.get(StockItem, item_id)


def test_a_dry_run_reports_the_fill_and_writes_nothing(db_session, engine):
    contract, item_id = _pre_kan632_item(db_session, engine)

    report = backfill_script.backfill(db_session, commit=False)

    assert [(item, contract_id) for item, contract_id, _ in report.filled] == [(item_id, contract.id)]
    item = _item(db_session, item_id)
    assert (item.trade_in_customer_id, item.trade_in_signed_at) == (None, None)
    assert "DRY RUN" in backfill_script.render(report, committed=False)


def test_a_dry_run_refuses_a_commit(db_session, engine, monkeypatch):
    _pre_kan632_item(db_session, engine)

    def _commits(db, *, tenant_id, contract_id):
        db.commit()

    monkeypatch.setattr(backfill_script, "get_trade_in_seller", _commits)
    with pytest.raises(RuntimeError, match="dry run"):
        backfill_script.backfill(db_session, commit=False)


def test_commit_fills_the_seller_the_fallback_would_answer_and_a_rerun_finds_nothing(db_session, engine):
    contract, item_id = _pre_kan632_item(db_session, engine)
    expected = get_trade_in_seller(db_session, tenant_id=contract.tenant_id, contract_id=contract.id)
    assert expected is not None

    report = backfill_script.backfill(db_session, commit=True)

    assert [customer for _, _, customer in report.filled] == [expected.customer_id]
    item = _item(db_session, item_id)
    assert (item.trade_in_customer_id, item.trade_in_signed_at) == (expected.customer_id, expected.known_since)
    # What the item's purchase and storno events carry from now on.
    assert trade_in_seller_fields(item) == {
        "tradeInCustomerId": str(expected.customer_id),
        "tradeInSignedAt": expected.known_since.isoformat(),
    }
    again = backfill_script.backfill(db_session, commit=True)
    assert (again.filled, again.unresolved, again.skipped) == ([], [], 0)


def test_an_item_whose_contract_this_betrieb_does_not_have_is_reported_not_guessed(db_session, engine):
    _, item_id = _pre_kan632_item(db_session, engine)
    item = _item(db_session, item_id)
    stranger = uuid.uuid4()
    item.pipeline_ref = f"contract:{stranger}:trade_in"
    db_session.commit()

    report = backfill_script.backfill(db_session, commit=True)

    assert report.filled == []
    assert [(i, c) for i, c, _ in report.unresolved] == [(item_id, stranger)]
    assert _item(db_session, item_id).trade_in_customer_id is None


def test_items_that_are_no_trade_in_are_left_alone(db_session, engine):
    _bought_trade_in(db_session, engine)  # carries its seller already
    others = db_session.scalars(
        select(StockItem).where(StockItem.pipeline_ref.is_(None) | ~StockItem.pipeline_ref.like("%:trade_in"))
    ).all()

    report = backfill_script.backfill(db_session, commit=True)

    assert (report.filled, report.unresolved) == ([], [])
    assert all(_item(db_session, other.id).trade_in_customer_id is None for other in others)
