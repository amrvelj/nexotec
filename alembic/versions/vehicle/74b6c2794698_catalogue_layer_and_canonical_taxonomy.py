"""Catalogue layer + canonical taxonomy (WP-5 PR-1, Vehicle PRD v0.6 phase V-A)

Creates the five catalogue tables (app.vehicle.models.catalogue): brand,
model_group, model_variant, variant_option, type_approval. None reference a
physical vehicle — VehicleMdm.catalogue_variant_id (PR-3) points here, never
the other way. All five are global, no tenant_id, same reasoning as the
shipped vehicle table's own "a VIN is decoded manufacturer data" note.

The 16 new reference_list/reference_value rows this catalogue needs
(vehicle_kind, vehicle_class, colour, …) are seeded by a SEPARATE
migration on the platform branch (6ba0a99ed5c4), not here. An earlier
version of this migration seeded them inline and assumed reference_value's
label_en column already existed by the time this ran — true only if the
platform branch's own label_en migration (f2a6c1b7de3c) had already
applied, which alembic's independent per-context branches never guarantee.
The Postgres migration-smoke-test CI job caught it (column "label_en"
does not exist) when `upgrade heads` happened to apply this branch first.
See 6ba0a99ed5c4's own docstring for the rest — vehicle_type/fuel_type/
body_style/drivetrain/transmission/exterior_color/interior_color/
oem_affiliations already exist from WP-1 and are reused as-is.

Revision ID: 74b6c2794698
Revises: f1014d3374ef
Create Date: 2026-08-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '74b6c2794698'
down_revision: Union[str, Sequence[str], None] = 'f1014d3374ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_brand",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_brand_code", "vehicle_brand", ["code"])

    op.create_table(
        "vehicle_model_group",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_brand.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("brand_id", "name", name="uq_vehicle_model_group_brand_id_name"),
    )
    op.create_index("ix_vehicle_model_group_brand_id", "vehicle_model_group", ["brand_id"])

    op.create_table(
        "vehicle_model_variant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vehicle_model_group.id"), nullable=False
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("model_year_from", sa.Integer(), nullable=False),
        sa.Column("model_year_to", sa.Integer(), nullable=True),
        sa.Column("vehicle_kind", sa.String(length=64), nullable=True),
        sa.Column("fuel_type", sa.String(length=64), nullable=True),
        sa.Column("body_style", sa.String(length=64), nullable=True),
        sa.Column("drivetrain", sa.String(length=64), nullable=True),
        sa.Column("transmission", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_model_variant_model_group_id", "vehicle_model_variant", ["model_group_id"])

    op.create_table(
        "vehicle_variant_option",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_model_variant.id"),
            nullable=False,
        ),
        sa.Column("option_code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("option_group", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_variant_option_model_variant_id", "vehicle_variant_option", ["model_variant_id"])

    op.create_table(
        "vehicle_type_approval",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_model_variant.id"),
            nullable=False,
        ),
        sa.Column("type_approval_number", sa.String(length=6), nullable=False, unique=True),
        sa.Column("first_registration_from", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_type_approval_model_variant_id", "vehicle_type_approval", ["model_variant_id"])
    op.create_index("ix_vehicle_type_approval_type_approval_number", "vehicle_type_approval", ["type_approval_number"])


def downgrade() -> None:
    op.drop_table("vehicle_type_approval")
    op.drop_table("vehicle_variant_option")
    op.drop_table("vehicle_model_variant")
    op.drop_table("vehicle_model_group")
    op.drop_table("vehicle_brand")
