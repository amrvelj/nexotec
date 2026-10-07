"""KAN-86 step 2: the eight per-context migrations that rewrite every enum
column from the member NAME to `.value` (alembic/versions/<context>/
*_kan86_<context>_enum_values.py).

Each migration freezes its own column list instead of importing the app.
The lists were generated from Base.metadata; the first tests check they
still agree with the models (exact equality was proved once, when they were
written — the models will move on, the migrations cannot). The rest seed one
row in every table that has an enum column, put every column into NAME
form with raw SQL (the ORM writes values since step 2), and run the real
migrations on Postgres: `_rewrite` for every member of every column, the
full `upgrade()`/`downgrade()` against main's names-only CHECK, and the
endpoints afterwards.
"""

import datetime as dt
import decimal
import enum
import importlib.util
import os
import pathlib
import uuid
from collections import defaultdict

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select, text

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.core.auth import AccessRole
from app.core.enum_type import StoredEnum
from app.db import Base
from tests.test_kan86_enum_both_forms import _bearer, _create_customer, _create_dealership, _to_name_form, _token

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
_CONTEXTS = ("core", "platform", "customer", "vehicle", "sales", "inventory", "valuation", "integration")


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"kan86_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATIONS = {
    path.parent.name: _load(path) for path in sorted(_VERSIONS.glob("*/*_kan86_*_enum_values.py"))
}


def _enum_columns_by_context() -> dict[str, dict[tuple[str, str], type[enum.Enum]]]:
    by_context: dict[str, dict[tuple[str, str], type[enum.Enum]]] = defaultdict(dict)
    for mapper in Base.registry.mappers:
        context = mapper.class_.__module__.split(".")[1]
        for column in mapper.local_table.columns:
            if isinstance(column.type, StoredEnum):
                by_context[context][(mapper.local_table.name, column.name)] = column.type.enum_class
    return by_context


def _frozen_columns_still_mapped() -> dict[tuple[str, str], list[enum.Enum]]:
    """Every frozen (table, column) that is still a StoredEnum column of
    its context, with the frozen members it still has. The rewrite tests
    run on these, never on the live models: a member or column added later
    was never stored as a name and no frozen migration rewrites it."""

    models = _enum_columns_by_context()
    columns: dict[tuple[str, str], list[enum.Enum]] = {}
    for context, module in _MIGRATIONS.items():
        for table, column, pairs in module._COLUMNS:
            enum_class = models.get(context, {}).get((table, column))
            if enum_class is None:
                continue
            members = [enum_class[name] for name, _ in pairs if name in enum_class.__members__]
            if members:
                columns[(table, column)] = members
    return columns


# --- the frozen lists match the models -------------------------------------


def test_one_migration_per_context_chain():
    assert sorted(_MIGRATIONS) == sorted(_CONTEXTS)
    for context, module in _MIGRATIONS.items():
        parents = {p.stem.split("_", 1)[0] for p in (_VERSIONS / context).glob("*.py")}
        assert module.down_revision in parents, f"{context}: {module.down_revision} is not in its own chain"


def test_every_frozen_column_and_member_agrees_with_the_models():
    """Each frozen (NAME, value) pair is still that member of that column's
    enum, and each frozen table belongs to no other context's models.
    Columns or members added later are not in any list, and need not be:
    they were never stored as names (step 2 writes values)."""

    models = _enum_columns_by_context()
    owner = {table: context for context, columns in models.items() for table, _ in columns}
    for context, module in _MIGRATIONS.items():
        for table, column, pairs in module._COLUMNS:
            assert owner.get(table, context) == context, f"{table} is filed under {context}, owned by {owner[table]}"
            enum_class = models[context].get((table, column))
            if enum_class is None:
                continue  # removed since; its own migration owns that
            members = {m.name: m.value for m in enum_class}
            for name, value in pairs:
                if name in members:
                    assert members[name] == value, (context, table, column, name)
            assert all(isinstance(m.value, str) for m in enum_class)


def test_the_frozen_lists_covered_all_62_columns_when_written():
    frozen = {(t, c) for module in _MIGRATIONS.values() for t, c, _ in module._COLUMNS}
    assert len(frozen) == 62


# --- the rewrite, on Postgres ----------------------------------------------


def _dummy(column: sa.Column):
    column_type = column.type
    if isinstance(column_type, StoredEnum):
        return next(iter(column_type.enum_class))
    if type(column_type).__name__ == "GUID":
        return uuid.uuid4()
    try:
        python_type = column_type.python_type
    except NotImplementedError:  # a TypeDecorator that does not say: its impl does
        python_type = column_type.impl_instance.python_type
    if python_type is uuid.UUID:
        return uuid.uuid4()
    if python_type is bool:
        return False
    if python_type is int:
        return 1
    if python_type is decimal.Decimal:
        return decimal.Decimal(0)
    if python_type is dt.datetime:
        return dt.datetime.now(dt.UTC)
    if python_type is dt.date:
        return dt.datetime.now(dt.UTC).date()
    if python_type in (dict, list):
        return python_type()
    if python_type is str:
        return "x"
    raise AssertionError(f"no dummy for {column.table.name}.{column.name} ({column_type!r})")


def _seed_one_row_each(conn, tables: list[sa.Table]) -> dict[str, dict[str, object]]:
    """One row in every table of `tables` and in every table a NOT NULL
    foreign key of theirs needs, parents first."""

    seeded: dict[str, dict[str, object]] = {}

    def seed(table: sa.Table) -> dict[str, object]:
        if table.name in seeded:
            return seeded[table.name]
        row: dict[str, object] = {}
        for column in table.columns:
            foreign = next(iter(column.foreign_keys), None)
            if foreign is not None and not column.nullable:
                parent = seed(foreign.column.table)
                row[column.name] = parent[foreign.column.name]
            elif isinstance(column.type, StoredEnum) or (
                not column.nullable and column.default is None and column.server_default is None
            ):
                row[column.name] = _dummy(column)
            elif column.primary_key or column.default is not None:
                row[column.name] = _python_default(column)
        if table.name == "integration_connection":
            row["scope"], row["tenant_id"] = _platform_scope(table), None
        conn.execute(table.insert().values(**row))
        seeded[table.name] = row
        return row

    for table in tables:
        seed(table)
    return seeded


def _python_default(column: sa.Column):
    default = column.default
    if default is None:
        return _dummy(column)
    if default.is_callable:
        return default.arg(None)
    return default.arg


def _platform_scope(table: sa.Table):
    return next(m for m in table.c.scope.type.enum_class if m.name == "PLATFORM")


def _raw(conn, table: str, column: str):
    quote = conn.dialect.identifier_preparer.quote
    return conn.execute(text(f"SELECT {quote(column)} FROM {quote(table)}")).scalar_one()


def _set_raw(conn, table: str, column: str, stored: str) -> None:
    quote = conn.dialect.identifier_preparer.quote
    extra = ""
    if (table, column) == ("integration_connection", "scope"):
        extra = ", tenant_id = " + ("NULL" if stored.upper() == "PLATFORM" else f"'{uuid.uuid4()}'")
    conn.execute(text(f"UPDATE {quote(table)} SET {quote(column)} = :stored{extra}"), {"stored": stored})


def _rewrite_all(conn, *, up: bool) -> None:
    for module in _MIGRATIONS.values():
        module._rewrite(conn, up=up)


@pytest.fixture()
def seeded(engine):
    columns = _frozen_columns_still_mapped()
    tables = [Base.metadata.tables[name] for name in sorted({table for table, _ in columns})]
    with engine.begin() as conn:
        rows = _seed_one_row_each(conn, tables)
    assert {table for table, _ in columns} <= set(rows)
    return columns


def test_every_member_of_every_column_is_rewritten_to_its_value_and_back(engine, seeded):
    longest = max(len(members) for members in seeded.values())
    for index in range(longest):
        expected: dict[tuple[str, str], enum.Enum] = {}
        with engine.begin() as conn:
            for (table, column), members in seeded.items():
                member = members[index % len(members)]
                expected[(table, column)] = member
                _set_raw(conn, table, column, member.name)

            _rewrite_all(conn, up=True)
            after_up = {key: _raw(conn, *key) for key in seeded}
            _rewrite_all(conn, up=True)  # a second run changes nothing
            after_second = {key: _raw(conn, *key) for key in seeded}

        assert after_up == {key: member.value for key, member in expected.items()}, index
        assert after_second == after_up
        assert _decoded(engine, seeded) == expected, index

        with engine.begin() as conn:
            _rewrite_all(conn, up=False)
            after_down = {key: _raw(conn, *key) for key in seeded}
        assert after_down == {key: member.name for key, member in expected.items()}, index


def _decoded(engine, seeded) -> dict[tuple[str, str], enum.Enum]:
    """Every seeded enum cell as the ORM reads it."""

    mapped = {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers}
    with sa.orm.Session(engine) as session:
        return {
            (table, column): session.execute(
                select(getattr(mapped[table], _attribute(mapped[table], column)))
            ).scalar_one()
            for table, column in seeded
        }


def _attribute(model, column: str) -> str:
    return next(attr.key for attr in sa.inspect(model).column_attrs if attr.columns[0].name == column)


def test_a_string_that_is_neither_form_is_left_alone(engine, seeded, capsys):
    """A stored string that is no member's name or value (KAN-60's 'MAIL'
    before its fix, say) is not the rewrite's to guess: left as stored, and
    reported. (KAN-54's MESSAGE is a member and is rewritten like any other
    — covered by the member-cycling test above.)"""

    with engine.begin() as conn:
        _set_raw(conn, "customer", "preferred_channel", "SMS_LEGACY")
        _set_raw(conn, "customer", "language", "DE")
        _rewrite_all(conn, up=True)
        assert _raw(conn, "customer", "preferred_channel") == "SMS_LEGACY"
        assert _raw(conn, "customer", "language") == "de"

    assert "customer.preferred_channel holds 'SMS_LEGACY' in 1 row(s)" in capsys.readouterr().out


def test_a_row_already_in_value_form_is_not_touched(engine, seeded, capsys):
    with engine.begin() as conn:
        _set_raw(conn, "customer_email", "consent_source", "form")  # KAN-91's shape
        _rewrite_all(conn, up=True)
        assert _raw(conn, "customer_email", "consent_source") == "form"

    out = capsys.readouterr().out
    assert "customer_email.consent_source NAME -> value: 0 row(s)" in out
    assert "customer_email.consent_source holds" not in out


# --- the real upgrade() / downgrade(), as alembic runs them -----------------

_NAMES_CHECK = "(scope = 'PLATFORM' AND tenant_id IS NULL) OR (scope = 'TENANT' AND tenant_id IS NOT NULL)"


def _check_definition(conn) -> str:
    return conn.execute(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_integration_connection_scope_tenant_id'")
    ).scalar_one()


def _with_mains_check(conn) -> None:
    """The integration_connection CHECK as main has it: names only."""

    conn.execute(text("ALTER TABLE integration_connection DROP CONSTRAINT ck_integration_connection_scope_tenant_id"))
    conn.execute(
        text(f"ALTER TABLE integration_connection ADD CONSTRAINT ck_integration_connection_scope_tenant_id CHECK ({_NAMES_CHECK})")
    )


def _run(conn, step) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        step()


@pytest.mark.parametrize("pick", [0, -1], ids=["first-member", "last-member"])
def test_upgrade_widens_the_check_then_rewrites_and_downgrade_undoes_both(engine, seeded, pick):
    members = {key: column_members[pick] for key, column_members in seeded.items()}
    with engine.begin() as conn:
        for (table, column), member in members.items():
            _set_raw(conn, table, column, member.name)
        _with_mains_check(conn)

        for module in _MIGRATIONS.values():
            _run(conn, module.upgrade)

        assert {key: _raw(conn, *key) for key in seeded} == {key: m.value for key, m in members.items()}
        assert "'platform'" in _check_definition(conn) and "'PLATFORM'" in _check_definition(conn)
        # A step-1 instance still serving writes the name: the widened CHECK takes it.
        _set_raw(conn, "integration_connection", "scope", "TENANT")
    assert _decoded(engine, seeded)[("integration_connection", "scope")].name == "TENANT"

    with engine.begin() as conn:
        for module in reversed(list(_MIGRATIONS.values())):
            _run(conn, module.downgrade)
        after = {key: _raw(conn, *key) for key in seeded}
        assert "'platform'" not in _check_definition(conn)

    expected = {key: m.name for key, m in members.items()}
    expected[("integration_connection", "scope")] = "TENANT"
    assert after == expected


def test_rewriting_before_widening_the_check_would_fail(engine, seeded):
    """Why upgrade() replaces the CHECK first: main's names-only CHECK
    refuses 'platform'."""

    with engine.begin() as conn:
        _set_raw(conn, "integration_connection", "scope", "PLATFORM")
        _with_mains_check(conn)
    with (
        pytest.raises(sa.exc.IntegrityError, match="ck_integration_connection_scope_tenant_id"),
        engine.begin() as conn,
    ):
        _MIGRATIONS["integration"]._rewrite(conn, up=True)


# --- after the migration, through the endpoints ------------------------------


def test_name_form_rows_serve_from_the_endpoints_after_the_upgrade(client, db_session, engine):
    """Criterion 4, post-migration: a customer (with its email projection)
    and a stock item written in the name form, as step-1 code wrote them,
    are values after the real upgrade() and are listed and filtered."""

    tenant_id = _create_dealership(client)
    customer = _create_customer(client, tenant_id, "Named")
    _to_name_form(db_session, customer["id"])
    inventory = _bearer(_token(tenant_id, AccessRole.INVENTORY))
    response = client.post("/v1/inventory/stock-items", json={"vehicleLabel": "VW Golf", "condition": "new"}, headers=inventory)
    assert response.status_code == 201, response.text
    stock_id = response.json()["id"]
    db_session.execute(
        text("UPDATE stock_item SET lifecycle_status = upper(lifecycle_status), condition = upper(condition) WHERE id = :id"),
        {"id": stock_id},
    )
    db_session.commit()
    before = db_session.execute(
        text("SELECT c.customer_type, c.language, e.email_type FROM customer c JOIN customer_email e ON e.customer_id = c.id")
    ).one()
    assert tuple(before) == ("INDIVIDUAL", "DE", "PERSONAL")
    db_session.close()

    with engine.begin() as conn:
        for module in _MIGRATIONS.values():
            _run(conn, module.upgrade)

    with engine.connect() as conn:
        after = conn.execute(
            text("SELECT c.customer_type, c.language, e.email_type FROM customer c JOIN customer_email e ON e.customer_id = c.id")
        ).one()
        stock = conn.execute(text("SELECT condition FROM stock_item WHERE id = :id"), {"id": stock_id}).scalar_one()
    assert tuple(after) == ("individual", "de", "personal")
    assert stock == "new"

    sales = _bearer(_token(tenant_id, AccessRole.SALES))
    listed = client.get("/v1/customers", headers=sales)
    filtered = client.get("/v1/customers", params={"customerType": "individual"}, headers=sales)
    stock_list = client.get("/v1/inventory/stock-items", params={"condition": "new"}, headers=inventory)
    assert listed.status_code == 200, listed.text
    assert [(i["id"], i["customerType"], i["email"]) for i in listed.json()["items"]] == [
        (customer["id"], "individual", customer["email"])
    ]
    assert filtered.status_code == 200 and [i["id"] for i in filtered.json()["items"]] == [customer["id"]]
    assert stock_list.status_code == 200, stock_list.text
    assert [i["id"] for i in stock_list.json()["items"]] == [stock_id]
