"""KAN-597: the identAPI's Energieetikette per gearbox, with its PDF link

Anto, 2026-11-11, option A; technical shape decided under ADR-126 (ADR-142).
`GET /energylabel/info/typeapproval/{nr}` answers one row per gearbox with
the class, the gearbox as text and `energyLabelLink` (the label as PDF).
`vehicle_model_variant_energy_rating` keeps one class per year (BFE
canonical, ADR-042); this child table keeps what each gearbox carries.

**Schema.** `vehicle_model_variant_energy_label`: one row per (variant,
year, gearbox) — global master data like the rating (ADR-122 (3)), no
`tenant_id`. `gearbox` is NOT NULL ('' when the provider names none) so the
unique key holds without NULLS NOT DISTINCT.

**Data.** None. A variant opened before this migration gains its rows on the
next «Neu laden» (or never asked again otherwise, ADR-123: no automatic
refresh); the provider is not asked just to fill them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ce1e6fdf5e2c"
down_revision: str | Sequence[str] | None = "6f3e6383c4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_model_variant_energy_label",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_variant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_model_variant.id"),
            nullable=False,
        ),
        sa.Column("label_year", sa.Integer(), nullable=False),
        sa.Column("gearbox", sa.String(length=64), nullable=False),
        sa.Column("energy_efficiency_category", sa.String(length=4), nullable=False),
        sa.Column("label_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint(
            "model_variant_id", "label_year", "gearbox",
            name="uq_vehicle_model_variant_energy_label_variant_year_gearbox",
        ),
    )
    op.create_index(
        "ix_vehicle_model_variant_energy_label_model_variant_id",
        "vehicle_model_variant_energy_label",
        ["model_variant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vehicle_model_variant_energy_label_model_variant_id", table_name="vehicle_model_variant_energy_label"
    )
    op.drop_table("vehicle_model_variant_energy_label")
