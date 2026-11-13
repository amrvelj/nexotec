"""KAN-540 (Anto, 2026-11-13: «we have currently no productive data,
therefore it can be dropped»): sales migration `976ea5f43dc0` deletes Sales'
retired `offer` counter rows from `sales_number_sequence` and leaves the
`contract` series, which still numbers contracts. Its downgrade is a no-op.

Tests build the schema with ``create_all``, never Alembic, so the migration is
loaded by path and its own `upgrade()` / `downgrade()` run against the test
schema (the real `alembic upgrade heads` run is `scripts/dev/check`'s
migration lanes).
"""

import importlib.util
import os
import pathlib
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "sales"
_postgres_only = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _load():
    (path,) = sorted(_VERSIONS.glob("976ea5f43dc0_*.py"))
    spec = importlib.util.spec_from_file_location("kan540_retire_sales_offer_counter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load()


def _run(engine, step) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        step()


def _rows(engine) -> set[tuple[uuid.UUID, str, int]]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT tenant_id, series, next_value FROM sales_number_sequence")).all()
    return {(uuid.UUID(str(tenant)), series, value) for tenant, series, value in rows}


@_postgres_only
def test_the_upgrade_deletes_the_offer_counters_and_keeps_the_contract_counters(engine):
    one, two = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO sales_number_sequence (tenant_id, series, next_value) VALUES (:t, :s, :v)"),
            [
                {"t": one, "s": "offer", "v": 124},
                {"t": one, "s": "contract", "v": 46},
                {"t": two, "s": "offer", "v": 1},
                {"t": two, "s": "contract", "v": 3},
            ],
        )

    _run(engine, _MIGRATION.upgrade)
    assert _rows(engine) == {(one, "contract", 46), (two, "contract", 3)}

    _run(engine, _MIGRATION.upgrade)  # a rerun changes nothing
    assert _rows(engine) == {(one, "contract", 46), (two, "contract", 3)}

    _run(engine, _MIGRATION.downgrade)  # deliberately a no-op
    assert _rows(engine) == {(one, "contract", 46), (two, "contract", 3)}

