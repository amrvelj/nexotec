"""The collation text columns sort under — nexotec_text_sort (KAN-740)

Anto, 2026-11-18: one order for every UI language — case ignored, an accented
or umlauted letter beside its base letter (ICU root at strength 2,
`und-u-ks-level2`). Every sorted list names it on its text sort columns
(`app.core.text_collation`), so the order no longer depends on the
collation the database was created with.

A catalogue object only: no table, column or index changes and no row is
rewritten. The statement is the one `app.core.text_collation.
CREATE_TEXT_SORT_COLLATION` runs for a schema built from the metadata. It
needs a Postgres built with ICU (the PGDG builds are).

Revision ID: f2b6d9c4a1e3
Revises: c4e81d07a2b6
Create Date: 2026-11-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b6d9c4a1e3"
down_revision: Union[str, Sequence[str], None] = "c4e81d07a2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Written out rather than imported: a migration keeps the statement it ran.
_COLLATION = "nexotec_text_sort"


def upgrade() -> None:
    op.execute(
        f'CREATE COLLATION IF NOT EXISTS "{_COLLATION}" '
        "(provider = icu, locale = 'und-u-ks-level2', deterministic = true)"
    )


def downgrade() -> None:
    op.execute(f'DROP COLLATION IF EXISTS "{_COLLATION}"')
