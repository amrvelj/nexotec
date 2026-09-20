"""seed the auto_i_dat provider_code_map (KAN-38, Configurator C-0 PR 2c)

Data only — no schema change. Maps auto-i-dat's coded-field vocabulary (the
"Kodierte Felder" tables on pp.32-34 of *Webservice Fahrzeuge*, footer date
22.06.2021) onto Nexotec's canonical reference values, for the real
`auto_i_dat` provider only. The mock provider stays unseeded on purpose.

**Keying.** The one the only production reader uses
(`app/vehicle/services/catalogue_sync.py`): `provider` = the integration
provider_code; `vehicle_kind` = the RAW `FzArt` ('01' Personenwagen, '02'
Nutzfahrzeuge, '03' Motorräder); `code_group` = the semantic field name, which
is also the canonical list's code; `provider_code` = the raw code. A numeric
`CodeGrpNr` is never stored (the table has no column for it) — the per-kind
pattern (010/020/110, 011/021/111, 012/022/112, …) lives in `GROUPS` below,
and a kind-independent vocabulary is stored once per kind because the resolver
matches `vehicle_kind` exactly and has no wildcard.

**What is — and is not — seeded.** Only mappings where the provider's meaning
and the canonical value are the same thing (identity or strict subset). Every
ambiguous code, and every code with no canonical value, is deliberately left
out so the resolver writes a `mapping_gap` for an admin: a code with no row is
"never auto-mapped and never dropped" (FR-C-10). The reason for each of the
115 unmapped `(CodeGrpNr, code)` pairs is recorded next to the spec snapshot in
`scripts/auto_i_dat_code_snapshot.py`, and a test asserts that the seeded pairs
and the recorded pairs together are exactly the spec's 191. Not seeded at all:
`PneuTyp` (500), `AchsenCode` (550) and `FarbArt` — the adapter hard-codes them,
so a map row would be a second definition of the same fact.

**`112 → engine_cycle`.** `Antrieb` is one wire field with three code groups.
012 / 022 (cars / light commercials) are a driven axle; **112 (motorcycles) is
a stroke count — 2 Takt / 4 Takt / Kein Takt — and maps to `engine_cycle`,
never `drivetrain`.** Code 2 collides ("Vorne" in 012/022, "2 Takt" in 112),
which is why `vehicle_kind` is part of the key. These rows stay inert until the
sync routes `Antrieb` by vehicle kind (KAN-38 PR 2d): today it resolves every
kind under `drivetrain`, so a motorcycle's stroke code simply becomes a gap —
it can never land in `drivetrain` from this seed.

**Anomaly, reproduced not repaired.** Groups 021 and 111 print code 14 twice
and no code 15 (in the rendered page and in the text layer). They are treated
as the distinct codes {…, 14, 16}; no code 15 is invented, and a live 15 simply
becomes a gap.

**Provenance and limits.** Codes and our canonical values only — no provider
label text. It is a 2021 snapshot; nothing in the spec says it equals the live
`Codes` result, and no staging account exists to compare. **Go-live gate:** the
seed is inert until an account exists, and before any real tenant syncs,
`scripts/verify_auto_i_dat.py` must show the live `Codes` against the snapshot
and that the provider selects the code group by `FzArt` and not `FzArtExtern`
(spec p34 is silent) — `body_style` is the one seeded group whose meaning
differs between kinds 01 and 02.

**Re-runs and admin rows.** Insert-if-absent by natural key. The resolver's
gap-resolve endpoint also writes this table, so a row may already exist: an
identical one is left, and one that *disagrees* is left alone and reported
loudly — an admin's decision wins over a default, and a deploy never fails on
it. `downgrade()` removes only rows whose target still equals the seeded one.
The seed data is frozen here by design (a migration must keep doing what it did
the day it ran); a later correction ships as a new migration.

Revision ID: 7c4e9a2b6d13
Revises: 90dc1077a569
Create Date: 2026-09-20 11:01:22.895805

"""

from typing import NamedTuple, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "7c4e9a2b6d13"
down_revision: Union[str, Sequence[str], None] = "90dc1077a569"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROVIDER = "auto_i_dat"
ALL_KINDS: tuple[str, ...] = ("01", "02", "03")

# `FzArt` has no CodeGrpNr; the lookup is self-referential (the raw FzArt is
# both the kind qualifier and the code), exactly as `catalogue_sync` calls it.
FZART: dict[str, str] = {"01": "passenger_car", "02": "light_commercial", "03": "motorcycle"}

# (CodeGrpNr, FzArt kinds it applies to, code_group == canonical list, {code: canonical value}).
# One entry per numbered group that gets any rows. The groups that do not
# (014, 110, 500, 550) are recorded, with reasons, in
# scripts/auto_i_dat_code_snapshot.py.
GROUPS: tuple[tuple[str, tuple[str, ...], str, dict[str, str]], ...] = (
    ("010", ("01",), "body_style", {"1": "suv", "3": "coupe", "4": "van", "5": "estate", "6": "sedan", "9": "convertible"}),
    ("020", ("02",), "body_style", {"1": "suv", "7": "pickup"}),
    (
        "011",
        ("01",),
        "fuel_type",
        {
            "1": "petrol", "2": "petrol", "3": "diesel", "4": "diesel", "9": "electric", "10": "hydrogen",
            "12": "plugin_hybrid", "13": "hybrid", "15": "plugin_hybrid", "16": "hybrid",
        },
    ),
    (
        "021",  # as 011, minus code 15 — not printed in this group (see the anomaly note above)
        ("02",),
        "fuel_type",
        {
            "1": "petrol", "2": "petrol", "3": "diesel", "4": "diesel", "9": "electric", "10": "hydrogen",
            "12": "plugin_hybrid", "13": "hybrid", "16": "hybrid",
        },
    ),
    (
        "111",  # motorcycles: codes 1-5 differ from cars (3 is petrol here, diesel for a car)
        ("03",),
        "fuel_type",
        {
            "1": "petrol", "2": "petrol", "3": "petrol", "4": "petrol", "5": "diesel", "9": "electric",
            "10": "hydrogen", "12": "plugin_hybrid", "13": "hybrid", "16": "hybrid",
        },
    ),
    ("012", ("01",), "drivetrain", {"1": "rwd", "2": "fwd", "5": "awd"}),
    ("022", ("02",), "drivetrain", {"1": "rwd", "2": "fwd", "5": "awd"}),
    # R-C-5: a stroke count, never a drive layout. Do not merge with the two groups above.
    ("112", ("03",), "engine_cycle", {"2": "two_stroke", "4": "four_stroke", "9": "no_stroke"}),
    ("013", ("01",), "transmission", {"1": "manual", "5": "automatic", "6": "cvt"}),
    ("023", ("02",), "transmission", {"1": "manual", "5": "automatic", "6": "cvt"}),
    (
        "045",
        ALL_KINDS,
        "equipment_feature",
        {
            "1": "navigation", "2": "air_conditioning", "3": "leather_seats", "4": "sunroof", "5": "cruise_control",
            "29": "parking_sensors", "31": "air_conditioning", "32": "cruise_control", "43": "parking_sensors",
        },
    ),
    ("041", ALL_KINDS, "valuation_classification", {"0": "definitive", "1": "none", "2": "provisional", "4": "not_yet"}),
    (
        "047",
        ALL_KINDS,
        "option_relation_type",
        {
            "1": "not_with", "2": "only_with", "3": "price_in_combination_with", "4": "becomes_standard_with",
            "5": "not_in_combination_with", "6": "only_in_combination_with",
        },
    ),
    ("065", ALL_KINDS, "consumption_norm", {"3": "nedc", "4": "wltp"}),
    ("046", ALL_KINDS, "option_group", {"2": "exterior", "3": "safety", "5": "interior"}),
)


def _build_rows() -> list[tuple[str, str, str, str, str]]:
    """(vehicle_kind, code_group, provider_code, canonical_list_code, canonical_value_code)."""

    rows = [(code, "vehicle_kind", code, "vehicle_kind", value) for code, value in FZART.items()]
    for _group_nr, kinds, code_group, mapping in GROUPS:
        for kind in kinds:
            for provider_code, value in mapping.items():
                rows.append((kind, code_group, provider_code, code_group, value))
    return rows


SEED_ROWS: list[tuple[str, str, str, str, str]] = _build_rows()


class SeedResult(NamedTuple):
    inserted: int
    identical: int
    # ((vehicle_kind, code_group, provider_code), existing target, seed target)
    conflicting: list[tuple[tuple[str, str, str], tuple[str, str], tuple[str, str]]]


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
    it against the create_all schema without Alembic.
    """

    table = _table()
    existing = {
        (row.vehicle_kind, row.code_group, row.provider_code): (row.canonical_list_code, row.canonical_value_code)
        for row in conn.execute(
            sa.select(
                table.c.vehicle_kind, table.c.code_group, table.c.provider_code,
                table.c.canonical_list_code, table.c.canonical_value_code,
            ).where(table.c.provider == PROVIDER)
        )
    }
    now = sa.func.now()
    inserted = identical = 0
    conflicting: list[tuple[tuple[str, str, str], tuple[str, str], tuple[str, str]]] = []
    for kind, code_group, provider_code, list_code, value_code in SEED_ROWS:
        key = (kind, code_group, provider_code)
        target = (list_code, value_code)
        if key in existing:
            if existing[key] == target:
                identical += 1
            else:
                conflicting.append((key, existing[key], target))
            continue
        conn.execute(
            table.insert().values(
                id=uuid7(), provider=PROVIDER, vehicle_kind=kind, code_group=code_group,
                provider_code=provider_code, canonical_list_code=list_code, canonical_value_code=value_code,
                created_at=now, updated_at=now,
            )
        )
        inserted += 1
    return SeedResult(inserted, identical, conflicting)


def _unseed(conn: Connection) -> int:
    """Delete only rows whose target still equals the seeded one — an admin who
    re-pointed a key keeps their decision.
    """

    table = _table()
    removed = 0
    for kind, code_group, provider_code, list_code, value_code in SEED_ROWS:
        result = conn.execute(
            table.delete().where(
                sa.and_(
                    table.c.provider == PROVIDER,
                    table.c.vehicle_kind == kind,
                    table.c.code_group == code_group,
                    table.c.provider_code == provider_code,
                    table.c.canonical_list_code == list_code,
                    table.c.canonical_value_code == value_code,
                )
            )
        )
        removed += result.rowcount
    return removed


def upgrade() -> None:
    result = _seed(op.get_bind())
    print(
        f"KAN-38 PR 2c: seeded {result.inserted} auto_i_dat code-map rows "
        f"({result.identical} already present, {len(result.conflicting)} left as they were)."
    )
    for key, existing_target, seed_target in result.conflicting:
        print(f"KAN-38 PR 2c: NOT overwritten {key}: existing mapping {existing_target}, seed {seed_target}.")


def downgrade() -> None:
    removed = _unseed(op.get_bind())
    print(f"KAN-38 PR 2c: removed {removed} seeded auto_i_dat code-map rows.")
