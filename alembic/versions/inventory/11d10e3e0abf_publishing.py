"""stock_item_media + stock_item_publishing (WP-7 PR-8, ADR-062)

Revision ID: 11d10e3e0abf
Revises: ce4b8c411730
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '11d10e3e0abf'
down_revision: Union[str, Sequence[str], None] = 'ce4b8c411730'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_item_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), nullable=False,
            comment="Owned by the platform context (Dealership.id). No DB-level FK.",
        ),
        sa.Column("stock_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_item.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # 1..16 enforced in app/inventory/services/publishing.py, not a DB
        # CHECK — reorder_media's own renumbering needs to pass positions
        # through a transient out-of-range value to escape the unique
        # constraint below (CHECK is always immediate, never deferrable).
    )
    op.create_index("ix_stock_item_media_tenant_id", "stock_item_media", ["tenant_id"])
    op.create_index("ix_stock_item_media_stock_item_id", "stock_item_media", ["stock_item_id"])
    op.create_unique_constraint(
        "uq_stock_item_media_stock_item_id_position", "stock_item_media", ["stock_item_id", "position"]
    )

    op.create_table(
        "stock_item_publishing",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), nullable=False,
            comment="Owned by the platform context (Dealership.id). No DB-level FK.",
        ),
        sa.Column("stock_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stock_item.id"), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("zusatztitel", sa.String(length=500), nullable=True),
        sa.Column("bemerkungen", sa.Text(), nullable=True),
        sa.Column("zustandsbeschreibung", sa.Text(), nullable=True),
        sa.Column("haendlerbemerkungen", sa.Text(), nullable=True),
        sa.Column("youtube_url", sa.String(length=500), nullable=True),
        sa.Column("pdf_document_ref", sa.String(length=500), nullable=True),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_stock_item_publishing_tenant_id", "stock_item_publishing", ["tenant_id"])
    op.create_index("ix_stock_item_publishing_stock_item_id", "stock_item_publishing", ["stock_item_id"])
    op.create_unique_constraint(
        "uq_stock_item_publishing_stock_item_id_channel", "stock_item_publishing", ["stock_item_id", "channel"]
    )


def downgrade() -> None:
    op.drop_table("stock_item_publishing")
    op.drop_table("stock_item_media")
