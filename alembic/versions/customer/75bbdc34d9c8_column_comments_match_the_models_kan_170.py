"""Column comments match the models (KAN-170 / KAN-251)

CLAUDE.md rule 2: a cross-context GUID column carries a comment naming its
owner. The models declare these comments; the migrations that created or
moved the columns in this chain did not (or wrote an earlier wording), so
`alembic check` reported every one of them as drift. The gate test
tests/test_migrated_schema_matches_models.py now fails on any difference,
with no allowlist — this migration sets each comment exactly as the model
declares it. Comments only: no data, type or constraint changes.

Revision ID: 75bbdc34d9c8
Revises: 50843c8247e0
Create Date: 2026-10-12 16:13:15.807606

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '75bbdc34d9c8'
down_revision: Union[str, Sequence[str], None] = '50843c8247e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, nullable, comment before this migration, comment the model declares)
_COMMENTS: tuple[tuple[str, str, bool, str | None, str], ...] = (
    ('customer', 'group_id', False, None, 'Owned by the platform context (DealerGroup). No DB-level FK.'),
    ('customer_phone', 'group_id', False, None, 'Owned by the platform context (DealerGroup). No DB-level FK.'),
    ('customer_email', 'group_id', False, None, 'Owned by the platform context (DealerGroup). No DB-level FK.'),
    ('customer_external_id', 'group_id', False, None, 'Owned by the platform context (DealerGroup). No DB-level FK.'),
    ('customer_number_sequence', 'group_id', False, None, 'Owned by the platform context (DealerGroup). No DB-level FK (PR-2, ADR-015).'),
    ('vehicle_party', 'vehicle_id', False, None, 'Owned by the vehicle context. No DB-level FK (PR-2, ADR-015) — reconciled nightly.'),
    ('vehicle_party', 'customer_id', False, None, 'Same context (customer) as this table. No DB-level FK (PR-2, ADR-015) — named explicitly in scope.'),
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
