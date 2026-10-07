"""integration enum columns store .value (KAN-86 step 2)

KAN-86 moves every enum column from the member NAME to `.value`
(app/core/enum_type.py). From this release on StoredEnum writes `.value`,
and this migration rewrites the rows the integration context's columns still
hold in name form. Step-1 code still running during the deploy reads and
filters either form, so the overlap is safe; it writes names, which
KAN-175 sweeps.

The integration_connection scope/tenant CHECK is widened to accept both
forms first (step-1 instances still write 'PLATFORM' / 'TENANT' during the
deploy); KAN-175 narrows it to the values. Replacing it takes an ACCESS
EXCLUSIVE lock on integration_connection — reads included, so every
gateway lookup waits — held until the startup upgrade commits.

- The column lists below are frozen here on purpose, not imported from the
  app: a migration must keep meaning what it meant when it ran. They were
  generated from Base.metadata; tests/test_kan86_value_migrations.py checks
  them against the models.
- Only rows holding a member's name are touched, so a second run changes
  nothing. A string that is neither a member's name nor its value is left
  as it is and reported; StoredEnum still refuses to read it. (KAN-54's
  preferred_channel MESSAGE is a member: its meaning is undecided, its
  storage is not, so 'MESSAGE' becomes 'message' like any other name.)
- One UPDATE per table, so each row is rewritten once. It runs inside the
  startup migration transaction (KAN-92's lock): every rewritten row stays
  locked until `alembic upgrade heads` commits.
- Downgrade turns every value back into its name — also rows that held the
  value before the upgrade (KAN-91's consent_source), so it is not an exact
  inverse; step-1 code reads both either way.

Revision ID: 75d35e49f1ca
Revises: 2b6e4f8a9c1d
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "75d35e49f1ca"
down_revision: Union[str, Sequence[str], None] = "2b6e4f8a9c1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, ((NAME, value), ...)) — every StoredEnum column of the
# integration context at this revision, every member whose name and value differ.
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


def _rewrite(conn: sa.engine.Connection, *, up: bool) -> None:
    """NAME -> value (up) or value -> NAME (down): one UPDATE per table."""

    quote = conn.dialect.identifier_preparer.quote
    direction = "NAME -> value" if up else "value -> NAME"
    by_table: dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]] = {}
    for table, column, pairs in _COLUMNS:
        mapping = pairs if up else tuple((value, name) for name, value in pairs)
        by_table.setdefault(table, []).append((column, mapping))

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
        olds_only = {k: v for k, v in params.items() if k.startswith("o")}
        counts = conn.execute(
            sa.text(f"SELECT {', '.join(f'COUNT(*) FILTER (WHERE {m})' for m in matches)} FROM {tbl}"), olds_only
        ).one()
        result = conn.execute(
            sa.text(f"UPDATE {tbl} SET {', '.join(sets)} WHERE {' OR '.join(matches)}"), params
        )
        for (column, _), count in zip(columns, counts):
            print(f"KAN-86: {table}.{column} {direction}: {count} row(s)")
        print(f"KAN-86: {table}: {result.rowcount} row(s) rewritten")

    for table, column, pairs in _COLUMNS:
        col, tbl = quote(column), quote(table)
        known = {f"k{i}": form for i, form in enumerate(sorted({f for pair in pairs for f in pair}))}
        unknown = conn.execute(
            sa.text(
                f"SELECT {col}, COUNT(*) FROM {tbl} WHERE {col} IS NOT NULL "
                f"AND {col} NOT IN ({', '.join(':' + k for k in known)}) GROUP BY {col}"
            ),
            known,
        ).all()
        for stored, count in unknown:
            print(
                f"KAN-86 REPORT: {table}.{column} holds {stored!r} in {count} row(s), "
                "neither a name nor a value; left unchanged"
            )


_CHECK = "ck_integration_connection_scope_tenant_id"
_CHECK_NAMES = "(scope = 'PLATFORM' AND tenant_id IS NULL) OR (scope = 'TENANT' AND tenant_id IS NOT NULL)"
# Both forms until KAN-175: step-1 instances still running during this
# deploy keep writing 'PLATFORM' / 'TENANT'.
_CHECK_BOTH = (
    "(scope IN ('PLATFORM', 'platform') AND tenant_id IS NULL) "
    "OR (scope IN ('TENANT', 'tenant') AND tenant_id IS NOT NULL)"
)


def _replace_check(condition: str) -> None:
    op.drop_constraint(_CHECK, "integration_connection", type_="check")
    op.create_check_constraint(_CHECK, "integration_connection", condition)


def upgrade() -> None:
    _replace_check(_CHECK_BOTH)  # before the rewrite, which writes 'platform' / 'tenant'
    _rewrite(op.get_bind(), up=True)


def downgrade() -> None:
    _rewrite(op.get_bind(), up=False)  # before narrowing, so no row holds a value form
    _replace_check(_CHECK_NAMES)

