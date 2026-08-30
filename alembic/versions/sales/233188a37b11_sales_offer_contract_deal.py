"""sales_offer, sales_contract, sales_deal, sales_number_sequence (WP-8
PR-1, S-D01/S-D06, ADR-060)

status is stored as a plain string column (native_enum=False), matching
every other context's SAEnum convention. sales_deal.status is a bare
String, never either entity's own enum type, since it spans BOTH
vocabularies (see app/sales/services/deal.py's own docstring).

Revision ID: 233188a37b11
Revises: 1a4cac4d57da
Create Date: 2026-08-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '233188a37b11'
down_revision: Union[str, Sequence[str], None] = '1a4cac4d57da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_number_sequence",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("series", sa.String(length=16), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
    )

    op.create_table(
        "sales_offer",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_number", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "customer_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the customer context. No DB-level FK.",
        ),
        sa.Column("customer_label", sa.String(length=200), nullable=True),
        sa.Column("customer_locality", sa.String(length=100), nullable=True),
        sa.Column("customer_denorm_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stock_item_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the inventory context (StockItem.id). No DB-level FK.",
        ),
        sa.Column("vehicle_label", sa.String(length=200), nullable=True),
        sa.Column("gross_price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("cancelled_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_sales_offer_tenant_id", "sales_offer", ["tenant_id"])
    op.create_index("ix_sales_offer_offer_number", "sales_offer", ["offer_number"])
    op.create_index("ix_sales_offer_customer_id", "sales_offer", ["customer_id"])

    op.create_table(
        "sales_contract",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_number", sa.String(length=16), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("offer_number", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "customer_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the customer context. No DB-level FK.",
        ),
        sa.Column("customer_label", sa.String(length=200), nullable=True),
        sa.Column("customer_locality", sa.String(length=100), nullable=True),
        sa.Column("customer_denorm_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stock_item_id", postgresql.UUID(as_uuid=True), nullable=True,
            comment="Owned by the inventory context (StockItem.id). No DB-level FK.",
        ),
        sa.Column("vehicle_label", sa.String(length=200), nullable=True),
        sa.Column("gross_price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("cancelled_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_sales_contract_tenant_id", "sales_contract", ["tenant_id"])
    op.create_index("ix_sales_contract_contract_number", "sales_contract", ["contract_number"])
    op.create_index("ix_sales_contract_offer_id", "sales_contract", ["offer_id"])
    op.create_index("ix_sales_contract_customer_id", "sales_contract", ["customer_id"])

    op.create_table(
        "sales_deal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("number", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("offer_number", sa.String(length=16), nullable=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contract_number", sa.String(length=16), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_label", sa.String(length=200), nullable=True),
        sa.Column("customer_locality", sa.String(length=100), nullable=True),
        sa.Column("customer_denorm_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vehicle_label", sa.String(length=200), nullable=True),
        sa.Column("gross_price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("margin", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("documents_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_sales_deal_tenant_id", "sales_deal", ["tenant_id"])
    op.create_index("ix_sales_deal_number", "sales_deal", ["number"])
    op.create_index("ix_sales_deal_offer_id", "sales_deal", ["offer_id"])
    op.create_index("ix_sales_deal_contract_id", "sales_deal", ["contract_id"])
    op.create_index("ix_sales_deal_customer_id", "sales_deal", ["customer_id"])


def downgrade() -> None:
    op.drop_table("sales_deal")
    op.drop_table("sales_contract")
    op.drop_table("sales_offer")
    op.drop_table("sales_number_sequence")
