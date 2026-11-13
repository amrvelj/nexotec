"""KAN-555: vehicle_mdm_custody_event.transaction_id goes with the legacy transaction table

The column named a row of Sales' legacy `transaction` table (a plain GUID, no
foreign key since PR-2), which the sales chain's `4726dcd89260` drops. Nothing
writes it; its one reader was Vehicle's nightly reference check, removed in
the same change.

**Data.** The column's values go with it: they named rows that no longer
exist.

**Downgrade.** Re-adds the column, nullable, with the owner comment it had;
the values are not restored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.types import GUID

# revision identifiers, used by Alembic.
revision: str = "778849af53d8"
down_revision: str | Sequence[str] | None = "01f4dda679ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT = "Owned by the sales context (Transaction). No DB-level FK — reconciled nightly."


def upgrade() -> None:
    with op.batch_alter_table("vehicle_mdm_custody_event") as batch:
        batch.drop_column("transaction_id")


def downgrade() -> None:
    with op.batch_alter_table("vehicle_mdm_custody_event") as batch:
        batch.add_column(sa.Column("transaction_id", GUID(), nullable=True, comment=_COMMENT))
