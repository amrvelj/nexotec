"""KAN-79 (Anto, 2026-11-02): migration `8d03887f7876` drops the legacy
`vehicle` table and its `vehicle_custody_event`; its downgrade recreates both
empty in the exact shape they had at `63bb7ea0e61c`, because the older
migrations' downgrades (`0ba433107313`'s enum CHECKs, `e1b483faad5c`,
`22708a77f565`'s foreign keys) run over those names in CI's round trip.

The models are gone, so the test schema has neither table: the downgrade is
run first (it creates them), its catalogue compared with the shape the
`pg_dump` of a database at `63bb7ea0e61c` showed, then the upgrade (gone).
"""

import importlib.util
import os
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "vehicle"
_postgres_only = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _load():
    (path,) = sorted(_VERSIONS.glob("8d03887f7876_*.py"))
    spec = importlib.util.spec_from_file_location("kan79_retire_legacy_vehicle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load()
_TABLES = ("vehicle", "vehicle_custody_event")


def _run(engine, step) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        step()


def _tables(engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) & set(_TABLES)


def _shape(engine) -> dict:
    with engine.connect() as conn:
        columns = conn.execute(
            sa.text(
                "SELECT table_name, column_name, data_type, character_maximum_length, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name IN ('vehicle', 'vehicle_custody_event') "
                "ORDER BY table_name, ordinal_position"
            )
        ).all()
        constraints = conn.execute(
            sa.text(
                "SELECT conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid::regclass::text IN ('vehicle', 'vehicle_custody_event') ORDER BY 1, 2"
            )
        ).all()
        indexes = conn.execute(
            sa.text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE tablename IN ('vehicle', 'vehicle_custody_event') ORDER BY 1, 2"
            )
        ).all()
        comments = conn.execute(
            sa.text(
                "SELECT c.relname, a.attname, col_description(c.oid, a.attnum) FROM pg_class c "
                "JOIN pg_attribute a ON a.attrelid = c.oid "
                "WHERE c.relname IN ('vehicle', 'vehicle_custody_event') AND col_description(c.oid, a.attnum) "
                "IS NOT NULL ORDER BY 1, 2"
            )
        ).all()
    return {"columns": columns, "constraints": constraints, "indexes": indexes, "comments": comments}


@_postgres_only
def test_the_downgrade_recreates_both_tables_in_their_old_shape_and_the_upgrade_drops_them(engine):
    assert _tables(engine) == set()  # no model maps them any more

    _run(engine, _MIGRATION.downgrade)
    shape = _shape(engine)

    vehicle_columns = [c[1] for c in shape["columns"] if c[0] == "vehicle"]
    assert vehicle_columns == [
        "id", "vin", "make", "model", "model_year", "trim", "engine", "condition", "vehicle_type", "fuel_type",
        "body_style", "drivetrain", "transmission", "exterior_color", "interior_color",
        "energy_efficiency_category", "co2_emissions_gkm", "odometer", "registration_status",
        "registration_canton", "status", "current_custodian_partner_id", "version", "created_at", "updated_at",
        "created_by", "updated_by",
    ]
    custody_columns = [c[1] for c in shape["columns"] if c[0] == "vehicle_custody_event"]
    assert custody_columns == [
        "id", "vehicle_id", "partner_id", "event_type", "event_date", "transaction_id", "created_at", "created_by",
    ]
    defaults = {(c[0], c[1]): c[5] for c in shape["columns"] if c[5] is not None}
    assert defaults == {
        ("vehicle", "registration_status"): "'unregistered'::character varying",
        ("vehicle", "status"): "'in_transit'::character varying",
        ("vehicle", "version"): "1",
    }
    assert {(c[0], c[1], c[2]) for c in shape["constraints"]} == {
        ("vehicle", "vehicle_pkey", "p"),
        ("vehicle", "uq_vehicle_vin", "u"),
        ("vehicle", "ck_vehicle_condition_enum", "c"),
        ("vehicle", "ck_vehicle_registration_status_enum", "c"),
        ("vehicle", "ck_vehicle_status_enum", "c"),
        ("vehicle_custody_event", "vehicle_custody_event_pkey", "p"),
        ("vehicle_custody_event", "fk_vehicle_custody_event_vehicle_id_vehicle", "f"),
        ("vehicle_custody_event", "ck_vehicle_custody_event_event_type_enum", "c"),
    }
    checks = {c[1]: c[3] for c in shape["constraints"] if c[2] == "c"}
    assert "'certified_pre_owned'" in checks["ck_vehicle_condition_enum"]
    assert "'scrapped'" in checks["ck_vehicle_status_enum"]
    assert "'repossessed'" in checks["ck_vehicle_custody_event_event_type_enum"]
    assert {i[1] for i in shape["indexes"]} == {
        "vehicle_pkey", "uq_vehicle_vin", "ix_vehicle_vin", "vehicle_custody_event_pkey",
        "ix_vehicle_custody_event_vehicle_id", "ix_vehicle_custody_event_partner_id",
        "ix_vehicle_custody_event_vehicle_id_partner_id",
    }
    assert {(c[0], c[1]) for c in shape["comments"]} == {
        ("vehicle", "current_custodian_partner_id"),
        ("vehicle_custody_event", "partner_id"),
        ("vehicle_custody_event", "transaction_id"),
    }
    assert all("No DB-level FK (PR-2, ADR-015) — reconciled nightly." in c[2] for c in shape["comments"])

    _run(engine, _MIGRATION.upgrade)
    assert _tables(engine) == set()
