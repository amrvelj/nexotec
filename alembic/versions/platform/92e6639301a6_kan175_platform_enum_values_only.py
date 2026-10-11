"""platform enum columns hold .value only, with a CHECK each (KAN-175, KAN-86 step 3)

KAN-86 step 2 rewrote every enum column from the member NAME to `.value`;
step-1 instances still serving during that deploy kept writing names. This
migration finishes the move (app/core/enum_type.py):

- The same idempotent NAME -> value rewrite as step 2 (one UPDATE per
  table, counted and printed), for every enum column of the platform
  context at this revision. A second run changes nothing.
- A stored string that is neither a member's name nor its value stops the
  upgrade with a report naming table, column, string and row count — the
  CHECK below could not be created over it, and guessing a member for it is
  exactly what this migration must not do. The startup migration then fails
  loudly instead of serving undecodable rows.
- Then every column gets `ck_<table>_<column>_enum`: `"<column>" IN
  (<values>)`, the constraint StoredEnum attaches to the model, so an
  unknown string can no longer be stored.
- The column lists are frozen here on purpose, not imported from the app:
  a migration must keep meaning what it meant when it ran. They were
  generated from Base.metadata; tests/test_kan175_enum_values_only.py checks
  them against the models.
- Downgrade drops the constraints; it does not turn values back into names
  (step-2 code reads values).

Revision ID: 92e6639301a6
Revises: 046ca7268483
Create Date: 2026-10-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "92e6639301a6"
down_revision: str | Sequence[str] | None = "046ca7268483"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, ((NAME, value), ...)) — every StoredEnum column of the
# platform context at this revision, every member.
_COLUMNS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("dealership", "default_correspondence_language", (
        ("DE", "de"),
        ("FR", "fr"),
        ("IT", "it"),
        ("EN", "en"),
    )),
    ("dealership", "franchise_type", (
        ("FRANCHISE", "franchise"),
        ("INDEPENDENT", "independent"),
    )),
    ("dealership", "status", (
        ("PENDING_ONBOARDING", "pending_onboarding"),
        ("ACTIVE", "active"),
        ("SUSPENDED", "suspended"),
        ("OFFBOARDED", "offboarded"),
    )),
    ("user", "employment_status", (
        ("ACTIVE", "active"),
        ("ON_LEAVE", "on_leave"),
        ("TERMINATED", "terminated"),
    )),
    ("user", "role", (
        ("SALES", "sales"),
        ("SERVICE_ADVISOR", "service_advisor"),
        ("FINANCE_MANAGER", "finance_manager"),
        ("GM", "gm"),
        ("ADMIN", "admin"),
        ("TECHNICIAN", "technician"),
        ("PARTS", "parts"),
        ("OTHER", "other"),
    )),
    ("user", "status", (
        ("INVITED", "invited"),
        ("ACTIVE", "active"),
        ("SUSPENDED", "suspended"),
        ("DEACTIVATED", "deactivated"),
    )),
)


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


def downgrade() -> None:
    for table, column, _pairs in _COLUMNS:
        op.drop_constraint(_check_name(table, column), table, type_="check")
