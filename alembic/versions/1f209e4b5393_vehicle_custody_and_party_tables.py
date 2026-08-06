"""vehicle, vehicle_custody_event, vehicle_party tables

Revision ID: 1f209e4b5393
Revises: c9654d846ac9
Create Date: 2026-08-06 08:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1f209e4b5393'
down_revision: Union[str, Sequence[str], None] = 'c9654d846ac9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("make", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("model_year", sa.Integer(), nullable=False),
        sa.Column("trim", sa.String(length=100), nullable=True),
        sa.Column("engine", sa.String(length=100), nullable=True),
        sa.Column(
            "condition",
            sa.Enum(
                "new", "used", "certified_pre_owned", "demo",
                name="vehicle_condition", native_enum=False, length=32,
            ),
            nullable=False,
        ),
        sa.Column("vehicle_type", sa.String(length=64), nullable=True),
        sa.Column("fuel_type", sa.String(length=64), nullable=True),
        sa.Column("body_style", sa.String(length=64), nullable=True),
        sa.Column("drivetrain", sa.String(length=64), nullable=True),
        sa.Column("transmission", sa.String(length=64), nullable=True),
        sa.Column("exterior_color", sa.String(length=64), nullable=True),
        sa.Column("interior_color", sa.String(length=64), nullable=True),
        sa.Column("energy_efficiency_category", sa.String(length=4), nullable=True),
        sa.Column("co2_emissions_gkm", sa.Integer(), nullable=True),
        sa.Column("odometer", sa.Integer(), nullable=True),
        sa.Column(
            "registration_status",
            sa.Enum(
                "unregistered", "registered", "deregistered", "export",
                name="registration_status", native_enum=False, length=32,
            ),
            nullable=False,
            server_default="unregistered",
        ),
        sa.Column("registration_canton", sa.String(length=2), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "in_transit", "in_stock", "sold", "in_service", "totaled", "scrapped",
                name="vehicle_status", native_enum=False, length=32,
            ),
            nullable=False,
            server_default="in_transit",
        ),
        sa.Column(
            "current_custodian_partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dealer.id", name="fk_vehicle_current_custodian_partner_id_dealer"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_vin", "vehicle", ["vin"])
    op.create_unique_constraint("uq_vehicle_vin", "vehicle", ["vin"])

    op.create_table(
        "vehicle_custody_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle.id", name="fk_vehicle_custody_event_vehicle_id_vehicle"),
            nullable=False,
        ),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dealer.id", name="fk_vehicle_custody_event_partner_id_dealer"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.Enum(
                "acquired", "transferred", "sold", "repossessed",
                name="custody_event_type", native_enum=False, length=32,
            ),
            nullable=False,
        ),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        # No FK — Transaction (issue #6) doesn't exist yet, forward reference.
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_custody_event_vehicle_id", "vehicle_custody_event", ["vehicle_id"])
    op.create_index("ix_vehicle_custody_event_partner_id", "vehicle_custody_event", ["partner_id"])

    op.create_table(
        "vehicle_party",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle.id", name="fk_vehicle_party_vehicle_id_vehicle"),
            nullable=False,
        ),
        # No FK — Customer (issue #4) doesn't exist on this branch's
        # dependency chain (stacked on issue #3), forward reference.
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "keeper", "driver", name="vehicle_party_role", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_vehicle_party_vehicle_id", "vehicle_party", ["vehicle_id"])
    op.create_index("ix_vehicle_party_customer_id", "vehicle_party", ["customer_id"])
    op.create_unique_constraint(
        "uq_vehicle_party_scope", "vehicle_party", ["vehicle_id", "customer_id", "role", "effective_from"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_vehicle_party_scope", "vehicle_party", type_="unique")
    op.drop_index("ix_vehicle_party_customer_id", table_name="vehicle_party")
    op.drop_index("ix_vehicle_party_vehicle_id", table_name="vehicle_party")
    op.drop_table("vehicle_party")

    op.drop_index("ix_vehicle_custody_event_partner_id", table_name="vehicle_custody_event")
    op.drop_index("ix_vehicle_custody_event_vehicle_id", table_name="vehicle_custody_event")
    op.drop_table("vehicle_custody_event")

    op.drop_constraint("uq_vehicle_vin", "vehicle", type_="unique")
    op.drop_index("ix_vehicle_vin", table_name="vehicle")
    op.drop_table("vehicle")
