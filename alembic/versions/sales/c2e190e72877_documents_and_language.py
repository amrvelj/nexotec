"""sales_document + customer_language + sales_contract pricing itemization
(WP-8 PR-7)

Revision ID: c2e190e72877
Revises: 041201c45ee7
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c2e190e72877'
down_revision: Union[str, Sequence[str], None] = '041201c45ee7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales_offer", sa.Column("customer_language", sa.String(length=2), nullable=True))
    op.add_column("sales_contract", sa.Column("customer_language", sa.String(length=2), nullable=True))
    op.add_column("sales_contract", sa.Column("base_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("options_total", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("list_price", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("accessories_total", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("sales_contract", sa.Column("discount_amount", sa.DECIMAL(precision=12, scale=2), nullable=True))

    op.create_table(
        "sales_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_type", sa.String(length=16), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("correspondence_language", sa.String(length=2), nullable=False),
        sa.Column("content_definition", sa.JSON(), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rendered_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_sales_document_tenant_id", "sales_document", ["tenant_id"])
    op.create_index("ix_sales_document_owner_id", "sales_document", ["owner_id"])


def downgrade() -> None:
    op.drop_table("sales_document")
    op.drop_column("sales_contract", "discount_amount")
    op.drop_column("sales_contract", "accessories_total")
    op.drop_column("sales_contract", "list_price")
    op.drop_column("sales_contract", "options_total")
    op.drop_column("sales_contract", "base_price")
    op.drop_column("sales_contract", "customer_language")
    op.drop_column("sales_offer", "customer_language")
