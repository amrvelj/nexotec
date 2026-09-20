"""KAN-38 (Configurator C-0) PR 2c: the auto_i_dat ``provider_code_map`` seed.

Tests build the schema with ``create_all``, never Alembic, so — like the canonical-list tests — they load the
migration by path and assert on its constants, then run its own ``_seed`` helper against the test schema. The
seeded map is opt-in per test and never autouse: ``test_vehicle_catalogue_sync`` and
``test_configurator_spec_block`` both rely on an empty map.
"""

import importlib.util
from pathlib import Path

from sqlalchemy import func, select

from app.vehicle.models.provider import ProviderCodeMap
from app.vehicle.services.provider import resolve_provider_code

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
PROVIDER = "auto_i_dat"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_migration():
    (path,) = sorted((_VERSIONS / "vehicle").glob("7c4e9a2b6d13_*.py"))
    return _load(path, "kan38_pr2c_code_map_seed")


def _canonical_values() -> dict[str, set[str]]:
    """Every canonical list -> value codes, from the migrations that seed them. Four of the lists this map
    targets live in the trunk migration (a differently shaped constant, no English), three in the platform
    branch — a loader that globbed only ``platform/`` would silently miss 21 values.
    """

    values: dict[str, set[str]] = {}
    trunk = _load(_VERSIONS / "c9654d846ac9_reference_list_and_reference_value_.py", "kan38_trunk_lists")
    for list_code, rows in trunk.SEED_LISTS.items():
        values.setdefault(list_code, set()).update(row[0] for row in rows)
    for name in (
        "platform/6ba0a99ed5c4_vehicle_catalogue_reference_lists.py",
        "platform/a3d9c1e58f27_configurator_canonical_lists.py",
    ):
        module = _load(_VERSIONS / name, f"kan38_{Path(name).stem}")
        for list_code, rows in module.NEW_LISTS:
            values.setdefault(list_code, set()).update(row[0] for row in rows)
    return values


def _rows_for(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(ProviderCodeMap).where(ProviderCodeMap.provider == PROVIDER))


# --- R-C-5: 112 is a stroke count, never a drive layout -----------------------------------------------------------


def test_112_maps_to_engine_cycle_and_never_to_drivetrain():
    migration = _seed_migration()
    groups = {group: (kinds, code_group, mapping) for group, kinds, code_group, mapping in migration.GROUPS}

    # The ticket's criterion: 112 -> engine_cycle, with its three printed codes, and nothing else feeds that list.
    assert groups["112"] == (("03",), "engine_cycle", {"2": "two_stroke", "4": "four_stroke", "9": "no_stroke"})
    assert [g for g, (_k, code_group, _m) in groups.items() if code_group == "engine_cycle"] == ["112"]

    # No motorcycle (FzArt 03) has a drivetrain row, and drivetrain holds only the driven-axle codes of cars/LCVs.
    drivetrain = [(kind, code) for kind, code_group, code, _l, _v in migration.SEED_ROWS if code_group == "drivetrain"]
    assert {kind for kind, _code in drivetrain} == {"01", "02"}
    assert {code for _kind, code in drivetrain} == {"1", "2", "5"}


# --- every canonical target exists --------------------------------------------------------------------------------


def test_every_canonical_target_exists_in_the_canonical_seed_constants():
    """Nothing validates a map row's target at runtime (no FK, and the resolver returns it unchecked), so a
    mistyped canonical value_code would only surface as a wrong value on a synced variant."""

    canonical = _canonical_values()
    rows = _seed_migration().SEED_ROWS
    for list_code in {list_code for _k, _cg, _c, list_code, _v in rows}:  # a vacuous pass is impossible
        assert canonical.get(list_code), f"canonical list {list_code!r} not found in the seed constants"
    for kind, code_group, provider_code, list_code, value_code in rows:
        assert value_code in canonical[list_code], (kind, code_group, provider_code, list_code, value_code)


# --- behaviour on the test schema, through the real resolver ------------------------------------------------------


def test_seed_inserts_every_row(db_session):
    migration = _seed_migration()

    result = migration._seed(db_session.connection())

    assert result.inserted == len(migration.SEED_ROWS)
    assert _rows_for(db_session) == len(migration.SEED_ROWS)


def test_rows_read_back_through_the_resolver_including_the_treibstoff_3_collision(db_session):
    _seed_migration()._seed(db_session.connection())

    def resolve(kind, code_group, code):
        return resolve_provider_code(
            db_session, provider=PROVIDER, vehicle_kind=kind, code_group=code_group, provider_code=code
        )

    # The model docstring's own example: the same (provider, group, code) means two different things by kind.
    assert resolve("01", "fuel_type", "3").value_code == "diesel"
    assert resolve("03", "fuel_type", "3").value_code == "petrol"
    # Antrieb: 2 is a driven axle for a car and a stroke count for a motorcycle.
    assert resolve("01", "drivetrain", "2").value_code == "fwd"
    assert resolve("03", "engine_cycle", "2").value_code == "two_stroke"
    # A stroke code asked for under drivetrain finds nothing — it becomes a gap, never a wrong value.
    assert resolve("03", "drivetrain", "2") is None
    # FzArt is self-referential, exactly as catalogue_sync calls it.
    assert resolve("03", "vehicle_kind", "03").value_code == "motorcycle"
    # A kind-independent vocabulary resolves under every kind.
    assert {resolve(kind, "equipment_feature", "2").value_code for kind in ("01", "02", "03")} == {"air_conditioning"}


def test_unseed_removes_what_seed_inserted(db_session):
    migration = _seed_migration()
    migration._seed(db_session.connection())

    removed = migration._unseed(db_session.connection())

    assert removed == len(migration.SEED_ROWS)
    assert _rows_for(db_session) == 0
