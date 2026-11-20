"""KAN-740 (ADR-190): the indexes behind the word sorts serve the collated ascending sort — the
first page reads the index in order instead of sorting every row. (A descending sort is `DESC NULLS
LAST` in the preserved `pagination.py`, which no default-ordered b-tree serves, collated or not:
unchanged by KAN-740.) Without the
collation on the index (the state before KAN-740) the planner cannot use it for
`ORDER BY … COLLATE "nexotec_text_sort"`, and every page is a full sort.

Each list's statement is built as the list builds it (its `SortField`s through the preserved
`paginate_query_sorted`); `enable_seqscan` and `enable_sort` are off, so that on near-empty test
tables without statistics the planner shows whether an index *can* give the order — the property
under test — rather than which plan is cheapest for one row: when no index can, the Sort stays
(disabled, not impossible) and the test fails."""

import uuid

import pytest
from sqlalchemy import select, text

from app.core.pagination import SortPageParams, paginate_query_sorted
from app.core.sorting import decode_cursor_for_sort, parse_sort
from app.customer.api.customers import CUSTOMER_SORT_FIELDS
from app.customer.models.customer import Customer
from app.sales.api.deals import DEAL_SORT_FIELDS
from app.sales.models.deal import SalesDeal
from tests.postgres_only import POSTGRES_SQL


def _plan(db_session, stmt) -> str:
    compiled = stmt.compile(dialect=db_session.bind.dialect, compile_kwargs={"literal_binds": True})
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    db_session.execute(text("SET LOCAL enable_sort = off"))
    rows = db_session.execute(text(f"EXPLAIN {compiled}")).scalars().all()
    db_session.rollback()
    return "\n".join(rows)


def _statement(base, model, sort: str, allowed, cursor=None):
    fields = parse_sort(sort, allowed=allowed)
    params = SortPageParams(limit=25, cursor=decode_cursor_for_sort(cursor, fields), sort_fields=fields)
    return paginate_query_sorted(base, model=model, params=params), params


def _assert_served(plan: str, index: str) -> None:
    assert index in plan, plan
    # A top-level full Sort means the index did not give the order; an Incremental Sort only
    # breaks ties (the id) inside the order the index gave.
    full_sorts = [line for line in plan.splitlines() if "Sort" in line and "Incremental Sort" not in line and "Sort Key" not in line and "Presorted Key" not in line]
    assert not full_sorts, plan


@POSTGRES_SQL
@pytest.mark.parametrize(
    ("sort", "index"),
    [("lastName:asc", "ix_customer_group_id_last_name"), ("companyName:asc", "ix_customer_company_name")],
)
def test_the_customer_list_reads_its_name_indexes_in_order(db_session, sort, index):
    group_id = uuid.uuid4()
    base = select(Customer).where(Customer.group_id == group_id)
    stmt, _ = _statement(base, Customer, sort, CUSTOMER_SORT_FIELDS)
    _assert_served(_plan(db_session, stmt), index)


@POSTGRES_SQL
def test_the_deal_list_reads_its_seller_index_in_order(db_session):
    base = select(SalesDeal).where(SalesDeal.tenant_id == uuid.uuid4())
    stmt, _ = _statement(base, SalesDeal, "ownerLabel:asc", DEAL_SORT_FIELDS)
    _assert_served(_plan(db_session, stmt), "ix_sales_deal_owner_label")
