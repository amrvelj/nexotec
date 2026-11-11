"""KAN-587: the size class resolves through the code map; the type-approval class gets its own field

Anto's ruling (2026-11-10, option A): «Fahrzeugklasse» (`vehicle_class`) is
the **size class** auto-i-dat sends for every identified passenger car — the
identAPI's `category`, 1 Micro … 6 Luxury — and the type-approval class on the
Fahrzeugausweis (M1, N1, L3e, L1e), which that list held until now, gets its
own list and field, `homologation_class`. The platform chain's `5d1f8a3c7e42`
reshapes the two reference lists; this migration does the vehicle context's
half. It reads no platform table, so the two chains may run in either order.

**Schema.** `homologation_class` (nullable, `String(64)`) on the two column
carriers of the specification block (ADR-071): `vehicle_model_variant` and
`vehicle_configuration`.

**Data**, in this order — plain DML with its constants bound on the statement,
so an offline `--sql` render writes the values (KAN-174):

1. **Stored type-approval classes move to the new field, never dropped.** A
   variant or configuration whose `vehicle_class` is `m1` / `n1` / `l3e` /
   `l1e` gets that value in `homologation_class` and `vehicle_class` NULL; its
   version moves once. On a configuration, an override mark on `vehicle_class`
   (`overridden_fields`: the advisor typed the class over the catalogue's)
   moves with the value, so the size class is free to be filled below.
2. **A code-map row that maps a `vehicle_class` code to a type-approval class
   goes, and its gap reopens.** Its target left the list, so no resolve could
   write it today (`resolve_mapping_gap` checks the list). An admin's mapping
   normally outranks a seed (`7c4e9a2b6d13`); this one names a value the list
   no longer has.
3. **The six `auto_i_dat` rows are seeded** (vehicle kind `01`, code group
   `vehicle_class`, codes 1–6 → `micro`, `city`, `compact`, `large`,
   `executive`, `luxury`), insert-if-absent: a row an admin wrote for one of
   these keys onto a value the list still has is left alone and reported.
   Passenger cars only — `FzKlasse` is absent for commercials and motorcycles
   (PRD-Configurator §The canonical code map).
4. **Every open `vehicle_class` gap whose code is now mapped is resolved** to
   the mapped value, with no resolving user (a system write).
5. **What the gaps left NULL is filled** (the KAN-83 / KAN-240 rule a resolve
   follows): each variant whose recorded raw `category` is now mapped, then
   each provider configuration built from such a variant — its NULL only,
   never a set value, never a field the advisor overrode, never a host's
   frozen snapshot (ADR-041).

**What this does not do.** No audit row and no `configuration.updated` event
for the configurations step 5 fills or step 1 moves: `audit_event` and the
outbox are `core`'s tables, and a migration writes its own context's tables
only — this migration is the record. Hosts' frozen snapshots keep what they
froze: a snapshot taken while `vehicleClass` read `m1` still says so.
The demo provider (`auto_i_dat_mock`) is not seeded here, as before:
`scripts/seed_demo_provider_code_map.py` copies the real provider's rows.

**Downgrade** puts the stored type-approval classes back into `vehicle_class`
and drops the column. A size class has no place in the old list, so the stored
size classes are cleared and the six seeded rows and their gaps' resolutions
are undone — deliberately lossy for the size class, which the next lookup
records again as a gap.

Revision ID: 8e2b6c4f1a97
Revises: bb6e760d9464
Create Date: 2026-11-11 05:10:00.000000

"""

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import context, op
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "8e2b6c4f1a97"
down_revision: str | Sequence[str] | None = "bb6e760d9464"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER = "auto_i_dat"
PASSENGER_CAR_KIND = "01"  # the raw `FzArt`, as `ProviderCodeMap` is keyed
CODE_GROUP = "vehicle_class"

# The type-approval classes `vehicle_class` held until KAN-587.
HOMOLOGATION_CODES: tuple[str, ...] = ("m1", "n1", "l3e", "l1e")

# The identAPI's `category` → the canonical size class (platform `5d1f8a3c7e42`).
SIZE_CLASS_BY_CATEGORY: dict[str, str] = {
    "1": "micro", "2": "city", "3": "compact", "4": "large", "5": "executive", "6": "luxury",
}
SIZE_CODES: tuple[str, ...] = tuple(SIZE_CLASS_BY_CATEGORY.values())

_CARRIERS: tuple[str, ...] = ("vehicle_model_variant", "vehicle_configuration")

# The mapping a gap's or a recorded raw code's key resolves to, correlated on `{row}`.
_MAPPED_VALUE = (
    "SELECT m.canonical_value_code FROM vehicle_provider_code_map AS m"
    " WHERE m.provider = {row}.provider AND m.vehicle_kind = {row}.vehicle_kind"
    " AND m.code_group = {row}.code_group AND m.provider_code = {row}.provider_code"
)
# A variant's size class as its recorded raw `category` now maps.
_VARIANT_SIZE_CLASS = (
    "SELECT m.canonical_value_code FROM vehicle_variant_provider_code AS c"
    " JOIN vehicle_provider_code_map AS m ON m.provider = c.provider AND m.vehicle_kind = c.vehicle_kind"
    "  AND m.code_group = c.code_group AND m.provider_code = c.provider_code"
    " WHERE c.model_variant_id = vehicle_model_variant.id AND c.code_group = :code_group"
    " ORDER BY c.provider LIMIT 1"
)


def _rename_override_mark(old: str, new: str) -> str:
    """`overridden_fields` (a JSON array of field names) with `old` renamed to `new`, order kept."""

    return (
        f"(SELECT COALESCE(json_agg(CASE WHEN marked.field = '{old}' THEN '{new}' ELSE marked.field END"
        " ORDER BY marked.position), '[]'::json)"
        " FROM json_array_elements_text(overridden_fields) WITH ORDINALITY AS marked(field, position))"
    )


class Result(NamedTuple):
    variants_moved: int
    configurations_moved: int
    stale_mappings_removed: int
    gaps_reopened: int
    seeded: int
    gaps_resolved: int
    variants_filled: int
    configurations_filled: int


def _statement(sql: str, **extra: object) -> sa.TextClause:
    """The step's constants bound on the statement itself, so an offline
    `--sql` render (`literal_binds`, `alembic/env.py`) writes their values;
    parameters passed to `execute` alongside would render as NULL there."""

    params = {
        "provider": sa.bindparam("provider", PROVIDER),
        "kind": sa.bindparam("kind", PASSENGER_CAR_KIND),
        "code_group": sa.bindparam("code_group", CODE_GROUP),
        "homologation_codes": sa.bindparam("homologation_codes", list(HOMOLOGATION_CODES), expanding=True),
        "size_codes": sa.bindparam("size_codes", list(SIZE_CODES), expanding=True),
        **{name: sa.bindparam(name, value) for name, value in extra.items()},
    }
    return sa.text(sql).bindparams(*(param for name, param in params.items() if f":{name}" in sql))


def _run(conn: Connection, sql: str, **extra: object) -> int:
    result = conn.execute(_statement(sql, **extra))
    # Offline (`--sql`) nothing ran: `execute` returns None.
    return result.rowcount if result is not None else 0


def apply_data(conn: Connection) -> Result:
    """The upgrade's five data steps. Takes a connection so the tests can run
    it against the create_all schema without Alembic. Postgres only (the
    `overridden_fields` rewrite is JSON SQL)."""

    # 1. Stored type-approval classes move to their own field.
    variants_moved = _run(
        conn,
        "UPDATE vehicle_model_variant SET homologation_class = vehicle_class, vehicle_class = NULL,"
        " version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE vehicle_class IN :homologation_codes",
    )
    configurations_moved = _run(
        conn,
        "UPDATE vehicle_configuration SET homologation_class = vehicle_class, vehicle_class = NULL,"
        f" overridden_fields = {_rename_override_mark('vehicle_class', 'homologation_class')},"
        " version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE vehicle_class IN :homologation_codes",
    )

    # 2. A mapping onto a type-approval class names a value the list no longer has.
    stale_mappings_removed = _run(
        conn,
        "DELETE FROM vehicle_provider_code_map"
        " WHERE code_group = :code_group AND canonical_value_code IN :homologation_codes",
    )
    gaps_reopened = _run(
        conn,
        "UPDATE vehicle_mapping_gap SET resolved = false, resolved_at = NULL, resolved_value_code = NULL,"
        " resolved_by = NULL, updated_at = CURRENT_TIMESTAMP"
        " WHERE code_group = :code_group AND resolved AND resolved_value_code IN :homologation_codes",
    )

    # 3. The six size classes, insert-if-absent.
    seeded = 0
    for category, size_class in SIZE_CLASS_BY_CATEGORY.items():
        seeded += _run(
            conn,
            "INSERT INTO vehicle_provider_code_map (id, provider, vehicle_kind, code_group, provider_code,"
            " canonical_list_code, canonical_value_code, created_at, updated_at)"
            " SELECT CAST(:id AS UUID), :provider, :kind, :code_group, :category, :code_group, :size_class,"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
            " WHERE NOT EXISTS (SELECT 1 FROM vehicle_provider_code_map AS held WHERE held.provider = :provider"
            "  AND held.vehicle_kind = :kind AND held.code_group = :code_group AND held.provider_code = :category)",
            id=str(uuid7()),
            category=category,
            size_class=size_class,
        )

    # 4. Open gaps whose code is now mapped are resolved to the mapped value.
    mapped = _MAPPED_VALUE.format(row="vehicle_mapping_gap")
    gaps_resolved = _run(
        conn,
        "UPDATE vehicle_mapping_gap SET resolved = true, resolved_at = CURRENT_TIMESTAMP,"
        f" resolved_value_code = ({mapped}), resolved_by = NULL, updated_at = CURRENT_TIMESTAMP"
        f" WHERE code_group = :code_group AND NOT resolved AND EXISTS ({mapped})",
    )

    # 5. What the gaps left NULL: the variants, then the provider configurations built from them.
    variants_filled = _run(
        conn,
        f"UPDATE vehicle_model_variant SET vehicle_class = ({_VARIANT_SIZE_CLASS}),"
        " version = version + 1, updated_at = CURRENT_TIMESTAMP"
        f" WHERE vehicle_class IS NULL AND EXISTS ({_VARIANT_SIZE_CLASS})",
    )
    configurations_filled = _run(
        conn,
        "UPDATE vehicle_configuration SET vehicle_class = (SELECT v.vehicle_class FROM vehicle_model_variant AS v"
        "  WHERE v.id = vehicle_configuration.catalogue_variant_id),"
        " version = version + 1, updated_at = CURRENT_TIMESTAMP, updated_by = NULL"
        " WHERE source = 'provider' AND vehicle_class IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM json_array_elements_text(overridden_fields) AS marked(field)"
        "  WHERE marked.field = 'vehicle_class')"
        " AND EXISTS (SELECT 1 FROM vehicle_model_variant AS v"
        "  JOIN vehicle_variant_provider_code AS c ON c.model_variant_id = v.id AND c.code_group = :code_group"
        "  WHERE v.id = vehicle_configuration.catalogue_variant_id AND v.vehicle_class IS NOT NULL)",
    )
    return Result(
        variants_moved, configurations_moved, stale_mappings_removed, gaps_reopened,
        seeded, gaps_resolved, variants_filled, configurations_filled,
    )


def revert_data(conn: Connection) -> None:
    """The downgrade's data steps, before the column is dropped."""

    _run(
        conn,
        "UPDATE vehicle_mapping_gap SET resolved = false, resolved_at = NULL, resolved_value_code = NULL,"
        " resolved_by = NULL, updated_at = CURRENT_TIMESTAMP"
        " WHERE code_group = :code_group AND resolved AND resolved_value_code IN :size_codes",
    )
    for category, size_class in SIZE_CLASS_BY_CATEGORY.items():
        _run(
            conn,
            "DELETE FROM vehicle_provider_code_map WHERE provider = :provider AND vehicle_kind = :kind"
            " AND code_group = :code_group AND provider_code = :category"
            " AND canonical_list_code = :code_group AND canonical_value_code = :size_class",
            category=category,
            size_class=size_class,
        )
    _run(
        conn,
        "UPDATE vehicle_model_variant SET vehicle_class = homologation_class,"
        " version = version + 1, updated_at = CURRENT_TIMESTAMP"
        " WHERE homologation_class IS NOT NULL OR vehicle_class IN :size_codes",
    )
    _run(
        conn,
        "UPDATE vehicle_configuration SET vehicle_class = homologation_class,"
        f" overridden_fields = {_rename_override_mark('homologation_class', 'vehicle_class')},"
        " version = version + 1, updated_at = CURRENT_TIMESTAMP"
        " WHERE homologation_class IS NOT NULL OR vehicle_class IN :size_codes",
    )


def upgrade() -> None:
    for table in _CARRIERS:
        op.add_column(table, sa.Column("homologation_class", sa.String(length=64), nullable=True))
    result = apply_data(op.get_bind())
    if context.is_offline_mode():
        return
    print(
        f"KAN-587: moved {result.variants_moved} variant and {result.configurations_moved} configuration "
        f"type-approval classes to homologation_class; removed {result.stale_mappings_removed} code-map rows "
        f"onto a type-approval class ({result.gaps_reopened} gaps reopened); seeded {result.seeded} of "
        f"{len(SIZE_CLASS_BY_CATEGORY)} size-class rows; resolved {result.gaps_resolved} gaps; filled "
        f"{result.variants_filled} variants and {result.configurations_filled} configurations."
    )
    if result.seeded < len(SIZE_CLASS_BY_CATEGORY):
        held = op.get_bind().execute(
            _statement(
                "SELECT provider_code, canonical_list_code, canonical_value_code FROM vehicle_provider_code_map"
                " WHERE provider = :provider AND vehicle_kind = :kind AND code_group = :code_group"
                " ORDER BY provider_code"
            )
        )
        for provider_code, list_code, value_code in held:
            seed = SIZE_CLASS_BY_CATEGORY.get(provider_code)
            if seed is not None and (list_code, value_code) != (CODE_GROUP, seed):
                print(
                    f"KAN-587: NOT overwritten — category {provider_code} is already mapped to "
                    f"{list_code}/{value_code} (seed: {CODE_GROUP}/{seed})."
                )


def downgrade() -> None:
    revert_data(op.get_bind())
    for table in _CARRIERS:
        op.drop_column(table, "homologation_class")
