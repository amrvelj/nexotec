"""audit_event.acting_dealership_id — the Betrieb a write was made in (KAN-722)

A group-owned customer's audit rows carry the group as `tenant_id`, so
nothing recorded which Betrieb wrote them. KAN-722 hides an offboarded
Betrieb's entries from its sisters' History tab and puts exactly those
entries in its archive export; from this revision on, the customer
services record the acting Betrieb on each row. Older rows stay NULL:
`app.core.audit_attribution` attributes them by their author (Anto,
2026-11-15, answer A).

Nullable, no default, no backfill: adding it is a catalogue change only and
rewrites no row. No index — the column is read only after the
(entity_type, entity_id) lookup or the export's tenant scan narrowed the rows.

Revision ID: c4e81d07a2b6
Revises: 90ac3e5a8b9a
Create Date: 2026-11-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4e81d07a2b6"
down_revision: Union[str, Sequence[str], None] = "90ac3e5a8b9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_event", sa.Column("acting_dealership_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_event", "acting_dealership_id")
