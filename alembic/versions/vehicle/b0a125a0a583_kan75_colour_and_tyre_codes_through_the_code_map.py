"""KAN-75 — FarbArt, AchsenCode and PneuTyp resolve through the code map

**Reverses `7c4e9a2b6d13`'s rationale, on purpose.** That seed left these three
codes out because "the adapter hard-codes them, so a map row would be a second
definition of the same fact". The hard-coding is what this ticket removes: the
adapter guessed (`FarbArt` unknown → `exterior`, `AchsenCode` missing →
`front`, unknown → an invented `variant_<n>`, `PneuTyp` unknown → no season)
where every other coded field writes a `mapping_gap` (FR-C-10, never
auto-mapped, never dropped). Anto's ruling (2026-10-12, option A) routes them
through `ProviderCodeMap` like every other coded field, and his answers of
2026-10-23 settle the rest: an unclassified colour is hidden until its gap is
resolved (A2); a resolved gap fills the stored colours and tyres at once (B1,
which needs the raw code kept beside each row); the mock provider is seeded
and returns raw codes like the real service (C1).

**Schema.**
  * `vehicle_colour_cache.colour_type` becomes nullable (the resolved
    `colour_area` value) and gains `colour_area_code`, the raw `FarbArt`.
  * `vehicle_tyre_spec_cache.axle` becomes nullable (the resolved
    `axle_position` value) and gains `axle_code` and `season_code`, the raw
    `AchsenCode` and `PneuTyp`. The row's key moves from (axle, season) to
    (axle_code, season_code): an unresolved code has no value to key on, and
    two raw codes an admin maps onto one value must stay two rows.

**Backfill.** The cache rows were written by the inline decode, so their raw
codes are recovered from the decoded value: exterior/interior → 1/2,
both/front/rear → 1/2/3, `variant_<n>` → n (its `axle` cleared: no canonical
value exists for it, so the next sync files the gap), summer/winter →
121/122. A row the old decode had defaulted (an unknown `FarbArt` stored as
`exterior`, a missing `AchsenCode` as `front`) cannot be told apart and gets
the code of the value it shows; the next sync of the variant writes what the
provider really sends. No staging auto-i-dat account exists, so every row
today came from the mock provider, whose codes are exactly these.

**Seed.** Insert-if-absent by natural key, as `7c4e9a2b6d13`: an admin's row
that disagrees is left alone and reported. Seven codes per vehicle kind —
`FarbArt` 1/2 → `colour_area`, `AchsenCode` 1/2/3 → `axle_position`,
`PneuTyp` 121/122 → `tyre_type` — for `auto_i_dat` under every FzArt it
seeds (01, 02, 03) and for `auto_i_dat_mock` under the FzArt its demo
variants carry ("1"). The canonical values are the platform branch's
`b13e074523d7`; no `depends_on` ties the two: the seed reads no reference
value (as `7c4e9a2b6d13`), and a dependency on another branch's head would
leave that head out of `alembic_version`. `downgrade()` removes only rows
whose target still equals the seeded one.

Revision ID: b0a125a0a583
Revises: a0719737860e
Create Date: 2026-10-28 05:10:00.000000

"""

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from alembic import context, op
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "b0a125a0a583"
down_revision: str | Sequence[str] | None = "a0719737860e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (code_group == canonical list, {raw code: canonical value})
CODES: tuple[tuple[str, dict[str, str]], ...] = (
    ("colour_area", {"1": "exterior", "2": "interior"}),  # FarbArt: 1 Aussenfarbe, 2 Polsterfarbe
    ("axle_position", {"1": "both", "2": "front", "3": "rear"}),  # AchsenCode 1 Vorne+Hinten, 2 Vorne, 3 Hinten
    ("tyre_type", {"121": "summer", "122": "winter"}),  # PneuTyp (CodeGrpNr 500)
)
# provider -> the raw FzArt values its variants carry.
PROVIDER_KINDS: dict[str, tuple[str, ...]] = {"auto_i_dat": ("01", "02", "03"), "auto_i_dat_mock": ("1",)}


def _build_rows() -> list[tuple[str, str, str, str, str]]:
    """(provider, vehicle_kind, code_group, provider_code, canonical_value_code)."""

    return [
        (provider, kind, code_group, provider_code, value)
        for provider, kinds in PROVIDER_KINDS.items()
        for kind in kinds
        for code_group, mapping in CODES
        for provider_code, value in mapping.items()
    ]


SEED_ROWS: list[tuple[str, str, str, str, str]] = _build_rows()

_UNIQUE_OLD = "uq_vehicle_tyre_spec_cache_tenant_variant_axle_season"
_UNIQUE_NEW = "uq_vehicle_tyre_spec_cache_tenant_variant_raw_codes"


class SeedResult(NamedTuple):
    inserted: int
    identical: int
    # ((provider, vehicle_kind, code_group, provider_code), existing target, seed target)
    conflicting: list[tuple[tuple[str, str, str, str], tuple[str, str], tuple[str, str]]]


def _table() -> sa.TableClause:
    return sa.table(
        "vehicle_provider_code_map",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("provider", sa.String()),
        sa.column("vehicle_kind", sa.String()),
        sa.column("code_group", sa.String()),
        sa.column("provider_code", sa.String()),
        sa.column("canonical_list_code", sa.String()),
        sa.column("canonical_value_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _seed(conn: Connection) -> SeedResult:
    """Insert-if-absent by natural key. Takes a connection so the tests can run
    it against the create_all schema without Alembic."""

    table = _table()
    providers = tuple(PROVIDER_KINDS)
    existing = {
        (row.provider, row.vehicle_kind, row.code_group, row.provider_code): (
            row.canonical_list_code,
            row.canonical_value_code,
        )
        for row in conn.execute(
            sa.select(
                table.c.provider, table.c.vehicle_kind, table.c.code_group, table.c.provider_code,
                table.c.canonical_list_code, table.c.canonical_value_code,
            ).where(table.c.provider.in_(providers))
        )
    }
    now = sa.func.now()
    inserted = identical = 0
    conflicting: list[tuple[tuple[str, str, str, str], tuple[str, str], tuple[str, str]]] = []
    for provider, kind, code_group, provider_code, value_code in SEED_ROWS:
        key = (provider, kind, code_group, provider_code)
        target = (code_group, value_code)
        if key in existing:
            if existing[key] == target:
                identical += 1
            else:
                conflicting.append((key, existing[key], target))
            continue
        conn.execute(
            table.insert().values(
                id=uuid7(), provider=provider, vehicle_kind=kind, code_group=code_group,
                provider_code=provider_code, canonical_list_code=code_group, canonical_value_code=value_code,
                created_at=now, updated_at=now,
            )
        )
        inserted += 1
    return SeedResult(inserted, identical, conflicting)


def _unseed(conn: Connection) -> int:
    """Delete only rows whose target still equals the seeded one — an admin who
    re-pointed a key keeps their decision."""

    table = _table()
    removed = 0
    for provider, kind, code_group, provider_code, value_code in SEED_ROWS:
        result = conn.execute(
            table.delete().where(
                sa.and_(
                    table.c.provider == provider,
                    table.c.vehicle_kind == kind,
                    table.c.code_group == code_group,
                    table.c.provider_code == provider_code,
                    table.c.canonical_list_code == code_group,
                    table.c.canonical_value_code == value_code,
                )
            )
        )
        removed += result.rowcount
    return removed


def _backfill(conn: Connection) -> None:
    """Recovers each cached row's raw codes from the value the inline decode
    stored (see the module docstring)."""

    conn.execute(
        sa.text(
            "UPDATE vehicle_colour_cache SET colour_area_code = CASE colour_type "
            "WHEN 'exterior' THEN '1' WHEN 'interior' THEN '2' END"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE vehicle_tyre_spec_cache SET "
            "axle_code = CASE WHEN axle = 'both' THEN '1' WHEN axle = 'front' THEN '2' WHEN axle = 'rear' THEN '3' "
            "WHEN axle LIKE 'variant\\_%' ESCAPE '\\' THEN substr(axle, 9) END, "
            "season_code = CASE season WHEN 'summer' THEN '121' WHEN 'winter' THEN '122' END"
        )
    )
    conn.execute(sa.text("UPDATE vehicle_tyre_spec_cache SET axle = NULL WHERE axle NOT IN ('both', 'front', 'rear')"))


def upgrade() -> None:
    op.add_column("vehicle_colour_cache", sa.Column("colour_area_code", sa.String(length=16), nullable=True))
    op.alter_column("vehicle_colour_cache", "colour_type", existing_type=sa.String(length=16), nullable=True)
    op.add_column("vehicle_tyre_spec_cache", sa.Column("axle_code", sa.String(length=16), nullable=True))
    op.add_column("vehicle_tyre_spec_cache", sa.Column("season_code", sa.String(length=16), nullable=True))
    op.alter_column("vehicle_tyre_spec_cache", "axle", existing_type=sa.String(length=16), nullable=True)
    op.drop_constraint(_UNIQUE_OLD, "vehicle_tyre_spec_cache", type_="unique")

    if context.is_offline_mode():
        op.execute("-- KAN-75 backfill and code-map seed skipped: they read rows and need a live database")
    else:
        conn = op.get_bind()
        _backfill(conn)
        result = _seed(conn)
        print(
            f"KAN-75: seeded {result.inserted} FarbArt/AchsenCode/PneuTyp code-map rows "
            f"({result.identical} already present, {len(result.conflicting)} left as they were)."
        )
        for key, existing_target, seed_target in result.conflicting:
            print(f"KAN-75: NOT overwritten {key}: existing mapping {existing_target}, seed {seed_target}.")

    op.create_unique_constraint(
        _UNIQUE_NEW, "vehicle_tyre_spec_cache", ["tenant_id", "model_variant_id", "axle_code", "season_code"]
    )


def downgrade() -> None:
    op.drop_constraint(_UNIQUE_NEW, "vehicle_tyre_spec_cache", type_="unique")
    conn = op.get_bind()
    removed = _unseed(conn)
    print(f"KAN-75: removed {removed} seeded FarbArt/AchsenCode/PneuTyp code-map rows.")
    # The old columns cannot hold "unresolved". These are cache rows the next
    # sync writes again; an unresolved colour goes, an unresolved axle takes
    # back its old spelling (`variant_<n>`, or `front` for a missing code).
    conn.execute(sa.text("DELETE FROM vehicle_colour_cache WHERE colour_type IS NULL"))
    conn.execute(
        sa.text(
            "UPDATE vehicle_tyre_spec_cache SET axle = CASE WHEN axle_code IS NULL THEN 'front' "
            "ELSE 'variant_' || axle_code END WHERE axle IS NULL"
        )
    )
    # Two raw codes an admin mapped onto one value would collide under the old
    # key: the older row stays.
    conn.execute(
        sa.text(
            "DELETE FROM vehicle_tyre_spec_cache t USING vehicle_tyre_spec_cache o "
            "WHERE t.tenant_id = o.tenant_id AND t.model_variant_id = o.model_variant_id "
            "AND t.axle = o.axle AND t.season IS NOT DISTINCT FROM o.season AND t.id > o.id"
        )
    )
    op.create_unique_constraint(
        _UNIQUE_OLD, "vehicle_tyre_spec_cache", ["tenant_id", "model_variant_id", "axle", "season"]
    )
    op.alter_column("vehicle_tyre_spec_cache", "axle", existing_type=sa.String(length=16), nullable=False)
    op.drop_column("vehicle_tyre_spec_cache", "season_code")
    op.drop_column("vehicle_tyre_spec_cache", "axle_code")
    op.alter_column("vehicle_colour_cache", "colour_type", existing_type=sa.String(length=16), nullable=False)
    op.drop_column("vehicle_colour_cache", "colour_area_code")
