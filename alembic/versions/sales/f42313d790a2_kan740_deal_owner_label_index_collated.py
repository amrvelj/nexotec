"""KAN-740: the deal list's seller sort index uses the text sort collation

The deal list sorts `ownerLabel` (the responsible seller's name, KAN-200)
under `nexotec_text_sort` (ADR-190; Anto, 2026-11-18). An index in the
database's default collation serves neither that ORDER BY nor the keyset
cursor's `>`, so `ix_sales_deal_owner_label` is rebuilt under the collation,
same name, same column. Nothing looks a deal up by its seller's label.

`ix_sales_deal_stock_item_label` stays as it is: it holds a stock number, an
identifier that keeps byte order (`IDENTIFIER_TEXT`, ADR-190).

`sales_deal` is hot: the new index is built CONCURRENTLY under a temporary
name in an autocommit block, after a DROP INDEX CONCURRENTLY IF EXISTS so a
rerun repairs an INVALID one; the old one is dropped only once the new one
holds, then the new one takes its name (ADR-134; migrations.md). The downgrade
rebuilds it in the default collation the same way.

The collation is the core chain's (`f2b6d9c4a1e3`); this revision creates it too, with the
same `IF NOT EXISTS` statement, rather than `depends_on` that revision: a dependency on the core
chain's head makes it an «effective head» that `alembic_version` never holds, and nothing then
says in which order the chains run. Whichever runs first creates it.

Revision ID: f42313d790a2
Revises: a7c3e9d1f5b2
Create Date: 2026-11-20 05:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f42313d790a2"
down_revision: str | Sequence[str] | None = "a7c3e9d1f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Written out rather than imported: a migration keeps the statements it ran.
_COLLATION = '"nexotec_text_sort"'
_CREATE_COLLATION = (
    f"CREATE COLLATION IF NOT EXISTS {_COLLATION} (provider = icu, locale = 'und-u-ks-level2', deterministic = true)"
)
_TABLE = "sales_deal"
_INDEX = "ix_sales_deal_owner_label"


def _rebuild(columns: str) -> None:
    building = f"{_INDEX}_kan740"
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {building}")
    op.execute(f"CREATE INDEX CONCURRENTLY {building} ON {_TABLE} ({columns})")
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
    op.execute(f"ALTER INDEX {building} RENAME TO {_INDEX}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_CREATE_COLLATION)
        _rebuild(f"owner_label COLLATE {_COLLATION}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _rebuild("owner_label")
