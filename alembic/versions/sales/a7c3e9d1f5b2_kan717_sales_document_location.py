"""sales_document.location_id — the site an offer or contract prints for (KAN-717)

Anto's ruling (2026-11-15, B): a sales document prints for the responsible
seller's primary location; with none, for the seat. The site is fixed when the
document is made, so a reprint prints the same letterhead. Platform's location
id, no foreign key (rule 2). Nullable: every document made before this prints
for the seat, as it always did — no backfill.

Revision ID: a7c3e9d1f5b2
Revises: 4726dcd89260
Create Date: 2026-11-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.types import GUID

# revision identifiers, used by Alembic.
revision: str = "a7c3e9d1f5b2"
down_revision: Union[str, Sequence[str], None] = "4726dcd89260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales_document",
        sa.Column(
            "location_id", GUID(), nullable=True, comment="Owned by the platform context (Location). No DB-level FK."
        ),
    )


def downgrade() -> None:
    op.drop_column("sales_document", "location_id")
