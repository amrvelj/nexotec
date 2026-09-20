"""KAN-38 (Configurator C-0) PR 2c: the auto_i_dat ``provider_code_map`` seed.

Tests build the schema with ``create_all``, never Alembic, so — like the canonical-list tests — they load the
migration by path and assert on its constants, then run its own ``_seed`` helper against the test schema. The
seeded map is therefore opt-in per test and never autouse: ``test_vehicle_catalogue_sync`` and
``test_configurator_spec_block`` both rely on an empty map.

What these guard, in order: that the spec transcription is what was read (checksums), that every one of the
191 spec codes is either seeded or recorded as deliberately unmapped (so a new code forces a decision), that
``112`` is a stroke count and can never land in ``drivetrain`` (R-C-5 — the ticket's named exit criterion),
that every canonical target exists, and that the rows behave through the real resolver.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services.provider import resolve_provider_code
from scripts.auto_i_dat_code_snapshot import (
    ADAPTER_OWNS,
    AMBIGUOUS,
    NEEDS_CANONICAL,
    NO_TARGET,
    PRINTED_ROWS,
    SPEC_CODES,
    TOTAL_DISTINCT_PAIRS,
    TOTAL_PRINTED_ROWS,
    UNMAPPED,
)

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
    branch — a test that globbed only ``platform/`` would silently miss 21 values.
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


# --- the spec snapshot -------------------------------------------------------------------------------------


def test_the_snapshot_matches_the_transcription_checksums():
    assert len(SPEC_CODES) == 19
    assert set(PRINTED_ROWS) == set(SPEC_CODES)
    assert sum(PRINTED_ROWS.values()) == TOTAL_PRINTED_ROWS == 193
    assert sum(len(codes) for codes in SPEC_CODES.values()) == TOTAL_DISTINCT_PAIRS == 191
    for group, codes in SPEC_CODES.items():
        assert len(set(codes)) == len(codes), f"{group}: duplicate code in the distinct snapshot"
    # The one printed defect: 021 and 111 print code 14 twice and no code 15.
    assert {g for g, codes in SPEC_CODES.items() if PRINTED_ROWS[g] != len(codes)} == {"021", "111"}
    assert "15" in SPEC_CODES["011"] and "15" not in SPEC_CODES["021"] and "15" not in SPEC_CODES["111"]


# --- the partition: seeded ∪ unmapped == the spec -------------------------------------------------------------


def _seeded_pairs() -> set[tuple[str, str]]:
    return {(group, code) for group, _kinds, _code_group, mapping in _seed_migration().GROUPS for code in mapping}


def test_every_spec_code_is_either_seeded_or_recorded_as_unmapped_with_a_reason():
    seeded = _seeded_pairs()
    unmapped = set(UNMAPPED)
    spec = {(group, code) for group, codes in SPEC_CODES.items() for code in codes}

    assert not seeded & unmapped, "a code is both seeded and recorded as unmapped"
    assert seeded | unmapped == spec, (
        f"not decided: {sorted(spec - seeded - unmapped)}; invented: {sorted((seeded | unmapped) - spec)}"
    )
    assert (len(seeded), len(unmapped)) == (76, 115)
    categories = {NO_TARGET, AMBIGUOUS, NEEDS_CANONICAL, ADAPTER_OWNS}
    for pair, (category, reason) in UNMAPPED.items():
        assert category in categories and reason.strip(), pair


# --- shape --------------------------------------------------------------------------------------------------


def test_seed_rows_are_unique_fit_the_columns_and_target_a_list_named_like_their_code_group():
    rows = _seed_migration().SEED_ROWS
    assert len(rows) == 127

    keys = [(kind, code_group, provider_code) for kind, code_group, provider_code, _l, _v in rows]
    assert len(set(keys)) == len(keys), "duplicate natural key (uq_vehicle_provider_code_map_natural_key)"

    columns = ProviderCodeMap.__table__.c
    limits = {
        "vehicle_kind": columns.vehicle_kind.type.length,
        "code_group": columns.code_group.type.length,
        "provider_code": columns.provider_code.type.length,
        "canonical_list_code": columns.canonical_list_code.type.length,
        "canonical_value_code": columns.canonical_value_code.type.length,
    }
    assert PROVIDER == _seed_migration().PROVIDER and len(PROVIDER) <= columns.provider.type.length
    for kind, code_group, provider_code, list_code, value_code in rows:
        assert kind in {"01", "02", "03"}
        assert len(kind) <= limits["vehicle_kind"] and len(code_group) <= limits["code_group"]
        assert len(provider_code) <= limits["provider_code"]
        assert len(list_code) <= limits["canonical_list_code"] and len(value_code) <= limits["canonical_value_code"]
        # The sync passes the semantic name as code_group and the canonical column comes from the same-named list.
        assert list_code == code_group


def test_every_seeded_code_exists_in_its_spec_group_and_kind_independent_groups_cover_every_kind():
    for group, kinds, _code_group, mapping in _seed_migration().GROUPS:
        assert set(mapping) <= set(SPEC_CODES[group]), f"{group}: seeds a code the spec does not print"
    kind_independent = {"041", "045", "046", "047", "065"}
    for group, kinds, _code_group, _mapping in _seed_migration().GROUPS:
        if group in kind_independent:
            assert kinds == ("01", "02", "03"), group


# --- R-C-5: 112 is a stroke count, never a drive layout -----------------------------------------------------------


def test_112_maps_to_engine_cycle_and_never_to_drivetrain():
    migration = _seed_migration()
    groups = {group: (kinds, code_group, mapping) for group, kinds, code_group, mapping in migration.GROUPS}

    # The ticket's criterion: 112 -> engine_cycle, with its three printed codes ...
    assert groups["112"] == (("03",), "engine_cycle", {"2": "two_stroke", "4": "four_stroke", "9": "no_stroke"})
    # ... and nothing else maps into engine_cycle, so the stroke list has exactly one source.
    assert [g for g, (_k, code_group, _m) in groups.items() if code_group == "engine_cycle"] == ["112"]

    rows = migration.SEED_ROWS
    drivetrain = [(kind, code, value) for kind, code_group, code, _l, value in rows if code_group == "drivetrain"]
    # No drivetrain row exists for a motorcycle, and 112's codes never appear under drivetrain for any kind.
    assert {kind for kind, _code, _value in drivetrain} == {"01", "02"}
    assert {code for _kind, code, _value in drivetrain} == {"1", "2", "5"}
    assert {value for _kind, _code, value in drivetrain} == {"rwd", "fwd", "awd"}
    assert not any(kind == "03" and list_code == "drivetrain" for kind, _cg, _c, list_code, _v in rows)
    assert not any(value in {"two_stroke", "four_stroke", "no_stroke"} for _k, _c, value in drivetrain)
    # The trap itself: code 2 is a driven axle in 012/022 and a stroke count in 112.
    assert ("01", "2", "fwd") in drivetrain
    assert ("03", "engine_cycle", "2", "engine_cycle", "two_stroke") in rows


def test_the_canonical_stroke_list_is_what_112_targets():
    canonical = _canonical_values()
    assert canonical["engine_cycle"] == {"two_stroke", "four_stroke", "no_stroke"}
    assert not canonical["engine_cycle"] & canonical["drivetrain"]


# --- every canonical target exists --------------------------------------------------------------------------------


def test_every_canonical_target_exists_in_the_canonical_seed_constants():
    canonical = _canonical_values()
    targeted = {list_code for _k, _cg, _c, list_code, _v in _seed_migration().SEED_ROWS}
    # A vacuous pass is impossible: every list the seed targets was actually found and is non-empty.
    for list_code in targeted:
        assert canonical.get(list_code), f"canonical list {list_code!r} not found in the seed constants"
    for kind, code_group, provider_code, list_code, value_code in _seed_migration().SEED_ROWS:
        assert value_code in canonical[list_code], (kind, code_group, provider_code, list_code, value_code)


# --- behaviour on the test schema, through the real resolver ------------------------------------------------------


def _rows_for(db_session, provider: str = PROVIDER) -> int:
    return db_session.scalar(select(func.count()).select_from(ProviderCodeMap).where(ProviderCodeMap.provider == provider))


@pytest.fixture
def seeded(db_session):
    """Opt-in on purpose — see the module docstring."""

    return _seed_migration()._seed(db_session.connection())


def test_seed_inserts_every_row_once_and_a_second_run_inserts_nothing(db_session):
    migration = _seed_migration()

    first = migration._seed(db_session.connection())
    assert (first.inserted, first.identical, first.conflicting) == (127, 0, [])
    assert _rows_for(db_session) == 127

    second = migration._seed(db_session.connection())
    assert (second.inserted, second.identical, second.conflicting) == (0, 127, [])
    assert _rows_for(db_session) == 127


def test_rows_read_back_through_the_resolver_including_the_treibstoff_3_collision(db_session, seeded):
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
    assert resolve("03", "engine_cycle", "2").list_code == "engine_cycle"
    # A stroke code asked for under drivetrain finds nothing — it becomes a gap, never a wrong value.
    assert resolve("03", "drivetrain", "2") is None
    # FzArt is self-referential, exactly as catalogue_sync calls it.
    assert resolve("03", "vehicle_kind", "03").value_code == "motorcycle"
    assert resolve("02", "vehicle_kind", "02").value_code == "light_commercial"
    # A kind-independent vocabulary resolves under every kind.
    assert {resolve(kind, "equipment_feature", "2").value_code for kind in ("01", "02", "03")} == {"air_conditioning"}


def test_codes_deliberately_left_out_still_become_mapping_gaps(db_session, seeded):
    for kind, code_group, code in (("01", "fuel_type", "5"), ("03", "body_style", "1"), ("01", "transmission", "2")):
        assert (
            resolve_provider_code(
                db_session, provider=PROVIDER, vehicle_kind=kind, code_group=code_group, provider_code=code
            )
            is None
        )
        gap = db_session.scalar(
            select(MappingGap).where(
                MappingGap.provider == PROVIDER, MappingGap.vehicle_kind == kind,
                MappingGap.code_group == code_group, MappingGap.provider_code == code,
            )
        )
        assert gap is not None and gap.occurrences == 1 and not gap.resolved


def test_the_mock_provider_is_left_unseeded(db_session, seeded):
    assert _rows_for(db_session, "auto_i_dat_mock") == 0


def test_a_conflicting_admin_mapping_is_left_alone_and_reported(db_session):
    # An admin already resolved this key to something else before the seed ran.
    db_session.add(
        ProviderCodeMap(
            provider=PROVIDER, vehicle_kind="01", code_group="fuel_type", provider_code="3",
            canonical_list_code="fuel_type", canonical_value_code="petrol",
        )
    )
    db_session.flush()

    result = _seed_migration()._seed(db_session.connection())

    assert result.inserted == 126 and result.identical == 0
    assert result.conflicting == [
        (("01", "fuel_type", "3"), ("fuel_type", "petrol"), ("fuel_type", "diesel")),
    ]
    row = db_session.scalar(
        select(ProviderCodeMap).where(
            ProviderCodeMap.provider == PROVIDER, ProviderCodeMap.vehicle_kind == "01",
            ProviderCodeMap.code_group == "fuel_type", ProviderCodeMap.provider_code == "3",
        )
    )
    assert row.canonical_value_code == "petrol"  # the admin's decision wins


def test_unseed_removes_only_rows_whose_target_still_matches_the_seed(db_session, seeded):
    migration = _seed_migration()
    # An admin re-points one seeded key, and an unrelated row exists for another provider.
    db_session.execute(
        ProviderCodeMap.__table__.update()
        .where(
            ProviderCodeMap.provider == PROVIDER, ProviderCodeMap.vehicle_kind == "01",
            ProviderCodeMap.code_group == "fuel_type", ProviderCodeMap.provider_code == "3",
        )
        .values(canonical_value_code="hybrid")
    )
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat_mock", vehicle_kind="1", code_group="equipment_feature", provider_code="navigation",
            canonical_list_code="equipment_feature", canonical_value_code="navigation",
        )
    )
    db_session.flush()

    removed = migration._unseed(db_session.connection())

    assert removed == 126
    assert _rows_for(db_session) == 1  # only the admin's re-pointed row survives
    assert _rows_for(db_session, "auto_i_dat_mock") == 1
