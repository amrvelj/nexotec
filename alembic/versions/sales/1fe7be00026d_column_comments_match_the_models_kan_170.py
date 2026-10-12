"""Column comments match the models (KAN-170 / KAN-251)

CLAUDE.md rule 2: a cross-context GUID column carries a comment naming its
owner. The models declare these comments; the migrations that created or
moved the columns in this chain did not (or wrote an earlier wording), so
`alembic check` reported every one of them as drift. The gate test
tests/test_migrated_schema_matches_models.py now fails on any difference,
with no allowlist — this migration sets each comment exactly as the model
declares it. Comments only: no data, type or constraint changes.

Revision ID: 1fe7be00026d
Revises: bfbe941673c3
Create Date: 2026-10-12 16:13:18.438151

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1fe7be00026d'
down_revision: Union[str, Sequence[str], None] = 'bfbe941673c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, nullable, comment before this migration, comment the model declares)
_COMMENTS: tuple[tuple[str, str, bool, str | None, str], ...] = (
    ('transaction', 'tenant_id', False, None, 'Owned by the platform context (Dealership). No DB-level FK.'),
    ('transaction', 'customer_id', False, None, 'Owned by the customer context. No DB-level FK (PR-2, ADR-015).'),
    ('transaction', 'vehicle_id', False, None, 'Owned by the vehicle context. No DB-level FK (PR-2, ADR-015).'),
    ('transaction', 'primary_user_id', False, None, 'Owned by the platform context (User). No DB-level FK.'),
)


def _set_comments(forward: bool) -> None:
    for table, column, nullable, previous, declared in _COMMENTS:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.UUID(as_uuid=True),
            comment=declared if forward else previous,
            existing_comment=previous if forward else declared,
            existing_nullable=nullable,
        )


def upgrade() -> None:
    _set_comments(forward=True)


def downgrade() -> None:
    _set_comments(forward=False)
