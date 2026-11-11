"""KAN-563 — the four Sales configuration columns vehicle's purge asks
through `sales.public.referenced_configuration_ids` are indexed, and the
safety net's query uses them (one SELECT per slot column, ADR-120)."""

import os
import uuid

import pytest
from sqlalchemy import inspect, select, text

from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.services.configuration_reference import _HELD_COLUMNS, referenced_configuration_ids

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="index use is Postgres-only")

_EXPECTED = {
    ("sales_offer", "configuration_id"): "ix_sales_offer_configuration_id",
    ("sales_offer", "trade_in_configuration_id"): "ix_sales_offer_trade_in_configuration_id",
    ("sales_contract", "configuration_id"): "ix_sales_contract_configuration_id",
    ("sales_contract", "trade_in_configuration_id"): "ix_sales_contract_trade_in_configuration_id",
}


def test_every_column_the_safety_net_filters_on_has_an_index(db_session):
    inspector = inspect(db_session.get_bind())
    indexed = {
        (table, tuple(index["column_names"])): index["name"]
        for table in ("sales_offer", "sales_contract")
        for index in inspector.get_indexes(table)
    }
    for (table, column), name in _EXPECTED.items():
        assert indexed.get((table, (column,))) == name, f"{table}.{column} has no index {name}"
    # The safety net reads exactly these four columns — a fifth would need its own index.
    assert {(c.table.name, c.name) for c in _HELD_COLUMNS} == set(_EXPECTED)


def test_the_safety_nets_query_is_answered_by_the_index(db_session):
    """With sequential scans priced out, the planner reaches each column
    through its index — which it can only do if the index exists."""

    ids = [uuid.uuid4(), uuid.uuid4()]
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    for column in _HELD_COLUMNS:
        compiled = select(column).where(column.in_(ids)).compile(
            dialect=db_session.get_bind().dialect, compile_kwargs={"literal_binds": True}
        )
        plan = "\n".join(row[0] for row in db_session.execute(text(f"EXPLAIN {compiled}")))
        assert _EXPECTED[(column.table.name, column.name)] in plan, plan
    db_session.rollback()


def test_the_safety_net_still_answers_which_ids_are_held(db_session):
    assert SalesOffer.__table__.c.configuration_id.index is True
    assert SalesContract.__table__.c.trade_in_configuration_id.index is True
    assert referenced_configuration_ids(db_session, [uuid.uuid4()]) == set()
