"""sales enum columns store .value (KAN-86 step 2)

KAN-86 moves every enum column from the member NAME to `.value`
(app/core/enum_type.py). From this release on StoredEnum writes `.value`,
and this migration rewrites the rows the sales context's columns still
hold in name form. Step-1 code still running during the deploy reads and
filters either form, so the overlap is safe; it writes names, which
KAN-175 sweeps.

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

Revision ID: 63b0d0b20fab
Revises: d652c2e3627a
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "63b0d0b20fab"
down_revision: Union[str, Sequence[str], None] = "d652c2e3627a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, ((NAME, value), ...)) — every StoredEnum column of the
# sales context at this revision, every member whose name and value differ.
_COLUMNS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("sales_contract", "financing", (
        ("CASH", "cash"),
        ("LEASING", "leasing"),
        ("CREDIT", "credit"),
    )),
    ("sales_contract", "status", (
        ("PENDING", "pending"),
        ("CONFIRMED", "confirmed"),
        ("CANCELLED", "cancelled"),
        ("INVOICED", "invoiced"),
    )),
    ("sales_document", "owner_type", (
        ("OFFER", "offer"),
        ("CONTRACT", "contract"),
    )),
    ("sales_line_item", "kind", (
        ("FACTORY_OPTION", "factory_option"),
        ("ACCESSORY", "accessory"),
    )),
    ("sales_offer", "status", (
        ("DRAFT", "draft"),
        ("OPEN", "open"),
        ("CANCELLED", "cancelled"),
    )),
    ("transaction", "status", (
        ("DRAFT", "draft"),
        ("COMPLETED", "completed"),
        ("CANCELLED", "cancelled"),
    )),
    ("transaction", "transaction_type", (
        ("SALE", "sale"),
        ("TRADE_IN", "trade_in"),
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


def upgrade() -> None:
    _rewrite(op.get_bind(), up=True)


def downgrade() -> None:
    _rewrite(op.get_bind(), up=False)

