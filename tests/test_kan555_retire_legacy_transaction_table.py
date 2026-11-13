"""KAN-555 (ADR-117; Anto, 2026-11-13, option A): the sales migration
`4726dcd89260` drops the legacy `transaction` table with every row, logging
how many it dropped by type and status; its downgrade recreates the table
empty in the exact shape it had at `976ea5f43dc0`, because the older
migrations' downgrades (the KAN-86/KAN-175 enum CHECKs, the retire comment,
PR-2's foreign keys) run over those names in CI's round trip. The vehicle
migration `778849af53d8` drops `vehicle_mdm_custody_event.transaction_id`,
the one column that named it.

The model is gone, so the test schema has no `transaction` table: the
downgrade runs first (it creates it), its catalogue is compared with the shape
a database at `976ea5f43dc0` had, then the upgrade drops it again.
"""

import importlib.util
import logging
import os
import pathlib
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
_postgres_only = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _load(chain: str, revision: str):
    (path,) = sorted((_VERSIONS / chain).glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"kan555_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DROP_TABLE = _load("sales", "4726dcd89260")
_DROP_COLUMN = _load("vehicle", "778849af53d8")


def _run(engine, step) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        step()


def _has_table(engine) -> bool:
    return "transaction" in sa.inspect(engine).get_table_names()


@pytest.fixture
def engine(engine):
    """The test schema is the models' (`create_all`), so a fresh database has no
    `transaction` table. One built before KAN-555 still holds it, and the schema
    reset drops only the tables the models know: drop that leftover first."""

    if _has_table(engine):
        with engine.begin() as conn:
            conn.execute(sa.text('DROP TABLE "transaction"'))
    return engine


def _shape(engine) -> dict:
    with engine.connect() as conn:
        columns = conn.execute(
            sa.text(
                "SELECT column_name, data_type, character_maximum_length, numeric_precision, numeric_scale, "
                "is_nullable, column_default FROM information_schema.columns WHERE table_name = 'transaction' "
                "ORDER BY ordinal_position"
            )
        ).all()
        constraints = conn.execute(
            sa.text(
                "SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = '\"transaction\"'::regclass ORDER BY 1"
            )
        ).all()
        indexes = conn.execute(
            sa.text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'transaction' ORDER BY 1")
        ).all()
        comments = conn.execute(
            sa.text(
                "SELECT a.attname, col_description(c.oid, a.attnum) FROM pg_class c "
                "JOIN pg_attribute a ON a.attrelid = c.oid WHERE c.relname = 'transaction' "
                "AND col_description(c.oid, a.attnum) IS NOT NULL ORDER BY 1"
            )
        ).all()
        table_comment = conn.execute(sa.text("SELECT obj_description('\"transaction\"'::regclass, 'pg_class')")).scalar()
    return {
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "comments": dict(comments),
        "table_comment": table_comment,
    }


def _insert(engine, *, transaction_type: str, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                'INSERT INTO "transaction" (id, tenant_id, transaction_type, status, customer_id, vehicle_id, '
                "primary_user_id, created_at, updated_at) VALUES (:id, :tenant, :type, :status, :customer, "
                ":vehicle, :user, now(), now())"
            ),
            {
                "id": uuid.uuid4(), "tenant": uuid.uuid4(), "type": transaction_type, "status": status,
                "customer": uuid.uuid4(), "vehicle": uuid.uuid4(), "user": uuid.uuid4(),
            },
        )


@_postgres_only
def test_the_downgrade_recreates_the_table_in_its_old_shape(engine):
    assert not _has_table(engine)  # no model maps it any more

    _run(engine, _DROP_TABLE.downgrade)
    try:
        shape = _shape(engine)

        assert [(c[0], c[1], c[2], c[3], c[4], c[5]) for c in shape["columns"]] == [
            ("id", "uuid", None, None, None, "NO"),
            ("tenant_id", "uuid", None, None, None, "NO"),
            ("transaction_type", "character varying", 16, None, None, "NO"),
            ("status", "character varying", 16, None, None, "NO"),
            ("customer_id", "uuid", None, None, None, "NO"),
            ("vehicle_id", "uuid", None, None, None, "NO"),
            ("primary_user_id", "uuid", None, None, None, "NO"),
            ("amount", "numeric", None, 12, 2, "YES"),
            ("transaction_date", "timestamp with time zone", None, None, None, "YES"),
            ("external_ref", "character varying", 255, None, None, "YES"),
            ("notes", "text", None, None, None, "YES"),
            ("version", "integer", None, 32, 0, "NO"),
            ("created_at", "timestamp with time zone", None, None, None, "NO"),
            ("updated_at", "timestamp with time zone", None, None, None, "NO"),
            ("created_by", "uuid", None, None, None, "YES"),
            ("updated_by", "uuid", None, None, None, "YES"),
        ]
        assert {c[0]: c[6] for c in shape["columns"] if c[6] is not None} == {
            "status": "'draft'::character varying",
            "version": "1",
        }
        assert [(c[0], c[1]) for c in shape["constraints"]] == [
            ("ck_transaction_status_enum", "c"),
            ("ck_transaction_transaction_type_enum", "c"),
            ("transaction_pkey", "p"),
        ]
        checks = {c[0]: c[2] for c in shape["constraints"] if c[1] == "c"}
        for value in ("'draft'", "'completed'", "'cancelled'"):
            assert value in checks["ck_transaction_status_enum"]
        for value in ("'sale'", "'trade_in'"):
            assert value in checks["ck_transaction_transaction_type_enum"]
        assert [i[0] for i in shape["indexes"]] == [
            "ix_transaction_customer_id",
            "ix_transaction_primary_user_id",
            "ix_transaction_tenant_id",
            "ix_transaction_vehicle_id",
            "transaction_pkey",
        ]
        assert shape["comments"] == {
            "customer_id": "Owned by the customer context. No DB-level FK (PR-2, ADR-015).",
            "primary_user_id": "Owned by the platform context (User). No DB-level FK.",
            "tenant_id": "Owned by the platform context (Dealership). No DB-level FK.",
            "vehicle_id": "Owned by the vehicle context. No DB-level FK (PR-2, ADR-015).",
        }
        assert shape["table_comment"].startswith("RETIRED (WP-8 PR-7, ADR-050/S-D12)")
    finally:
        # The schema reset drops only the tables the models know: never leave this one behind.
        _run(engine, _DROP_TABLE.upgrade)
    assert not _has_table(engine)


@_postgres_only
def test_the_upgrade_drops_every_row_and_logs_them_by_type_and_status(engine, caplog):
    _run(engine, _DROP_TABLE.downgrade)
    try:
        _insert(engine, transaction_type="sale", status="draft")
        _insert(engine, transaction_type="sale", status="draft")
        _insert(engine, transaction_type="sale", status="completed")
        _insert(engine, transaction_type="trade_in", status="cancelled")
    finally:
        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            _run(engine, _DROP_TABLE.upgrade)

    assert not _has_table(engine)
    logged = [r.getMessage() for r in caplog.records if r.getMessage().startswith("KAN-555")]
    assert logged == [
        "KAN-555: dropping the legacy transaction table with 4 row(s)",
        "KAN-555:   sale / completed: 1",
        "KAN-555:   sale / draft: 2",
        "KAN-555:   trade_in / cancelled: 1",
    ]


@_postgres_only
def test_the_custody_event_column_that_named_it_goes_and_comes_back_empty(engine):
    def custody_columns() -> dict[str, dict]:
        return {c["name"]: c for c in sa.inspect(engine).get_columns("vehicle_mdm_custody_event")}

    assert "transaction_id" not in custody_columns()  # the model no longer maps it

    _run(engine, _DROP_COLUMN.downgrade)
    try:
        restored = custody_columns()["transaction_id"]
        assert restored["nullable"] is True
        assert restored["comment"] == "Owned by the sales context (Transaction). No DB-level FK — reconciled nightly."
    finally:
        _run(engine, _DROP_COLUMN.upgrade)
    assert "transaction_id" not in custody_columns()
