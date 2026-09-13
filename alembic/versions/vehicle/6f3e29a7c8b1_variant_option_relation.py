"""Variant option relations (KAN-43, Configurator C-E, FR-C-06/ADR-072)

`OptionenAusschluss` (excludes) / `OptionenPack` (contains) / `Aktion`
(CodeGrpNr 047, the remaining `option_relation_type` values) — none of
these had anywhere to land before this ticket. ADR-072: stored and shown,
never enforced. See `app/vehicle/models/catalogue.py::VariantOptionRelation`.

Revision ID: 6f3e29a7c8b1
Revises: 00660589ef1c
Create Date: 2026-09-13 00:00:00.000001

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6f3e29a7c8b1"
down_revision: Union[str, Sequence[str], None] = "00660589ef1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_variant_option_relation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "model_variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_model_variant.id"),
            nullable=False,
        ),
        sa.Column("model_year", sa.Integer(), nullable=False),
        sa.Column(
            "from_option_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_variant_option.id"),
            nullable=False,
        ),
        sa.Column(
            "to_option_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_variant_option.id"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("price_in_combination", sa.DECIMAL(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_variant_option_relation_tenant_id", "vehicle_variant_option_relation", ["tenant_id"])
    op.create_index(
        "ix_vehicle_variant_option_relation_model_variant_id",
        "vehicle_variant_option_relation",
        ["model_variant_id"],
    )
    op.create_index(
        "ix_vehicle_variant_option_relation_from_option_id", "vehicle_variant_option_relation", ["from_option_id"]
    )
    op.create_index(
        "ix_vehicle_variant_option_relation_to_option_id", "vehicle_variant_option_relation", ["to_option_id"]
    )
    op.create_unique_constraint(
        "uq_vehicle_variant_option_relation_natural_key",
        "vehicle_variant_option_relation",
        ["tenant_id", "from_option_id", "to_option_id", "relation_type"],
    )


def downgrade() -> None:
    op.drop_table("vehicle_variant_option_relation")
