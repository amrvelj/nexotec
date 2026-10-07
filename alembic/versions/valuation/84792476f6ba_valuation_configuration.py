"""KAN-10 (C-F) — a valuation references the configuration it was captured
from (FR-C-14, amending FR-V-17; ADR-070). Nullable, no backfill: no
existing valuation was captured in the configurator. Three-column pattern,
no FK into the vehicle context (rule 2).

Revision ID: 84792476f6ba
Revises: eb808ea3562c
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "84792476f6ba"
down_revision: Union[str, Sequence[str], None] = "eb808ea3562c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "valuation",
        sa.Column(
            "configuration_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the vehicle context (VehicleConfiguration.id). No DB-level FK.",
        ),
    )
    op.add_column("valuation", sa.Column("configuration_label", sa.String(200), nullable=True))
    op.add_column("valuation", sa.Column("configuration_label_refreshed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_valuation_configuration_id", "valuation", ["configuration_id"])


def downgrade() -> None:
    op.drop_index("ix_valuation_configuration_id", table_name="valuation")
    op.drop_column("valuation", "configuration_label_refreshed_at")
    op.drop_column("valuation", "configuration_label")
    op.drop_column("valuation", "configuration_id")
