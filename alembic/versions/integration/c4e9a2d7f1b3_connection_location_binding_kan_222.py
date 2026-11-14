"""a connection names the location it serves (KAN-222, FR-A-21)

`integration_connection.location_id` with its label copy, rule 2's
three-column pattern: the platform context's `location.id` (no foreign key,
an owner comment instead), `location_label` as copied when the connection was
bound, and `location_label_refreshed_at`, stamped by the nightly
`integration.location_labels` job. All nullable: an unbound connection serves
the whole Betrieb, as every existing row does.

Revision ID: c4e9a2d7f1b3
Revises: b1f58e30a656
Create Date: 2026-11-14 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.types import GUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "c4e9a2d7f1b3"
down_revision: Union[str, Sequence[str], None] = "b1f58e30a656"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration_connection",
        sa.Column(
            "location_id",
            GUID(),
            nullable=True,
            comment="Owned by the platform context (Location.id). No DB-level FK.",
        ),
    )
    op.add_column("integration_connection", sa.Column("location_label", sa.String(length=200), nullable=True))
    op.add_column("integration_connection", sa.Column("location_label_refreshed_at", UTCDateTime(), nullable=True))
    op.create_index(
        op.f("ix_integration_connection_location_id"), "integration_connection", ["location_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_integration_connection_location_id"), table_name="integration_connection")
    op.drop_column("integration_connection", "location_label_refreshed_at")
    op.drop_column("integration_connection", "location_label")
    op.drop_column("integration_connection", "location_id")
