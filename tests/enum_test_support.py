"""Helpers for the tests of the enum-storage migrations (KAN-86, KAN-175).

Since KAN-175 every StoredEnum column carries `ck_{table}_{column}_enum`, so
the schema the tests build cannot hold a member NAME or an unknown string.
The tests of the older migrations (KAN-60, KAN-86 step 2) and KAN-175's own
migration tests seed exactly such rows, as a database from before KAN-175
holds them: they drop those constraints first. Dropping a constraint is DDL,
so tests/conftest.py rebuilds the shared schema before the next test.
"""

import uuid

from sqlalchemy import text

from app.core.auth import AccessRole, create_access_token
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

# The current schema's constraints only: a test database may hold a second
# copy of the tables in another schema, with the same names (KAN-298).
_IN_CURRENT_SCHEMA = "connamespace = current_schema()::regnamespace"
_ENUM_CHECKS = (
    "SELECT (SELECT relname FROM pg_class WHERE oid = conrelid), conname, pg_get_constraintdef(oid) "
    f"FROM pg_constraint WHERE {_IN_CURRENT_SCHEMA} AND contype = 'c' AND conname LIKE 'ck\\_%\\_enum'"
)


def enum_checks(conn) -> dict[tuple[str, str], str]:
    """Every `ck_*_enum` constraint: (table, name) -> definition."""

    return {(table, name): definition for table, name, definition in conn.execute(text(_ENUM_CHECKS))}


def scope_check_definition(conn) -> str:
    """ck_integration_connection_scope_tenant_id, as Postgres prints it."""

    return conn.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            f"WHERE {_IN_CURRENT_SCHEMA} AND conname = 'ck_integration_connection_scope_tenant_id'"
        )
    ).scalar_one()


def drop_enum_checks(conn) -> int:
    """Drops every `ck_*_enum` constraint (a database from before KAN-175);
    returns how many."""

    checks = enum_checks(conn)
    for table, name in checks:
        conn.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"'))
    return len(checks)


def widen_connection_scope_check(conn) -> None:
    """ck_integration_connection_scope_tenant_id as KAN-86 step 2 left it —
    both forms — which KAN-175 narrowed to the values."""

    conn.execute(text("ALTER TABLE integration_connection DROP CONSTRAINT ck_integration_connection_scope_tenant_id"))
    conn.execute(
        text(
            "ALTER TABLE integration_connection ADD CONSTRAINT ck_integration_connection_scope_tenant_id CHECK ("
            "(scope IN ('PLATFORM', 'platform') AND tenant_id IS NULL) "
            "OR (scope IN ('TENANT', 'tenant') AND tenant_id IS NOT NULL))"
        )
    )


def _token(tenant_id: uuid.UUID, role: AccessRole | None = None, *, is_dealer_manager: bool = False) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealership(client) -> uuid.UUID:
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent", "phone": "+41441234567",
        "uid": valid_uid(),
        "address": {
            "street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"
        },
    }
    response = client.post(
        "/v1/dealerships", json=payload, headers=_bearer(_token(uuid.uuid4(), AccessRole.PLATFORM_ADMIN))
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _create_customer(client, tenant_id: uuid.UUID, first_name: str) -> dict:
    payload = {
        "firstName": first_name, "lastName": "Muster", "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"{first_name.lower()}-{uuid.uuid4().hex[:6]}@example.ch"}],
    }
    response = client.post(
        "/v1/customers", json=payload, headers=_bearer(_token(tenant_id, is_dealer_manager=True))
    )
    assert response.status_code == 201, response.text
    return response.json()


def _to_name_form(db_session, customer_id: str) -> None:
    """Puts one customer and its email row back into the member-NAME form —
    what a step-1 instance wrote during KAN-86 step 2's deploy. Every value is
    the lower-cased name, so upper() is the name. Drops the enum CHECKs first
    (a database from before KAN-175)."""

    drop_enum_checks(db_session.connection())
    db_session.execute(
        text(
            "UPDATE customer SET customer_type = upper(customer_type), language = upper(language), "
            "lifecycle_status = upper(lifecycle_status), gender = upper(gender) WHERE id = :id"
        ),
        {"id": customer_id},
    )
    db_session.execute(
        text(
            "UPDATE customer_email SET email_type = upper(email_type), consent_source = upper(consent_source), "
            "consent_scope = upper(consent_scope) WHERE customer_id = :id"
        ),
        {"id": customer_id},
    )
    db_session.commit()


# KAN-79 retired the legacy `vehicle` table and its `vehicle_custody_event`,
# KAN-555 Sales' legacy `transaction` table: no model maps them, so the test
# schema lacks them, yet the frozen enum migrations (KAN-86's e1b483faad5c and
# 63b0d0b20fab, KAN-175's 0ba433107313 and bfbe941673c3) still read and rewrite
# them. Their tests recreate all three, with one row each, in the shape the
# dropping migrations' own downgrades recreate them (their DDL, run here), and
# drop them again afterwards (`drop_retired_legacy_tables`): the schema rebuild
# after a DDL test drops only the tables the models know.
RETIRED_LEGACY_TABLES = frozenset({"vehicle", "vehicle_custody_event", "transaction"})


def _downgrade_of(conn, chain: str, revision: str) -> None:
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions" / chain
    (path,) = sorted(versions.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"retired_legacy_tables_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        module.downgrade()


def create_retired_legacy_tables(conn) -> None:
    drop_retired_legacy_tables(conn)
    _downgrade_of(conn, "vehicle", "8d03887f7876")  # KAN-79
    _downgrade_of(conn, "sales", "4726dcd89260")  # KAN-555
    # One row each, as a database from before KAN-79 and KAN-555 held them.
    vehicle_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO vehicle (id, vin, make, model, model_year, condition, version, created_at, updated_at) "
            "VALUES (:id, 'VINKAN79LEGACY001', 'x', 'x', 2020, 'used', 1, now(), now())"
        ),
        {"id": vehicle_id},
    )
    conn.execute(
        text(
            "INSERT INTO vehicle_custody_event (id, vehicle_id, partner_id, event_type, event_date, created_at) "
            "VALUES (:id, :vehicle_id, :partner_id, 'acquired', now(), now())"
        ),
        {"id": uuid.uuid4(), "vehicle_id": vehicle_id, "partner_id": uuid.uuid4()},
    )
    conn.execute(
        text(
            'INSERT INTO "transaction" (id, tenant_id, transaction_type, status, customer_id, vehicle_id, '
            "primary_user_id, version, created_at, updated_at) "
            "VALUES (:id, :tenant_id, 'sale', 'draft', :customer_id, :vehicle_id, :user_id, 1, now(), now())"
        ),
        {
            "id": uuid.uuid4(), "tenant_id": uuid.uuid4(), "customer_id": uuid.uuid4(), "vehicle_id": vehicle_id,
            "user_id": uuid.uuid4(),
        },
    )


def drop_retired_legacy_tables(conn) -> None:
    conn.execute(text('DROP TABLE IF EXISTS vehicle_custody_event, vehicle, "transaction"'))
