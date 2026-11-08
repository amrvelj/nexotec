"""KAN-211 (ADR-123): catalogue details load once per Betrieb when a car is opened

- `vehicle_variant_detail_state`: one row per (Betrieb, variant, detail) saying whether
  that detail (options, colours, tyre_specs, images, energy_label) was loaded, refused
  by the provider's licence or failed, and when. The content tables cannot tell *empty*
  from *refused* from *never asked*; this table decides whether an open sends a request.
  Tenant-partitioned like the content it describes (ADR-013).
- `vehicle_variant_option.manufacturer_code`: the manufacturer's own option code (the
  identAPI's `optionCode`), beside auto-i's key in `option_code` — what the advisor
  searches by (Anto, 2026-11-08). NULL on rows stored before this release; the next
  «Neu laden» fills it.
- `vehicle_variant_option.option_group_code`: the provider's raw group code, kept so a
  resolved mapping gap fills `option_group` on the rows it left NULL (KAN-75's pattern).

No data step. The downgrade drops both; the detail states are a cache of what was
asked and are rebuilt on the next open.

Revision ID: e6f4cba3ab86
Revises: ddc30e4347ba
Create Date: 2026-11-08 15:09:13.057363

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f4cba3ab86"
down_revision: str | Sequence[str] | None = "ddc30e4347ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_variant_detail_state",
        sa.Column("model_variant_id", app.core.types.GUID(), nullable=False),
        sa.Column("detail", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("loaded_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("error", sa.String(length=80), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("tenant_id", app.core.types.GUID(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", app.core.types.GUID(), nullable=True),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.CheckConstraint(
            "detail IN ('options', 'colours', 'tyre_specs', 'images', 'energy_label')",
            name="ck_vehicle_variant_detail_state_detail_enum",
        ),
        sa.CheckConstraint(
            "state IN ('present', 'not_in_contract', 'unavailable')", name="ck_vehicle_variant_detail_state_state_enum"
        ),
        sa.ForeignKeyConstraint(
            ["model_variant_id"],
            ["vehicle_model_variant.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "model_variant_id", "detail", name="uq_vehicle_variant_detail_state_tenant_variant_detail"
        ),
    )
    op.create_index(
        op.f("ix_vehicle_variant_detail_state_model_variant_id"),
        "vehicle_variant_detail_state",
        ["model_variant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vehicle_variant_detail_state_tenant_id"), "vehicle_variant_detail_state", ["tenant_id"], unique=False
    )
    op.add_column(
        "vehicle_variant_option",
        sa.Column(
            "option_group_code",
            sa.String(length=32),
            nullable=True,
            comment="KAN-211: the provider's raw option group code; option_group is what it resolves to.",
        ),
    )
    op.add_column(
        "vehicle_variant_option",
        sa.Column(
            "manufacturer_code",
            sa.String(length=64),
            nullable=True,
            comment="KAN-211: the manufacturer's option code (identAPI optionCode); option_code is the provider's key.",
        ),
    )


def downgrade() -> None:
    op.drop_column("vehicle_variant_option", "manufacturer_code")
    op.drop_column("vehicle_variant_option", "option_group_code")
    op.drop_index(op.f("ix_vehicle_variant_detail_state_tenant_id"), table_name="vehicle_variant_detail_state")
    op.drop_index(op.f("ix_vehicle_variant_detail_state_model_variant_id"), table_name="vehicle_variant_detail_state")
    op.drop_table("vehicle_variant_detail_state")
