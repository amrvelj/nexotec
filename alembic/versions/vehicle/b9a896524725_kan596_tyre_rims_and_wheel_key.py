"""KAN-596: tyre rows carry the rim, and one row per approved wheel

FR-C-08 «Wheels and tyres»; the natural key is a technical decision under
ADR-126 (ADR-141). The identAPI's `TyresAndRimsItem` carries the rim
(`rims`, `rimWidth`, `rimSize`, `rimOffset`) and whether the row is the
factory fitment (`standardTyres`); `vehicle_tyre_spec_cache` kept none of
them, and its key (tenant, variant, axle code, season code) kept only the
first dimension per axle and season.

**Schema.** Five nullable columns: `rims` (designation as delivered),
`rim_width` (inches, one decimal), `rim_size` (inches), `rim_offset` (mm,
the ET), `is_standard`. The unique key becomes (tenant, variant, axle code,
season code, size, rims): one row per approved wheel. Rows that differ by
gearbox alone stay one row (the same wheel).

**Data.** None to move. Every existing row is unique under the old key, so
it is unique under the wider one; its rim columns stay NULL until the
Betrieb's next «Neu laden» of that car (ADR-123: no automatic refresh).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9a896524725"
down_revision: str | Sequence[str] | None = "ce1e6fdf5e2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "vehicle_tyre_spec_cache"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("rims", sa.String(length=32), nullable=True))
    op.add_column(_TABLE, sa.Column("rim_width", sa.DECIMAL(precision=4, scale=1), nullable=True))
    op.add_column(_TABLE, sa.Column("rim_size", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("rim_offset", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("is_standard", sa.Boolean(), nullable=True))
    op.drop_constraint("uq_vehicle_tyre_spec_cache_tenant_variant_raw_codes", _TABLE, type_="unique")
    op.create_unique_constraint(
        "uq_vehicle_tyre_spec_cache_tenant_variant_wheel",
        _TABLE,
        ["tenant_id", "model_variant_id", "axle_code", "season_code", "size", "rims"],
    )


def downgrade() -> None:
    # The narrower key cannot hold two wheels of one axle and season: the
    # rows past the first per (tenant, variant, axle code, season code) go —
    # a cache, reloaded by «Neu laden», not a record of anyone's work.
    op.execute(
        f"""
        DELETE FROM {_TABLE} t
        USING {_TABLE} keep
        WHERE t.tenant_id = keep.tenant_id
          AND t.model_variant_id = keep.model_variant_id
          AND t.axle_code IS NOT DISTINCT FROM keep.axle_code
          AND t.season_code IS NOT DISTINCT FROM keep.season_code
          AND t.id > keep.id
        """
    )
    op.drop_constraint("uq_vehicle_tyre_spec_cache_tenant_variant_wheel", _TABLE, type_="unique")
    op.create_unique_constraint(
        "uq_vehicle_tyre_spec_cache_tenant_variant_raw_codes",
        _TABLE,
        ["tenant_id", "model_variant_id", "axle_code", "season_code"],
    )
    op.drop_column(_TABLE, "is_standard")
    op.drop_column(_TABLE, "rim_offset")
    op.drop_column(_TABLE, "rim_size")
    op.drop_column(_TABLE, "rim_width")
    op.drop_column(_TABLE, "rims")
