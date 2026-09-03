"""sales_offer.copied_from_offer_id (KAN-12, PRD-Sales v2)

"Copy Offer starts a new lineage at version 1 and records
copiedFromOfferId." A real intra-context FK (sales_offer -> sales_offer),
not one of the cross-context "GUID column, no DB-level FK" references
elsewhere in this table — rule 2 is about crossing a bounded-context
boundary, which this doesn't.

Revision ID: d9e4c2a71f3b
Revises: 6aa677b9a9cb
Create Date: 2026-09-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd9e4c2a71f3b'
down_revision: Union[str, Sequence[str], None] = '6aa677b9a9cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales_offer",
        sa.Column("copied_from_offer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_offer.id"), nullable=True),
    )
    op.create_index("ix_sales_offer_copied_from_offer_id", "sales_offer", ["copied_from_offer_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_offer_copied_from_offer_id", table_name="sales_offer")
    op.drop_column("sales_offer", "copied_from_offer_id")
