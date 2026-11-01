"""KAN-497 — drop `vehicle_provider_sync_state`.

The table was the full seed's and the daily delta's bookkeeping (watermark,
cursor, pending FzKeys) and fed the platform's catalogue-sync status board.
KAN-485 removed both runs (ADR-023 as amended 2026-10-29: single lookups
only), so nothing has written it since; the board could only show a stale
alarm for a sync that no longer exists. Anto, 2026-10-31 (KAN-497 option A,
the migration approved): retire the board, the endpoint and the table. The
rows hold a few watermarks per Betrieb from the old seed, nothing a person
needs.

The downgrade recreates the table empty, in its last shape: the dropped rows
are not restored.

Revision ID: e4b7c2a91d36
Revises: b0a125a0a583
Create Date: 2026-11-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e4b7c2a91d36"
down_revision: Union[str, Sequence[str], None] = "b0a125a0a583"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("vehicle_provider_sync_state")


def downgrade() -> None:
    op.create_table(
        "vehicle_provider_sync_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("last_full_seed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delta_cursor", sa.Date(), nullable=True),
        sa.Column("last_system_watermark_date", sa.Date(), nullable=True),
        sa.Column("last_system_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_fz_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_provider_sync_state_tenant_id", "vehicle_provider_sync_state", ["tenant_id"])
    op.create_unique_constraint(
        "uq_vehicle_provider_sync_state_tenant_provider",
        "vehicle_provider_sync_state",
        ["tenant_id", "provider_code"],
    )
