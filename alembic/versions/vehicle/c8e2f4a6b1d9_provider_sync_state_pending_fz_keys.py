"""KAN-78 — `vehicle_provider_sync_state.pending_fz_keys`.

A refused Datenname for one FzKey used to abort the whole catalogue sync
run before any sync-state write. The sync now isolates each FzKey and
records the ones it could not sync here, so an operator can see them on
the sync-status board and the next delta retries them even after the
cursor has moved past the day they failed. Existing rows start with an
empty list: no run before this revision recorded per-key failures.

Revision ID: c8e2f4a6b1d9
Revises: a9d4c2e7f1b3
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2f4a6b1d9"
down_revision: Union[str, Sequence[str], None] = "a9d4c2e7f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vehicle_provider_sync_state",
        sa.Column("pending_fz_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("vehicle_provider_sync_state", "pending_fz_keys")
