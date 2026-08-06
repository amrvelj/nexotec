"""transaction table

Revision ID: e5873926e0a9
Revises: 44a979f9f37b
Create Date: 2026-08-06 13:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5873926e0a9'
down_revision: Union[str, Sequence[str], None] = '44a979f9f37b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dealer.id", name="fk_transaction_tenant_id_dealer"),
            nullable=False,
        ),
        sa.Column(
            "transaction_type",
            sa.Enum("sale", "trade_in", name="transaction_type", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft", "completed", "cancelled", name="transaction_status", native_enum=False, length=16
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer.id", name="fk_transaction_customer_id_customer"),
            nullable=False,
        ),
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle.id", name="fk_transaction_vehicle_id_vehicle"),
            nullable=False,
        ),
        sa.Column(
            "primary_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", name="fk_transaction_primary_user_id_user"),
            nullable=False,
        ),
        sa.Column("amount", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_transaction_tenant_id", "transaction", ["tenant_id"])
    op.create_index("ix_transaction_customer_id", "transaction", ["customer_id"])
    op.create_index("ix_transaction_vehicle_id", "transaction", ["vehicle_id"])
    op.create_index("ix_transaction_primary_user_id", "transaction", ["primary_user_id"])


def downgrade() -> None:
    op.drop_index("ix_transaction_primary_user_id", table_name="transaction")
    op.drop_index("ix_transaction_vehicle_id", table_name="transaction")
    op.drop_index("ix_transaction_customer_id", table_name="transaction")
    op.drop_index("ix_transaction_tenant_id", table_name="transaction")
    op.drop_table("transaction")
