"""Configurator C-A (KAN-39) — the specification block on the catalogue
variant, ``vehicle_variant_price``, and the ``vehicle_variant_option``
extension C-E needs.

ADR-071 — one spec block, three carriers. This migration adds the block's
38 columns to ``vehicle_model_variant`` (the *type* carrier). The
configuration carrier (C-C / KAN-41) and the host-snapshot carrier
(C-F / KAN-10) mix in / round-trip the **same**
``app.vehicle.models.spec_block.VehicleSpecBlock`` declaration, so they
cannot drift — ``tests/architecture/test_spec_block_carriers_do_not_drift.py``
fails if they do.

Every spec-block column is nullable: a freshly-seeded variant, and every
manual configuration (FR-C-05), carries only a few of them. ``0`` from
auto-i-dat means *unknown* → ``NULL`` (a service-layer rule, not a
constraint).

Column count on ``vehicle_model_variant``: **15 → 53**.

``vehicle_variant_price`` — new-car list price per model year
(``FahrzeugePreise``); ``FahrzeugeMatch`` (C-D) will not run without a
year-correct Neupreis. Global, matching ``ModelVariant``.

``vehicle_variant_option`` gains ``is_included`` / ``is_package`` /
``model_year`` and a child table
``vehicle_variant_option_equipment_feature`` (``SuchCode`` 045, the list
marketplace publishing reads). All nullable / defaulted so the existing
``catalogue_sync`` option upsert keeps working unchanged — C-E wires the
populate side.

**No provider call is made by this migration or anywhere in C-A.**
Seeding ``provider_code_map`` from the ``Codes`` Datenname moved to C-0.

Revision ID: c1f7a2e9b3d4
Revises: eb660a3213bd
Create Date: 2026-09-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c1f7a2e9b3d4"
down_revision: Union[str, Sequence[str], None] = "eb660a3213bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The 38-column specification block (ADR-071). Kept as (name, type) pairs +
# a loop rather than 38 hand-written op.add_column lines — the single source
# of truth for the field set is
# app.vehicle.models.spec_block.SPEC_BLOCK_FIELDS, and the drift test
# asserts this migration's result matches it. Every column is nullable.
def _spec_block_columns() -> list[sa.Column]:
    return [
        # labelling
        sa.Column("model_type_name", sa.String(length=100), nullable=True),
        sa.Column("trim_name", sa.String(length=30), nullable=True),
        # powertrain
        sa.Column("engine_cycle", sa.String(length=64), nullable=True),
        sa.Column("displacement_ccm", sa.Integer(), nullable=True),
        sa.Column("cylinders", sa.Integer(), nullable=True),
        sa.Column("gears", sa.Integer(), nullable=True),
        sa.Column("ps", sa.Integer(), nullable=True),
        sa.Column("kw", sa.Integer(), nullable=True),
        sa.Column("total_ps", sa.Integer(), nullable=True),
        sa.Column("total_kw", sa.Integer(), nullable=True),
        sa.Column("system_kw", sa.Integer(), nullable=True),
        # classification
        sa.Column("vehicle_class", sa.String(length=64), nullable=True),
        sa.Column("valuation_classification", sa.String(length=64), nullable=True),
        sa.Column("emission_standard", sa.String(length=64), nullable=True),
        # dimensions / weights
        sa.Column("doors", sa.Integer(), nullable=True),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("weight_empty_kg", sa.Integer(), nullable=True),
        sa.Column("weight_total_kg", sa.Integer(), nullable=True),
        sa.Column("payload_kg", sa.Integer(), nullable=True),
        sa.Column("towing_capacity_kg", sa.Integer(), nullable=True),
        sa.Column("wheelbase_mm", sa.Integer(), nullable=True),
        # consumption / energy
        sa.Column("consumption_mixed", sa.DECIMAL(precision=4, scale=1), nullable=True),
        sa.Column("consumption_urban", sa.DECIMAL(precision=4, scale=1), nullable=True),
        sa.Column("consumption_extra_urban", sa.DECIMAL(precision=4, scale=1), nullable=True),
        sa.Column("consumption_norm", sa.String(length=64), nullable=True),
        sa.Column("co2_gkm", sa.Integer(), nullable=True),
        sa.Column("energy_consumption_kwh", sa.DECIMAL(precision=5, scale=1), nullable=True),
        sa.Column("battery_capacity_kwh", sa.DECIMAL(precision=6, scale=2), nullable=True),
        sa.Column("range_km", sa.Integer(), nullable=True),
        sa.Column("tank_capacity_l", sa.Integer(), nullable=True),
        # homologation codes
        sa.Column("werkscode", sa.String(length=64), nullable=True),
        sa.Column("importcode", sa.String(length=64), nullable=True),
        # production window / price
        sa.Column("model_year", sa.Integer(), nullable=True),
        sa.Column("production_from", sa.Integer(), nullable=True),
        sa.Column("production_to", sa.Integer(), nullable=True),
        sa.Column("base_price", sa.DECIMAL(precision=12, scale=2), nullable=True),
        sa.Column("base_price_year", sa.Integer(), nullable=True),
        sa.Column("price_is_net", sa.Boolean(), nullable=True),
    ]


def upgrade() -> None:
    # 1 · the specification block on the catalogue variant (ADR-071)
    for column in _spec_block_columns():
        op.add_column("vehicle_model_variant", column)

    # 2 · vehicle_variant_price — new-car list price per model year
    op.create_table(
        "vehicle_variant_price",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_model_variant.id"),
            nullable=False,
        ),
        sa.Column("model_year", sa.Integer(), nullable=False),
        sa.Column("new_price", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("price_is_net", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="provider"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_vehicle_variant_price_model_variant_id", "vehicle_variant_price", ["model_variant_id"]
    )
    op.create_unique_constraint(
        "uq_vehicle_variant_price_variant_year",
        "vehicle_variant_price",
        ["model_variant_id", "model_year"],
    )
    # server_default was only to fill the (empty) table during the DDL — the
    # ORM owns the default from here on, same posture as elsewhere in this repo.
    op.alter_column("vehicle_variant_price", "price_is_net", server_default=None)
    op.alter_column("vehicle_variant_price", "source", server_default=None)

    # 3 · vehicle_variant_option — what C-E (KAN-43) needs
    op.add_column(
        "vehicle_variant_option",
        sa.Column("is_included", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "vehicle_variant_option",
        sa.Column("is_package", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("vehicle_variant_option", sa.Column("model_year", sa.Integer(), nullable=True))
    op.alter_column("vehicle_variant_option", "is_included", server_default=None)
    op.alter_column("vehicle_variant_option", "is_package", server_default=None)

    op.create_table(
        "vehicle_variant_option_equipment_feature",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "variant_option_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_variant_option.id"),
            nullable=False,
        ),
        sa.Column("feature_value_code", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_vehicle_variant_option_equipment_feature_tenant_id",
        "vehicle_variant_option_equipment_feature",
        ["tenant_id"],
    )
    op.create_index(
        "ix_vehicle_variant_option_equipment_feature_variant_option_id",
        "vehicle_variant_option_equipment_feature",
        ["variant_option_id"],
    )
    op.create_unique_constraint(
        "uq_vehicle_variant_option_equipment_feature_option_feature",
        "vehicle_variant_option_equipment_feature",
        ["variant_option_id", "feature_value_code"],
    )


def downgrade() -> None:
    op.drop_table("vehicle_variant_option_equipment_feature")
    op.drop_column("vehicle_variant_option", "model_year")
    op.drop_column("vehicle_variant_option", "is_package")
    op.drop_column("vehicle_variant_option", "is_included")

    op.drop_constraint("uq_vehicle_variant_price_variant_year", "vehicle_variant_price", type_="unique")
    op.drop_index("ix_vehicle_variant_price_model_variant_id", table_name="vehicle_variant_price")
    op.drop_table("vehicle_variant_price")

    for column in reversed(_spec_block_columns()):
        op.drop_column("vehicle_model_variant", column.name)
