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
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
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
