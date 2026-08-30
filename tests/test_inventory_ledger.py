"""WP-7 PR-6: the Wagenbuch (ADR-029)."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.core.base import utcnow
from app.core.errors import UnprocessableEntityError
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.models.stock_item_ledger import LedgerCategory, LedgerDirection
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.ledger import list_ledger_entries, record_cost
from app.inventory.services.stock_item import create_stock_item


def _make_item(db_session, tenant_id):
    return create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )


def test_record_cost_derives_direction_from_category(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    entry = record_cost(
        db_session,
        item=item,
        category=LedgerCategory.AUFBEREITUNG,
        amount=Decimal("450.00"),
        occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
        source_ref=str(uuid.uuid4()),
        actor_id=uuid.uuid4(),
    )
    assert entry.direction == LedgerDirection.COST


def test_record_cost_is_idempotent_by_source_ref(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    source_ref = str(uuid.uuid4())
    first = record_cost(
        db_session, item=item, category=LedgerCategory.REPARATUR, amount=Decimal("100.00"),
        occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC), source_ref=source_ref, actor_id=uuid.uuid4(),
    )
    second = record_cost(
        db_session, item=item, category=LedgerCategory.REPARATUR, amount=Decimal("100.00"),
        occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC), source_ref=source_ref, actor_id=uuid.uuid4(),
    )
    assert first.id == second.id

    entries = list_ledger_entries(db_session, tenant_id=tenant_id, stock_item_id=item.id)
    assert len(entries) == 1


def test_automatic_only_category_refuses_manual_booking(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    with pytest.raises(UnprocessableEntityError):
        record_cost(
            db_session, item=item, category=LedgerCategory.VERKAUFSERLOES, amount=Decimal("30000.00"),
            occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC), source_ref=str(uuid.uuid4()), actor_id=uuid.uuid4(),
        )


def test_automatic_only_category_is_allowed_when_is_auto(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    entry = record_cost(
        db_session, item=item, category=LedgerCategory.VERKAUFSERLOES, amount=Decimal("30000.00"),
        occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC), source_ref=str(uuid.uuid4()), actor_id=None, is_auto=True,
    )
    assert entry.is_auto is True


def test_kickback_and_zusatzerloes_are_hand_bookable(db_session):
    """Amended 2026-08-21: Finance doesn't exist yet, so these two revenue
    categories are hand-bookable in practice despite being automatic in
    spirit."""

    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    for category in (LedgerCategory.KICKBACK, LedgerCategory.ZUSATZERLOES):
        record_cost(
            db_session, item=item, category=category, amount=Decimal("200.00"),
            occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC), source_ref=str(uuid.uuid4()), actor_id=uuid.uuid4(),
        )


def test_record_cost_refuses_when_book_is_closed(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    item.left_stock_at = utcnow()
    db_session.commit()

    with pytest.raises(UnprocessableEntityError):
        record_cost(
            db_session, item=item, category=LedgerCategory.REPARATUR, amount=Decimal("100.00"),
            occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC), source_ref=str(uuid.uuid4()), actor_id=uuid.uuid4(),
        )


def test_list_ledger_entries_orders_by_occurred_at_descending(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id)
    record_cost(
        db_session, item=item, category=LedgerCategory.REPARATUR, amount=Decimal("100.00"),
        occurred_at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC), source_ref=str(uuid.uuid4()), actor_id=uuid.uuid4(),
    )
    record_cost(
        db_session, item=item, category=LedgerCategory.AUFBEREITUNG, amount=Decimal("200.00"),
        occurred_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC), source_ref=str(uuid.uuid4()), actor_id=uuid.uuid4(),
    )
    entries = list_ledger_entries(db_session, tenant_id=tenant_id, stock_item_id=item.id)
    assert [e.category for e in entries] == [LedgerCategory.AUFBEREITUNG, LedgerCategory.REPARATUR]
