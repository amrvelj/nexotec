"""integration enum columns hold .value only, with a CHECK each (KAN-175, KAN-86 step 3)

KAN-86 step 2 rewrote every enum column from the member NAME to `.value`;
step-1 instances still serving during that deploy kept writing names. This
migration finishes the move (app/core/enum_type.py):

- The same idempotent NAME -> value rewrite as step 2 (one UPDATE per
  table, counted and printed), for every enum column of the integration
  context at this revision. A second run changes nothing.
- A stored string that is neither a member's name nor its value stops the
  upgrade with a report naming table, column, string and row count — the
  CHECK below could not be created over it, and guessing a member for it is
  exactly what this migration must not do. The startup migration then fails
  loudly instead of serving undecodable rows.
- Then every column gets `ck_<table>_<column>_enum`: `"<column>" IN
  (<values>)`, the constraint StoredEnum attaches to the model, so an
  unknown string can no longer be stored.
- `ck_integration_connection_scope_tenant_id` accepts the values only (it
  accepted both forms since 75d35e49f1ca); the downgrade widens it again.

- The column lists are frozen here on purpose, not imported from the app:
  a migration must keep meaning what it meant when it ran. They were
  generated from Base.metadata; tests/test_kan175_enum_values_only.py checks
  them against the models.
- Downgrade drops the constraints; it does not turn values back into names
  (step-2 code reads values).

Revision ID: 148d12324c83
Revises: 75d35e49f1ca
Create Date: 2026-10-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "148d12324c83"
down_revision: str | Sequence[str] | None = "75d35e49f1ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, ((NAME, value), ...)) — every StoredEnum column of the
# integration context at this revision, every member.
_COLUMNS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("integration_call_log", "status", (
        ("SUCCESS", "success"),
        ("ERROR", "error"),
        ("TIMEOUT", "timeout"),
    )),
    ("integration_call_payload", "kind", (
        ("SUCCESS", "success"),
        ("ERROR", "error"),
    )),
    ("integration_connection", "environment", (
        ("SANDBOX", "sandbox"),
        ("PRODUCTION", "production"),
    )),
    ("integration_connection", "scope", (
        ("PLATFORM", "platform"),
        ("TENANT", "tenant"),
    )),
    ("integration_connection", "status", (
        ("CONNECTED", "connected"),
        ("NOT_CONFIGURED", "not_configured"),
        ("ERROR", "error"),
        ("EXPIRED", "expired"),
        ("DISABLED", "disabled"),
    )),
    ("integration_entitlement", "source", (
        ("PROBED", "probed"),
        ("DECLARED", "declared"),
    )),
    ("integration_notification", "kind", (
        ("EXPIRY_WARNING", "expiry_warning"),
        ("BREAK_GLASS_ACCESS", "break_glass_access"),
        ("SUPPORT_DIGEST", "support_digest"),
    )),
    ("integration_secret_ref", "slot", (
        ("PASSWORD", "password"),
        ("AES_KEY", "aes_key"),
        ("CLIENT_SECRET", "client_secret"),
        ("REFRESH_TOKEN", "refresh_token"),
        ("CERTIFICATE", "certificate"),
    )),
)


_SCOPE_CHECK = "ck_integration_connection_scope_tenant_id"
_SCOPE_VALUES = "(scope = 'platform' AND tenant_id IS NULL) OR (scope = 'tenant' AND tenant_id IS NOT NULL)"
_SCOPE_BOTH = (
    "(scope IN ('PLATFORM', 'platform') AND tenant_id IS NULL) "
    "OR (scope IN ('TENANT', 'tenant') AND tenant_id IS NOT NULL)"
)


def _replace_scope_check(condition: str) -> None:
    op.drop_constraint(_SCOPE_CHECK, "integration_connection", type_="check")
    op.create_check_constraint(_SCOPE_CHECK, "integration_connection", condition)


def _check_name(table: str, column: str) -> str:
    return f"ck_{table}_{column}_enum"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _rewrite_names(conn: sa.engine.Connection) -> None:
    """NAME -> value for every member whose name and value differ."""

    quote = conn.dialect.identifier_preparer.quote
    by_table: dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]] = {}
    for table, column, pairs in _COLUMNS:
        renames = tuple((name, value) for name, value in pairs if name != value)
        if renames:
            by_table.setdefault(table, []).append((column, renames))
    for table, columns in by_table.items():
        tbl = quote(table)
        params: dict[str, str] = {}
        sets, matches = [], []
        for c, (column, mapping) in enumerate(columns):
            col = quote(column)
            whens, olds = [], []
            for i, (old, new) in enumerate(mapping):
                params[f"o{c}_{i}"], params[f"n{c}_{i}"] = old, new
                whens.append(f"WHEN :o{c}_{i} THEN :n{c}_{i}")
                olds.append(f":o{c}_{i}")
            sets.append(f"{col} = CASE {col} {' '.join(whens)} ELSE {col} END")
            matches.append(f"{col} IN ({', '.join(olds)})")
        result = conn.execute(sa.text(f"UPDATE {tbl} SET {', '.join(sets)} WHERE {' OR '.join(matches)}"), params)
        print(f"KAN-175: {table}: {result.rowcount} row(s) rewritten NAME -> value")


def _refuse_unknown(conn: sa.engine.Connection) -> None:
    """Every stored string is a value now; anything else stops the upgrade."""

    quote = conn.dialect.identifier_preparer.quote
    unknown: list[str] = []
    for table, column, pairs in _COLUMNS:
        values = [value for _name, value in pairs]
        params = {f"v{i}": value for i, value in enumerate(values)}
        rows = conn.execute(
            sa.text(
                f"SELECT {quote(column)}, COUNT(*) FROM {quote(table)} WHERE {quote(column)} IS NOT NULL "
                f"AND {quote(column)} NOT IN ({', '.join(':' + key for key in params)}) GROUP BY {quote(column)}"
            ),
            params,
        ).all()
        unknown += [f"{table}.{column} = {stored!r} in {count} row(s)" for stored, count in rows]
    if unknown:
        raise RuntimeError(
            "KAN-175: stored strings that are neither a member's name nor its value — decide what each one "
            "means, then re-run: " + "; ".join(unknown)
        )


def upgrade() -> None:
    conn = op.get_bind()
    _rewrite_names(conn)
    _refuse_unknown(conn)
    for table, column, pairs in _COLUMNS:
        condition = f'"{column}" IN ({", ".join(_quote(value) for _name, value in pairs)})'
        op.create_check_constraint(_check_name(table, column), table, condition)
    _replace_scope_check(_SCOPE_VALUES)


def downgrade() -> None:
    _replace_scope_check(_SCOPE_BOTH)
    for table, column, _pairs in _COLUMNS:
        op.drop_constraint(_check_name(table, column), table, type_="check")
