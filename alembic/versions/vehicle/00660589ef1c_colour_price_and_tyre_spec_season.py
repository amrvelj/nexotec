"""Colour surcharge and tyre-spec season/remark (KAN-43, Configurator C-E)

`OptionenFarben.Preis` (FR-C-07: "the surcharge is a price line in build
mode") and `PneuDimTS.BemDe`/`PneuTyp` (FR-C-08: the alloy-wheel caveat and
summer/winter) were both parsed by no one until this ticket — see
`app/integration/adapters/auto_i_dat_soap.py` and `app/integration/adapters/
base.py`. This migration catches the mirror tables up to what the adapter
now returns.

`vehicle_tyre_spec_cache`'s unique constraint widens from
`(tenant_id, model_variant_id, axle)` to
`(tenant_id, model_variant_id, axle, season)` — the two-column key silently
collapsed a variant's summer and winter specs for the same axle into one
row on every re-sync. The table was empty in every environment before this
ticket (no caller had ever populated `TyreSpecCache` beyond the size/
load_index/speed_rating columns the original migration shipped), so no
backfill of `season` is needed for the constraint swap to be safe.

Revision ID: 00660589ef1c
Revises: e4a1c7f92b83
Create Date: 2026-09-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00660589ef1c"
down_revision: Union[str, Sequence[str], None] = "e4a1c7f92b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vehicle_colour_cache", sa.Column("price", sa.DECIMAL(12, 2), nullable=True))

    op.add_column("vehicle_tyre_spec_cache", sa.Column("season", sa.String(length=16), nullable=True))
    op.add_column("vehicle_tyre_spec_cache", sa.Column("remark", sa.String(length=160), nullable=True))
    op.drop_constraint(
        "uq_vehicle_tyre_spec_cache_tenant_variant_axle", "vehicle_tyre_spec_cache", type_="unique"
    )
    op.create_unique_constraint(
        "uq_vehicle_tyre_spec_cache_tenant_variant_axle_season",
        "vehicle_tyre_spec_cache",
        ["tenant_id", "model_variant_id", "axle", "season"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_vehicle_tyre_spec_cache_tenant_variant_axle_season", "vehicle_tyre_spec_cache", type_="unique"
    )
    op.create_unique_constraint(
        "uq_vehicle_tyre_spec_cache_tenant_variant_axle",
        "vehicle_tyre_spec_cache",
        ["tenant_id", "model_variant_id", "axle"],
    )
    op.drop_column("vehicle_tyre_spec_cache", "remark")
    op.drop_column("vehicle_tyre_spec_cache", "season")

    op.drop_column("vehicle_colour_cache", "price")
