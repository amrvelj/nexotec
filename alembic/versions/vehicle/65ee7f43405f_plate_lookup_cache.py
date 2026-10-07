"""KAN-42 (C-D) — the plate-lookup cache (FR-C-02).

`KontrollschildInfo` answers, kept per tenant (ADR-013) for the TTL stated
in `app/vehicle/services/identification.py::PLATE_LOOKUP_CACHE_TTL`.
Read by an exact plate or an exact Stammnummer only — never enumerable.

Revision ID: 65ee7f43405f
Revises: a9d4c2e7f1b3
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "65ee7f43405f"
down_revision: Union[str, Sequence[str], None] = "a9d4c2e7f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_plate_lookup_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plate", sa.String(16), nullable=False),
        sa.Column("vehicle_kind_code", sa.String(8), nullable=False),
        sa.Column("brand_name", sa.String(120), nullable=False),
        sa.Column("model_description", sa.String(200), nullable=False),
        sa.Column("production_from", sa.Integer(), nullable=True),
        sa.Column("production_to", sa.Integer(), nullable=True),
        sa.Column("type_approval_number", sa.String(16), nullable=False),
        sa.Column("first_registration_date", sa.Date(), nullable=True),
        sa.Column("stammnummer", sa.String(16), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vehicle_plate_lookup_cache_tenant_id", "vehicle_plate_lookup_cache", ["tenant_id"])
    op.create_index("ix_vehicle_plate_lookup_cache_tenant_plate", "vehicle_plate_lookup_cache", ["tenant_id", "plate"])
    op.create_index(
        "ix_vehicle_plate_lookup_cache_tenant_stammnummer", "vehicle_plate_lookup_cache", ["tenant_id", "stammnummer"]
    )


def downgrade() -> None:
    op.drop_index("ix_vehicle_plate_lookup_cache_tenant_stammnummer", table_name="vehicle_plate_lookup_cache")
    op.drop_index("ix_vehicle_plate_lookup_cache_tenant_plate", table_name="vehicle_plate_lookup_cache")
    op.drop_index("ix_vehicle_plate_lookup_cache_tenant_id", table_name="vehicle_plate_lookup_cache")
    op.drop_table("vehicle_plate_lookup_cache")
