"""Configurator C-C (KAN-41) — the configuration entity (ADR-068).

`vehicle_configuration` carries the ADR-071 specification block (the same
38 columns C-A put on `vehicle_model_variant`, via the shared
`VehicleSpecBlock` mixin) plus the configuration's own fields: source,
mode, match status/method, the catalogue link (three-column pattern, **no
FK** — catalogue variants are re-synced), the observed-at-capture fields,
the `vehicle_mdm` link (set only on a VIN hit, three-column, no FK),
denormalised identity strings, colour, `overridden_fields`, notes.

`vehicle_configuration_option` + `vehicle_configuration_option_equipment_feature`
— the selected options; C-C ships the tables and a hand-typed path, C-E
adds priced catalogue options / packages / colours / wheels / images.

Tenant-scoped (ADR-013). No standalone surface — no list endpoint, no
route, no human-readable number.

Revision ID: e4a1c7f92b83
Revises: d7b3e1c94a02
Create Date: 2026-09-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e4a1c7f92b83"
down_revision: Union[str, Sequence[str], None] = "d7b3e1c94a02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _spec_block_columns() -> list[sa.Column]:
    """The 38-column ADR-071 spec block — identical shape to
    `c1f7a2e9b3d4` (C-A) on `vehicle_model_variant`. All nullable."""

    return [
        sa.Column("model_type_name", sa.String(length=100), nullable=True),
        sa.Column("trim_name", sa.String(length=30), nullable=True),
        sa.Column("engine_cycle", sa.String(length=64), nullable=True),
        sa.Column("displacement_ccm", sa.Integer(), nullable=True),
        sa.Column("cylinders", sa.Integer(), nullable=True),
        sa.Column("gears", sa.Integer(), nullable=True),
        sa.Column("ps", sa.Integer(), nullable=True),
        sa.Column("kw", sa.Integer(), nullable=True),
        sa.Column("total_ps", sa.Integer(), nullable=True),
        sa.Column("total_kw", sa.Integer(), nullable=True),
        sa.Column("system_kw", sa.Integer(), nullable=True),
        sa.Column("vehicle_class", sa.String(length=64), nullable=True),
        sa.Column("valuation_classification", sa.String(length=64), nullable=True),
        sa.Column("emission_standard", sa.String(length=64), nullable=True),
        sa.Column("doors", sa.Integer(), nullable=True),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("weight_empty_kg", sa.Integer(), nullable=True),
        sa.Column("weight_total_kg", sa.Integer(), nullable=True),
        sa.Column("payload_kg", sa.Integer(), nullable=True),
        sa.Column("towing_capacity_kg", sa.Integer(), nullable=True),
        sa.Column("wheelbase_mm", sa.Integer(), nullable=True),
        sa.Column("consumption_mixed", sa.DECIMAL(precision=4, scale=1), nullable=True),
        sa.Column("consumption_urban", sa.DECIMAL(precision=4, scale=1), nullable=True),
        sa.Column("consumption_extra_urban", sa.DECIMAL(precision=4, scale=1), nullable=True),
        sa.Column("consumption_norm", sa.String(length=64), nullable=True),
        sa.Column("co2_gkm", sa.Integer(), nullable=True),
        sa.Column("energy_consumption_kwh", sa.DECIMAL(precision=5, scale=1), nullable=True),
        sa.Column("battery_capacity_kwh", sa.DECIMAL(precision=6, scale=2), nullable=True),
        sa.Column("range_km", sa.Integer(), nullable=True),
        sa.Column("tank_capacity_l", sa.Integer(), nullable=True),
        sa.Column("werkscode", sa.String(length=64), nullable=True),
        sa.Column("importcode", sa.String(length=64), nullable=True),
        sa.Column("model_year", sa.Integer(), nullable=True),
        sa.Column("production_from", sa.Integer(), nullable=True),
        sa.Column("production_to", sa.Integer(), nullable=True),
        sa.Column("base_price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("base_price_year", sa.Integer(), nullable=True),
        sa.Column("price_is_net", sa.Boolean(), nullable=True),
    ]


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "vehicle_configuration",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        # -- configuration identity / classification --
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("catalogue_match_status", sa.String(length=24), nullable=False),
        sa.Column("match_method", sa.String(length=20), nullable=False),
        # -- five coded spec fields declared directly (as on ModelVariant) --
        sa.Column("vehicle_kind", sa.String(length=64), nullable=True),
        sa.Column("fuel_type", sa.String(length=64), nullable=True),
        sa.Column("body_style", sa.String(length=64), nullable=True),
        sa.Column("drivetrain", sa.String(length=64), nullable=True),
        sa.Column("transmission", sa.String(length=64), nullable=True),
        # -- catalogue link (three-column, no FK) --
        sa.Column("catalogue_variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("catalogue_variant_label", sa.String(length=200), nullable=True),
        sa.Column("catalogue_variant_label_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        # -- observed at capture --
        sa.Column("vin", sa.String(length=17), nullable=True),
        sa.Column("stammnummer", sa.String(length=9), nullable=True),
        sa.Column("type_approval_number", sa.String(length=6), nullable=True),
        sa.Column("first_registration_date", sa.Date(), nullable=True),
        sa.Column("licence_plate", sa.String(length=16), nullable=True),
        sa.Column("mileage_km", sa.Integer(), nullable=True),
        # -- vehicle_mdm link, VIN-hit only (three-column, no FK) --
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vehicle_label", sa.String(length=80), nullable=True),
        sa.Column("vehicle_label_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        # -- identity tier (denormalised strings) --
        sa.Column("brand_display_name", sa.String(length=120), nullable=True),
        sa.Column("model_group_name", sa.String(length=120), nullable=True),
        sa.Column("variant_name", sa.String(length=160), nullable=True),
        # -- colour --
        sa.Column("exterior_colour", sa.String(length=120), nullable=True),
        sa.Column("interior_colour", sa.String(length=120), nullable=True),
        sa.Column("exterior_colour_surcharge", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("interior_colour_surcharge", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("overridden_fields", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_spec_block_columns(),
        *_audit_columns(),
    )
    op.create_index("ix_vehicle_configuration_tenant_id", "vehicle_configuration", ["tenant_id"])
    op.create_index(
        "ix_vehicle_configuration_catalogue_variant_id", "vehicle_configuration", ["catalogue_variant_id"]
    )
    op.create_index("ix_vehicle_configuration_vehicle_id", "vehicle_configuration", ["vehicle_id"])

    op.create_table(
        "vehicle_configuration_option",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "configuration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_configuration.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("variant_option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("option_code", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("option_group", sa.String(length=64), nullable=True),
        sa.Column("price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("is_included", sa.Boolean(), nullable=False),
        sa.Column("is_package", sa.Boolean(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        *_audit_columns(),
    )
    op.create_index(
        "ix_vehicle_configuration_option_tenant_id", "vehicle_configuration_option", ["tenant_id"]
    )
    op.create_index(
        "ix_vehicle_configuration_option_configuration_id",
        "vehicle_configuration_option",
        ["configuration_id"],
    )

    # Table name kept short — a longer one pushes the derived FK-index
    # identifier past Postgres' 63-char limit.
    op.create_table(
        "vehicle_config_option_feature",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "configuration_option_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_configuration_option.id"),
            nullable=False,
        ),
        sa.Column("feature_value_code", sa.String(length=64), nullable=False),
        *_audit_columns(),
    )
    op.create_index(
        "ix_vehicle_config_option_feature_tenant_id",
        "vehicle_config_option_feature",
        ["tenant_id"],
    )
    op.create_index(
        "ix_vehicle_config_option_feature_configuration_option_id",
        "vehicle_config_option_feature",
        ["configuration_option_id"],
    )


def downgrade() -> None:
    op.drop_table("vehicle_config_option_feature")
    op.drop_table("vehicle_configuration_option")
    op.drop_table("vehicle_configuration")
