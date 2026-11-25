"""KAN-819: the index behind the contact search by UID, built concurrently

`contact` is the group's whole register and read on every search, so the index
is built `CONCURRENTLY` in a migration of its own (migrations.md): a failure
leaves an INVALID index, which the leading `DROP INDEX CONCURRENTLY IF EXISTS`
repairs on a rerun.

Revision ID: b1d5f8a3c6e9
Revises: 9e4b7c2d1a58
Create Date: 2026-11-25 08:05:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1d5f8a3c6e9"
down_revision: str | Sequence[str] | None = "9e4b7c2d1a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_contact_tax_id_search_key"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.execute(f"CREATE INDEX CONCURRENTLY {_INDEX} ON contact (tax_id_search_key)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
