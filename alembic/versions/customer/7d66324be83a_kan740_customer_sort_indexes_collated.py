"""KAN-740: the customer list's surname and company-name sort indexes use the text sort collation

The customer list sorts `lastName` and `companyName` under `nexotec_text_sort`
(ADR-190; Anto, 2026-11-18: case ignored, Ä/é beside A/e, one order for every
language). An index in the database's default collation serves neither that
ORDER BY nor the keyset cursor's `>`, so every page would be a full sort. Both
indexes are rebuilt under the collation, same names, same columns:

- `ix_customer_group_id_last_name` (group_id, last_name COLLATE …)
- `ix_customer_company_name` (company_name COLLATE …)

Neither serves an exact lookup: the customer search matches both columns with
`ILIKE '%…%'`, which no b-tree serves in any collation.

`customer` is large and hot: each new index is built CONCURRENTLY under a
temporary name in an autocommit block, after a DROP INDEX CONCURRENTLY IF
EXISTS so a rerun repairs an INVALID one; the old index is dropped only once
the new one holds, then the new one takes its name (ADR-134; migrations.md).
The list keeps an index to sort on throughout. The downgrade rebuilds them in
the default collation the same way.

Needs the collation from the core chain (`f2b6d9c4a1e3`): `depends_on`.

Revision ID: 7d66324be83a
Revises: c7e2a9f4b1d3
Create Date: 2026-11-20 05:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d66324be83a"
down_revision: str | Sequence[str] | None = "c7e2a9f4b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = ("f2b6d9c4a1e3",)

# Written out rather than imported: a migration keeps the statements it ran.
_COLLATION = '"nexotec_text_sort"'
_TABLE = "customer"
# name -> (columns under the collation, columns in the default collation)
_INDEXES = {
    "ix_customer_group_id_last_name": (f"group_id, last_name COLLATE {_COLLATION}", "group_id, last_name"),
    "ix_customer_company_name": (f"company_name COLLATE {_COLLATION}", "company_name"),
}


def _rebuild(name: str, columns: str) -> None:
    building = f"{name}_kan740"
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {building}")
    op.execute(f"CREATE INDEX CONCURRENTLY {building} ON {_TABLE} ({columns})")
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"ALTER INDEX {building} RENAME TO {name}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, (collated, _plain) in _INDEXES.items():
            _rebuild(name, collated)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, (_collated, plain) in _INDEXES.items():
            _rebuild(name, plain)
