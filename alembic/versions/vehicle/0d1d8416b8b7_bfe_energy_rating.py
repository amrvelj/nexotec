"""BFE energy rating, dated by year (WP-5 PR-8, ADR-042)

Revision ID: 0d1d8416b8b7
Revises: 903b69527f0b
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0d1d8416b8b7'
down_revision: Union[str, Sequence[str], None] = '903b69527f0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_model_variant_energy_rating",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_model_variant.id"),
            nullable=False,
        ),
        sa.Column("rating_year", sa.Integer(), nullable=False),
        sa.Column("energy_efficiency_category", sa.String(length=4), nullable=True),
        sa.Column("emission_standard", sa.String(length=16), nullable=True),
        sa.Column("consumption_norm", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "model_variant_id", "rating_year", name="uq_vehicle_model_variant_energy_rating_variant_year"
        ),
    )
    op.create_index(
        "ix_vehicle_model_variant_energy_rating_model_variant_id",
        "vehicle_model_variant_energy_rating", ["model_variant_id"],
    )
    op.create_index(
        "ix_vehicle_model_variant_energy_rating_rating_year",
        "vehicle_model_variant_energy_rating", ["rating_year"],
    )


def downgrade() -> None:
    op.drop_table("vehicle_model_variant_energy_rating")
