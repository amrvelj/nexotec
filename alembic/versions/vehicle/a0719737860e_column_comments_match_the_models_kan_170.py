"""Column comments match the models (KAN-170 / KAN-251)

CLAUDE.md rule 2: a cross-context GUID column carries a comment naming its
owner. The models declare these comments; the migrations that created or
moved the columns in this chain did not (or wrote an earlier wording), so
`alembic check` reported every one of them as drift. The gate test
tests/test_migrated_schema_matches_models.py now fails on any difference,
with no allowlist — this migration sets each comment exactly as the model
declares it. Comments only: no data, type or constraint changes.

Revision ID: a0719737860e
Revises: 91384410f98e
Create Date: 2026-10-12 16:13:21.158836

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a0719737860e'
down_revision: Union[str, Sequence[str], None] = '91384410f98e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, nullable, comment before this migration, comment the model declares)
_COMMENTS: tuple[tuple[str, str, bool, str | None, str], ...] = (
    ('vehicle', 'current_custodian_partner_id', True, None, 'Owned by the platform context (Dealership). No DB-level FK (PR-2, ADR-015) — reconciled nightly.'),
    ('vehicle_custody_event', 'partner_id', False, None, 'Owned by the platform context (Dealership). No DB-level FK (PR-2, ADR-015) — reconciled nightly.'),
    ('vehicle_custody_event', 'transaction_id', True, None, 'Owned by the sales context (Transaction). No DB-level FK (PR-2, ADR-015) — reconciled nightly.'),
    ('vehicle_odometer_reading', 'recording_tenant_id', False, 'Owned by the platform context (Dealership). No DB-level FK.', 'Owned by the platform context (Dealership). No DB-level FK — reconciled nightly.'),
    ('vehicle_accessory', 'recording_tenant_id', False, 'Owned by the platform context (Dealership). No DB-level FK.', 'Owned by the platform context (Dealership). No DB-level FK — reconciled nightly.'),
    ('vehicle_mdm_custody_event', 'partner_id', False, 'Owned by the platform context (Dealership). No DB-level FK.', 'Owned by the platform context (Dealership). No DB-level FK — reconciled nightly.'),
    ('vehicle_mdm_custody_event', 'transaction_id', True, 'Owned by the sales context (Transaction). No DB-level FK.', 'Owned by the sales context (Transaction). No DB-level FK — reconciled nightly.'),
    ('vehicle_plate', 'recording_tenant_id', False, 'Owned by the platform context (Dealership). No DB-level FK.', 'Owned by the platform context (Dealership). No DB-level FK — reconciled nightly.'),
    ('vehicle_dealer_plate_assignment', 'tenant_id', False, None, 'Denormalized from DealerPlate.tenant_id at insert time — same reasoning as CustomerPhone.group_id.'),
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
