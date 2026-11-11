"""KAN-587 (Anto's ruling, 2026-11-10, option A): `vehicle_class` is the
**size class** auto-i-dat sends for every identified car; the type-approval
class (M1, N1, L3e, L1e) the list held until now has its own list and field,
`homologation_class`.

Tests build the schema with ``create_all``, never Alembic, so — like the other
seed tests — they load the two migrations by path and run their own steps
against the test schema (the real `alembic upgrade heads` run is
`scripts/dev/check`'s migration lanes):

- platform `5d1f8a3c7e42` moves the four type-approval rows to the new list
  (the same rows: an edited label and a switched-off value survive), adds the
  six size classes, and leaves any other value an admin added;
- vehicle `8e2b6c4f1a97` moves stored type-approval classes to the new field,
  seeds the six `auto_i_dat` code-map rows, resolves the open gaps and fills
  what they left NULL — and after it an identAPI `category` resolves with no
  gap (the exit criterion).
"""

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.integration.adapters import identapi_parse as parse
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.configuration import (
    ConfigurationMatchMethod,
    ConfigurationMatchStatus,
    ConfigurationMode,
    ConfigurationSource,
    VehicleConfiguration,
)
from app.vehicle.models.provider import MappingGap, ProviderCodeMap, VariantProviderCode
from app.vehicle.services import catalogue_sync
from tests import identapi_fixtures as fix
from tests.test_provider_code_map_seed import _seed_migration
from tests.test_vehicle_catalogue_sync import _bare_variant, _seed_reference_list

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
PROVIDER = "auto_i_dat"
MOCK = "auto_i_dat_mock"

# The vehicle migration's data steps are JSON SQL (`overridden_fields`), and a
# migration only ever runs on Postgres (ADR-011).
_postgres_only = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _load(pattern: str, name: str):
    (path,) = sorted(_VERSIONS.glob(pattern))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _platform_migration():
    return _load("platform/5d1f8a3c7e42_*.py", "kan587_platform_lists")


def _vehicle_migration():
    return _load("vehicle/8e2b6c4f1a97_*.py", "kan587_vehicle_code_map")


# --- platform: the two lists ---------------------------------------------------------------------------------------


def _list_values(db_session, list_code: str) -> dict[str, ReferenceValue]:
    db_session.expire_all()
    rows = db_session.scalars(
        select(ReferenceValue).join(ReferenceList).where(ReferenceList.list_code == list_code)
    ).all()
    return {row.value_code: row for row in rows}


def _seed_old_vehicle_class_list(db_session) -> dict[str, uuid.UUID]:
    """`vehicle_class` as `6ba0a99ed5c4` left it, with what platform staff may
    have done since: a label edited, a value switched off, a value added."""

    _seed_reference_list(db_session, "vehicle_class", ("m1", "n1", "l3e", "l1e", "m2"))
    values = _list_values(db_session, "vehicle_class")
    values["m1"].label_de = "M1 – PW (von Hand geändert)"
    values["l1e"].active = False
    db_session.commit()
    return {code: row.id for code, row in values.items()}


def test_the_type_approval_classes_move_to_their_own_list_as_the_same_rows(db_session):
    ids = _seed_old_vehicle_class_list(db_session)

    result = _platform_migration().apply(db_session.connection())
    db_session.commit()

    homologation = _list_values(db_session, "homologation_class")
    assert set(homologation) == {"m1", "n1", "l3e", "l1e"}
    assert {code: row.id for code, row in homologation.items()} == {code: ids[code] for code in homologation}
    assert homologation["m1"].label_de == "M1 – PW (von Hand geändert)", "an edited label survives the move"
    assert homologation["l1e"].active is False, "a switched-off value stays switched off"
    assert homologation["m1"].version == 2, "a moved row's version moves once"
    assert (result.moved, result.duplicates_removed) == (4, 0)


def test_vehicle_class_holds_the_six_size_classes_in_all_four_languages(db_session):
    _seed_old_vehicle_class_list(db_session)

    result = _platform_migration().apply(db_session.connection())
    db_session.commit()

    sizes = _list_values(db_session, "vehicle_class")
    assert [code for code in ("micro", "city", "compact", "large", "executive", "luxury") if code in sizes] == [
        "micro", "city", "compact", "large", "executive", "luxury",
    ]
    assert not {"m1", "n1", "l3e", "l1e"} & set(sizes), "a type-approval class is no size class"
    ordered = sorted((row for row in sizes.values() if row.value_code != "m2"), key=lambda row: row.sort_order)
    assert [row.value_code for row in ordered] == ["micro", "city", "compact", "large", "executive", "luxury"]
    for row in ordered:
        assert all((row.label_de, row.label_fr, row.label_it, row.label_en)), row.value_code
        assert row.active is True
    assert sizes["compact"].label_de == "Kompaktklasse" and sizes["executive"].label_en == "Upper mid-size"
    assert result.inserted == 6
    # A value an admin added is neither moved nor dropped: which fact it meant is not the migration's to guess.
    assert "m2" in sizes and result.left_in_size_list == ["m2"]


def test_the_list_migration_is_a_no_op_the_second_time_and_its_downgrade_restores_the_old_list(db_session):
    ids = _seed_old_vehicle_class_list(db_session)
    migration = _platform_migration()
    migration.apply(db_session.connection())
    db_session.commit()

    again = migration.apply(db_session.connection())
    db_session.commit()
    assert (again.moved, again.duplicates_removed, again.inserted) == (0, 0, 0)

    migration.revert(db_session.connection())
    db_session.commit()
    restored = _list_values(db_session, "vehicle_class")
    assert set(restored) == {"m1", "n1", "l3e", "l1e", "m2"}
    assert restored["m1"].id == ids["m1"] and restored["m1"].label_de == "M1 – PW (von Hand geändert)"
    assert _list_values(db_session, "homologation_class") == {}
    assert db_session.scalar(select(ReferenceList).where(ReferenceList.list_code == "homologation_class")) is None


def test_a_database_without_the_old_list_still_gets_both(db_session):
    """An environment where `6ba0a99ed5c4`'s rows were removed by hand: the
    migration creates what it needs rather than failing the deploy."""

    result = _platform_migration().apply(db_session.connection())
    db_session.commit()

    assert set(_list_values(db_session, "vehicle_class")) == {"micro", "city", "compact", "large", "executive", "luxury"}
    assert _list_values(db_session, "homologation_class") == {}
    assert (result.moved, result.inserted) == (0, 6)


# --- vehicle: stored values, the code map, the gaps -----------------------------------------------------------------


def _configuration(db_session, *, variant: ModelVariant | None, **fields) -> VehicleConfiguration:
    configuration = VehicleConfiguration(
        tenant_id=uuid.uuid4(),
        source=ConfigurationSource.PROVIDER if variant is not None else ConfigurationSource.MANUAL,
        mode=ConfigurationMode.RECORD,
        catalogue_match_status=ConfigurationMatchStatus.MATCHED if variant is not None else ConfigurationMatchStatus.UNVERIFIED,
        match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE if variant is not None else ConfigurationMatchMethod.MANUAL,
        catalogue_variant_id=variant.id if variant is not None else None,
        overridden_fields=fields.pop("overridden_fields", []),
        **fields,
    )
    db_session.add(configuration)
    db_session.flush()
    return configuration


def _raw_category(db_session, variant: ModelVariant, category: str, *, provider: str = PROVIDER) -> None:
    db_session.add(
        VariantProviderCode(
            model_variant_id=variant.id, provider=provider, vehicle_kind="01", code_group="vehicle_class",
            provider_code=category,
        )
    )


def _gap(db_session, category: str, *, resolved_as: str | None = None, provider: str = PROVIDER, kind: str = "01") -> MappingGap:
    gap = MappingGap(provider=provider, vehicle_kind=kind, code_group="vehicle_class", provider_code=category)
    if resolved_as is not None:
        gap.resolved = True
        gap.resolved_value_code = resolved_as
        gap.resolved_by = uuid.uuid4()
    db_session.add(gap)
    return gap


def _map(category: str, value: str, *, provider: str = PROVIDER, kind: str = "01") -> ProviderCodeMap:
    return ProviderCodeMap(
        provider=provider, vehicle_kind=kind, code_group="vehicle_class", provider_code=category,
        canonical_list_code="vehicle_class", canonical_value_code=value,
    )


def _mapping(db_session, category: str, *, provider: str = PROVIDER, kind: str = "01") -> str | None:
    return db_session.scalar(
        select(ProviderCodeMap.canonical_value_code).where(
            ProviderCodeMap.provider == provider, ProviderCodeMap.vehicle_kind == kind,
            ProviderCodeMap.code_group == "vehicle_class", ProviderCodeMap.provider_code == category,
        )
    )


def _apply(db_session):
    result = _vehicle_migration().apply_data(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    return result


@_postgres_only
def test_a_stored_type_approval_class_moves_to_its_own_field_and_is_never_dropped(db_session):
    variant = _bare_variant(db_session)
    variant.vehicle_class = "m1"
    manual = _configuration(db_session, variant=None, vehicle_class="n1")
    overridden = _configuration(
        db_session, variant=variant, vehicle_class="l3e", overridden_fields=["ps", "vehicle_class", "doors"]
    )
    # The advisor cleared the class the catalogue gave: a decision about the type-approval class.
    cleared = _configuration(db_session, variant=variant, vehicle_class=None, overridden_fields=["vehicle_class"])
    untouched = _configuration(db_session, variant=None, vehicle_class=None, overridden_fields=["ps"])
    editor = uuid.uuid4()
    for row in (manual, overridden, cleared, untouched):
        row.updated_by = editor
    db_session.commit()
    versions = (variant.version, manual.version, overridden.version, cleared.version, untouched.version)

    result = _apply(db_session)

    assert (variant.vehicle_class, variant.homologation_class) == (None, "m1")
    assert (manual.vehicle_class, manual.homologation_class) == (None, "n1")
    assert (overridden.vehicle_class, overridden.homologation_class) == (None, "l3e")
    # The advisor's override travels with the value it marked, in place.
    assert overridden.overridden_fields == ["ps", "homologation_class", "doors"]
    # ... and with a class they cleared: left on `vehicle_class`, it would keep the size class out for good.
    assert (cleared.vehicle_class, cleared.homologation_class) == (None, None)
    assert cleared.overridden_fields == ["homologation_class"]
    assert (untouched.homologation_class, untouched.overridden_fields) == (None, ["ps"])
    assert (variant.version, manual.version, overridden.version, cleared.version, untouched.version) == (
        versions[0] + 1, versions[1] + 1, versions[2] + 1, versions[3] + 1, versions[4],
    ), "a moved row's version moves once; an untouched row's does not"
    # A row the migration changed is a system write (KAN-243); an untouched row keeps its editor.
    assert (manual.updated_by, overridden.updated_by, cleared.updated_by, untouched.updated_by) == (
        None, None, None, editor,
    )
    assert (result.variants_moved, result.configurations_moved) == (1, 3)
    still_there = db_session.scalars(
        select(VehicleConfiguration.id).where(VehicleConfiguration.vehicle_class.in_(("m1", "n1", "l3e", "l1e")))
    ).all()
    assert still_there == []


@_postgres_only
def test_the_six_categories_are_seeded_and_the_open_gaps_resolved(db_session):
    for category in ("3", "4"):
        _gap(db_session, category)
    _gap(db_session, "9")  # a category the identAPI does not document: stays a gap
    _gap(db_session, "4", provider=MOCK)  # the demo provider is not seeded here
    db_session.commit()

    result = _apply(db_session)

    assert {category: _mapping(db_session, category) for category in "123456"} == {
        "1": "micro", "2": "city", "3": "compact", "4": "large", "5": "executive", "6": "luxury",
    }
    gaps = {
        (gap.provider, gap.provider_code): gap
        for gap in db_session.scalars(select(MappingGap).where(MappingGap.code_group == "vehicle_class"))
    }
    assert (gaps[(PROVIDER, "3")].resolved, gaps[(PROVIDER, "3")].resolved_value_code) == (True, "compact")
    assert (gaps[(PROVIDER, "4")].resolved, gaps[(PROVIDER, "4")].resolved_value_code) == (True, "large")
    assert gaps[(PROVIDER, "4")].resolved_at is not None and gaps[(PROVIDER, "4")].resolved_by is None
    assert gaps[(PROVIDER, "9")].resolved is False
    assert gaps[(MOCK, "4")].resolved is False and _mapping(db_session, "4", provider=MOCK) is None
    assert (result.seeded, result.gaps_resolved) == (6, 2)

    again = _apply(db_session)
    assert (again.seeded, again.gaps_resolved, again.variants_filled) == (0, 0, 0), "a second run writes nothing"


@_postgres_only
def test_what_the_gap_left_null_is_filled_on_variants_and_their_provider_configurations(db_session):
    variant = _bare_variant(db_session, "Looked up while the category was a gap")
    _raw_category(db_session, variant, "4")
    never_sent = _bare_variant(db_session, "The provider sent no category")
    undocumented = _bare_variant(db_session, "A category nobody maps")
    _raw_category(db_session, undocumented, "9")
    null_config = _configuration(db_session, variant=variant)
    cleared_before = _configuration(db_session, variant=variant, overridden_fields=["vehicle_class"])
    hand_typed = _configuration(db_session, variant=variant, vehicle_class="compact", overridden_fields=["vehicle_class"])
    of_other_variant = _configuration(db_session, variant=never_sent)
    manual = _configuration(db_session, variant=None)
    _gap(db_session, "4")
    db_session.commit()
    version = null_config.version

    result = _apply(db_session)

    assert variant.vehicle_class == "large"
    assert never_sent.vehicle_class is None and undocumented.vehicle_class is None
    assert (null_config.vehicle_class, null_config.version, null_config.updated_by) == ("large", version + 1, None)
    # What the advisor cleared before KAN-587 was the type-approval class: the mark went with it
    # (step 1), so the size class — which nobody ever cleared — is filled.
    assert (cleared_before.vehicle_class, cleared_before.overridden_fields) == ("large", ["homologation_class"])
    assert (hand_typed.vehicle_class, hand_typed.overridden_fields) == ("compact", ["vehicle_class"]), (
        "a set value is never overwritten, and its mark stays beside it"
    )
    assert of_other_variant.vehicle_class is None and manual.vehicle_class is None
    assert (result.variants_filled, result.configurations_filled) == (1, 2)


@_postgres_only
def test_a_mapping_onto_a_type_approval_class_is_replaced_and_what_it_filled_is_put_right(db_session):
    """An admin resolved the gaps the only way the old list allowed: every
    passenger-car category → `m1`. That target left the list, so the row goes,
    the seed takes its place, and the variants it filled get both facts."""

    stale_gap = _gap(db_session, "3", resolved_as="m1")
    other_kind_gap = _gap(db_session, "1", resolved_as="n1", kind="02")
    db_session.add_all([_map("3", "m1"), _map("1", "n1", kind="02"), _map("3", "m1", provider=MOCK)])
    variant = _bare_variant(db_session)
    variant.vehicle_class = "m1"
    _raw_category(db_session, variant, "3")
    config = _configuration(db_session, variant=variant, vehicle_class="m1")
    db_session.commit()

    result = _apply(db_session)

    assert _mapping(db_session, "3") == "compact"
    assert _mapping(db_session, "1", kind="02") is None and _mapping(db_session, "3", provider=MOCK) is None
    assert (stale_gap.resolved, stale_gap.resolved_value_code) == (True, "compact")
    # No seed covers a light commercial's category: its gap is open again for an admin.
    assert (other_kind_gap.resolved, other_kind_gap.resolved_value_code, other_kind_gap.resolved_by) == (False, None, None)
    assert (variant.vehicle_class, variant.homologation_class) == ("compact", "m1")
    assert (config.vehicle_class, config.homologation_class) == ("compact", "m1")
    assert (result.stale_mappings_removed, result.gaps_reopened) == (3, 2)


@_postgres_only
def test_an_admins_mapping_onto_a_value_the_list_still_has_is_left_alone(db_session):
    db_session.add(_map("2", "compact"))  # an admin's own call for category 2
    db_session.commit()

    result = _apply(db_session)

    assert _mapping(db_session, "2") == "compact", "an admin's decision outranks the seed (as in 7c4e9a2b6d13)"
    assert result.seeded == 5


@_postgres_only
def test_the_downgrade_puts_the_type_approval_class_back_and_takes_the_seed_away(db_session):
    variant = _bare_variant(db_session)
    variant.vehicle_class = "m1"
    sized = _bare_variant(db_session, "Sized")
    _raw_category(db_session, sized, "4")
    config = _configuration(db_session, variant=variant, vehicle_class="n1", overridden_fields=["vehicle_class"])
    both_marked = _configuration(db_session, variant=variant, vehicle_class="l3e")
    mark_only = _configuration(db_session, variant=None)
    _gap(db_session, "4")
    db_session.commit()
    migration = _vehicle_migration()
    _apply(db_session)
    assert (variant.homologation_class, sized.vehicle_class) == ("m1", "large")
    # After the upgrade an advisor overrides both classes on one car, and clears the new field on another.
    both_marked.vehicle_class = "city"
    both_marked.overridden_fields = ["vehicle_class", "ps", "homologation_class"]
    mark_only.overridden_fields = ["homologation_class"]
    db_session.commit()

    migration.revert_data(db_session.connection())
    db_session.commit()
    db_session.expire_all()

    assert variant.vehicle_class == "m1"
    assert sized.vehicle_class is None, "a size class has no place in the old list"
    assert (config.vehicle_class, config.overridden_fields) == ("n1", ["vehicle_class"])
    # One mark per field, never two — and no mark left naming the column the downgrade drops.
    assert (both_marked.vehicle_class, both_marked.overridden_fields) == ("l3e", ["vehicle_class", "ps"])
    assert mark_only.overridden_fields == ["vehicle_class"]
    assert [_mapping(db_session, category) for category in "123456"] == [None] * 6
    gap = db_session.scalars(select(MappingGap).where(MappingGap.code_group == "vehicle_class")).one()
    assert (gap.resolved, gap.resolved_value_code) == (False, None)


def test_the_migration_renders_offline_with_its_values():
    """KAN-174: `alembic upgrade bb6e760d9464:8e2b6c4f1a97 --sql` renders with
    no database, and renders the steps' values — parameters passed beside the
    statement would render as NULL and a script applied by hand would stamp
    the revision while its steps matched nothing (the KAN-550 review)."""

    run = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "bb6e760d9464:8e2b6c4f1a97", "--sql"],
        cwd=_VERSIONS.parent.parent, env=dict(os.environ), capture_output=True, text=True, timeout=300, check=False,
    )
    assert run.returncode == 0, run.stderr[-3000:]
    statements = [
        line for line in run.stdout.splitlines() if line.startswith(("UPDATE vehicle_", "DELETE", "INSERT", "ALTER"))
    ]
    assert sum(line.startswith("ALTER TABLE") and "homologation_class" in line for line in statements) == 2
    inserts = [line for line in statements if line.startswith("INSERT INTO vehicle_provider_code_map")]
    assert len(inserts) == 6
    for category, size_class in {"1": "micro", "2": "city", "3": "compact", "4": "large", "5": "executive", "6": "luxury"}.items():
        assert any(f"'auto_i_dat', '01', 'vehicle_class', '{category}', 'vehicle_class', '{size_class}'" in line for line in inserts)
    data = [line for line in statements if not line.startswith("ALTER")]
    assert len(data) == 13  # two moves, a delete, a reopen, six inserts, a resolve, two fills
    # ... each with its constants written out, none left as a NULL or a bare placeholder.
    assert sum("IN ('m1', 'n1', 'l3e', 'l1e')" in line for line in data) == 4  # the moves, the delete, the reopen
    assert sum("code_group = 'vehicle_class'" in line for line in data) == 11  # all but the two moves
    assert not any("IN (NULL)" in line or "code_group = NULL" in line or ":code_group" in line for line in data), data


# --- the exit criterion: an identAPI `category` resolves without a gap ---------------------------------------------


@_postgres_only
def test_an_identapi_category_resolves_to_its_size_class_without_a_gap(db_session):
    _seed_migration()._seed(db_session.connection())  # the identAPI's live map, before KAN-587
    _seed_reference_list(db_session, "energy_efficiency_category", ("a", "b", "c", "d", "e", "f", "g"))
    _apply(db_session)

    for fz_key, category, size_class in (
        ("01911001", 1, "micro"), ("01911002", 2, "city"), ("01911003", 3, "compact"),
        ("01911004", 4, "large"), ("01911005", 5, "executive"), ("01911006", 6, "luxury"),
    ):
        master = parse.parse_variant(fix.catalog_variant(fzkey=fz_key, category=category))
        assert master is not None
        variant = catalogue_sync.upsert_model_variant(db_session, provider_code=PROVIDER, master=master)
        db_session.commit()
        assert variant.vehicle_class == size_class, category
        assert variant.homologation_class is None, "no provider sends the type-approval class"

    gaps = db_session.scalars(
        select(MappingGap).where(MappingGap.provider == PROVIDER, MappingGap.code_group == "vehicle_class")
    ).all()
    assert gaps == []


@_postgres_only
def test_a_commercials_category_is_still_a_gap_never_a_guess(db_session):
    """`FzKlasse` is a passenger-car classification: the seed is keyed to kind
    `01` only, so a code under another kind is a gap for an admin, not `micro`."""

    _seed_migration()._seed(db_session.connection())
    _seed_reference_list(db_session, "energy_efficiency_category", ("a", "b", "c", "d", "e", "f", "g"))
    _apply(db_session)

    master = parse.parse_variant(fix.catalog_variant(fzkey="02911001", type="02", category=1))
    assert master is not None
    variant = catalogue_sync.upsert_model_variant(db_session, provider_code=PROVIDER, master=master)
    db_session.commit()

    assert variant.vehicle_class is None
    gap = db_session.scalars(select(MappingGap).where(MappingGap.code_group == "vehicle_class")).one()
    assert (gap.vehicle_kind, gap.provider_code, gap.resolved) == ("02", "1", False)
