"""KAN-740: a text column sorts in one order whatever the database's default collation (Anto,
2026-11-18: one order for every UI language — case ignored, Ä/é beside A/e). The cloud test
database runs `C.UTF-8`, so without the collation these sorts come back in byte order
(«Arzt, Ezra, Zeta, alpha, Ärztin, école»)."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql, sqlite

from app.core.pagination import SortPageParams, paginate_query_sorted
from app.core.sorting import SortField, build_page_for_sort, decode_cursor_for_sort
from app.core.text_collation import TEXT_SORT_COLLATION, TEXT_SORT_LOCALE, CollatedSortColumn
from tests.demo_models import DemoWidget
from tests.postgres_only import POSTGRES_SQL
from tests.text_sort_support import text_sort_order

NAMES = ["Zeta", "alpha", "Ärztin", "Arzt", "école", "Ezra", "arzt", "Émile", "zeta"]
EXPECTED = ["alpha", "Arzt", "arzt", "Ärztin", "école", "Émile", "Ezra", "Zeta", "zeta"]


def _widgets(db_session) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    db_session.add_all(DemoWidget(tenant_id=tenant_id, name=name) for name in NAMES)
    db_session.commit()
    return tenant_id


def _pages(db_session, tenant_id, direction: str, limit: int = 2) -> list[str]:
    fields = [SortField(api_name="name", column=DemoWidget.name, direction=direction, nullable=False)]
    names: list[str] = []
    cursor = None
    for _ in range(len(NAMES) + 1):
        params = SortPageParams(limit=limit, cursor=decode_cursor_for_sort(cursor, fields), sort_fields=fields)
        stmt = select(DemoWidget).where(DemoWidget.tenant_id == tenant_id)
        rows = list(db_session.scalars(paginate_query_sorted(stmt, model=DemoWidget, params=params)))
        page, cursor = build_page_for_sort(rows, params)
        names.extend(widget.name for widget in page)
        if cursor is None:
            return names
    raise AssertionError("the pages never ended")


@POSTGRES_SQL
def test_the_test_helper_asks_the_database_for_the_ruled_order(db_session):
    assert text_sort_order(db_session, NAMES) == EXPECTED
    assert text_sort_order(db_session, NAMES, descending=True) == list(reversed(EXPECTED))


@POSTGRES_SQL  # an ICU collation of Postgres; SQLite has none
def test_text_sorts_case_and_accent_insensitively_across_pages_both_ways(db_session):
    tenant_id = _widgets(db_session)

    assert _pages(db_session, tenant_id, "asc") == EXPECTED
    assert _pages(db_session, tenant_id, "desc") == list(reversed(EXPECTED))


@POSTGRES_SQL
def test_the_collation_exists_as_ruled(db_session):
    locale = db_session.scalar(
        text("SELECT colllocale FROM pg_collation WHERE collname = :name").bindparams(name=TEXT_SORT_COLLATION)
        if db_session.bind.dialect.server_version_info >= (17,)
        else text("SELECT colliculocale FROM pg_collation WHERE collname = :name").bindparams(name=TEXT_SORT_COLLATION)
    )
    assert locale == TEXT_SORT_LOCALE


def test_only_a_word_column_is_collated():
    assert isinstance(SortField("name", DemoWidget.name, "asc", False).column, CollatedSortColumn)
    for column in (DemoWidget.id, DemoWidget.created_at, DemoWidget.version):
        assert SortField("x", column, "asc", False).column is column


def test_a_code_or_identifier_keeps_byte_order():
    """ADR-190: a VIN, a Stammnummer or a number is no word; it keeps byte order and its indexes."""

    from app.customer.models.customer import Customer
    from app.inventory.models.stock_item import StockItem
    from app.vehicle.models.vehicle_mdm import VehicleMdm

    for column in (VehicleMdm.vin, VehicleMdm.stammnummer, StockItem.stock_number, Customer.customer_number):
        assert SortField("x", column, "asc", False).column is column, column
    assert isinstance(SortField("x", Customer.last_name, "asc", False).column, CollatedSortColumn)


def test_order_and_keyset_both_name_the_collation_on_postgres_and_neither_on_sqlite():
    field = SortField(api_name="name", column=DemoWidget.name, direction="asc", nullable=False)
    stmt = select(DemoWidget.id).order_by(field.column.asc()).where(field.column > "m")

    on_postgres = str(stmt.compile(dialect=postgresql.dialect()))
    assert on_postgres.count(f'COLLATE "{TEXT_SORT_COLLATION}"') == 2
    assert "COLLATE" not in str(stmt.compile(dialect=sqlite.dialect()))
