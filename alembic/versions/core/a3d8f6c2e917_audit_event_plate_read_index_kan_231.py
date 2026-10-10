"""audit_event partial index for the per-user plate-read limit (KAN-231)

Every plate read (the Plates tab, a resolved search hit) counts the reading
user's distinct vehicles over the rolling window from audit_event itself
(app/vehicle/services/plate_read_guard.py). Without an index that count
scans every audit row of the entity type, a table nothing purges.

Partial (entity_type = 'vehicle_plate_history'): only plate-read rows are
indexed, so every other audit write pays nothing.

Inside the migration transaction, deliberately not CONCURRENTLY — KAN-92's
advisory lock (alembic/env.py) is transaction-scoped and CONCURRENTLY would
release it mid-upgrade (see e7a3c9f1b5d2). The cost: CREATE INDEX holds a
SHARE lock on audit_event for the build, so audit writes wait that long.
On deploy the index starts empty: no plate-read rows exist before this PR.

Revision ID: a3d8f6c2e917
Revises: 5c5236cacf7e
Create Date: 2026-10-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3d8f6c2e917'
down_revision: Union[str, Sequence[str], None] = '5c5236cacf7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_audit_event_plate_read_actor_created"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "audit_event",
        ["actor_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("entity_type = 'vehicle_plate_history'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="audit_event")
