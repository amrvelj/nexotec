"""KAN-550 — vehicle-chain migration `bb6e760d9464` retires the SOAP-era codes.

Tests build the schema with ``create_all``, never Alembic, so — like the other
seed tests — they load the migration by path and run its own steps against the
test schema (the real `alembic upgrade heads` run is `scripts/dev/check`'s
migration lanes). What it must do, and what it must leave (Anto, 2026-11-09):

- the demo provider's code map moves from the SOAP-era kind `1` to `01`, an
  existing `01` row winning, and its SOAP-only rows (kind `1` itself, gearbox
  `A`/`M`) go;
- the demo provider's recorded raw codes and its gaps go;
- every gap keyed by a numeric CodeGrpNr goes, whatever the provider;
- the real provider's seeded map (`7c4e9a2b6d13`, the identAPI's live map),
  its raw codes and its semantic gaps stay untouched (D1).
"""

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import select

from app.vehicle.models.catalogue import VariantOption
from app.vehicle.models.provider import MappingGap, ProviderCodeMap, VariantOptionProviderCode, VariantProviderCode
from tests.colour_tyre_code_map_support import code_map_migration
from tests.test_vehicle_catalogue_sync import _bare_variant

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "vehicle"
MOCK = "auto_i_dat_mock"
REAL = "auto_i_dat"


def _load(pattern: str, name: str):
    (path,) = sorted(_VERSIONS.glob(pattern))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    return _load("bb6e760d9464_*.py", "kan550_retire_the_soap_codes")


def _map(provider: str, kind: str, group: str, code: str, value: str) -> ProviderCodeMap:
    return ProviderCodeMap(
        provider=provider, vehicle_kind=kind, code_group=group, provider_code=code,
        canonical_list_code=group, canonical_value_code=value,
    )


def _gap(provider: str, kind: str, group: str, code: str) -> MappingGap:
    return MappingGap(provider=provider, vehicle_kind=kind, code_group=group, provider_code=code)


def _keys(db_session, model, provider: str) -> set[tuple[str, str, str]]:
    rows = db_session.scalars(select(model).where(model.provider == provider)).all()
    return {(r.vehicle_kind, r.code_group, r.provider_code) for r in rows}


def _seed(db_session) -> int:
    """The state an environment can be in before the upgrade. Returns how many
    `auto_i_dat` map rows the real seeds wrote."""

    seed = _load("7c4e9a2b6d13_*.py", "kan38_pr2c_code_map_seed")
    seed._seed(db_session.connection())
    code_map_migration()._seed(db_session.connection())  # colour/axle/tyre, the mock under kind "1"
    db_session.add_all(
        [
            # an `01` row the demo copy script wrote, disagreeing with the `1` one
            _map(MOCK, "01", "colour_area", "2", "exterior"),
            # SOAP-only demo rows an admin resolved
            _map(MOCK, "1", "vehicle_kind", "1", "passenger_car"),
            _map(MOCK, "1", "transmission", "A", "automatic"),
            _map(MOCK, "1", "option_group", "comfort", "comfort"),
            # the identAPI's gearbox code on the demo provider: an `01` row that stays
            _map(MOCK, "01", "transmission", "5", "automatic"),
        ]
    )
    variant = _bare_variant(db_session)
    option = VariantOption(tenant_id=uuid.uuid4(), model_variant_id=variant.id, option_code="X", description="x")
    db_session.add(option)
    db_session.flush()
    for provider, kind in ((MOCK, "1"), (REAL, "01")):
        db_session.add(
            VariantProviderCode(
                model_variant_id=variant.id, provider=provider, vehicle_kind=kind, code_group="fuel_type",
                provider_code="3",
            )
        )
        db_session.add(
            VariantOptionProviderCode(
                tenant_id=option.tenant_id, variant_option_id=option.id, provider=provider, vehicle_kind=kind,
                code_group="equipment_feature", provider_code="navigation",
            )
        )
    db_session.add_all(
        [
            _gap(MOCK, "1", "transmission", "M"),
            _gap(REAL, "01", "014", "7"),  # a CodeGrpNr KAN-194 could not re-key
            _gap(REAL, "03", "110", "2"),
            _gap(REAL, "01", "vehicle_class", "4"),  # a semantic gap of the identAPI: stays
        ]
    )
    db_session.commit()
    return db_session.query(ProviderCodeMap).filter_by(provider=REAL).count()


def _upgrade(db_session) -> None:
    migration = _migration()
    migration.retire_demo_soap_codes(db_session.connection())
    migration.purge_codegrpnr_gaps(db_session.connection())
    db_session.commit()


def test_the_demo_providers_map_moves_to_the_identapi_kind_and_loses_its_soap_only_rows(db_session):
    _seed(db_session)

    _upgrade(db_session)

    rows = db_session.scalars(select(ProviderCodeMap).where(ProviderCodeMap.provider == MOCK)).all()
    assert {r.vehicle_kind for r in rows} == {"01"}
    by_key = {(r.code_group, r.provider_code): r.canonical_value_code for r in rows}
    assert by_key[("colour_area", "2")] == "exterior"  # the `01` row won over the `1` row's "interior"
    assert by_key[("colour_area", "1")] == "exterior"  # moved from `1`
    assert by_key[("option_group", "comfort")] == "comfort"  # moved from `1`
    assert by_key[("transmission", "5")] == "automatic"
    assert ("vehicle_kind", "1") not in by_key and ("transmission", "A") not in by_key
    assert len(rows) == 7 + 2  # b0a125a0a583's seven codes, the moved option group, the identAPI gearbox


def test_the_demo_providers_raw_codes_and_gaps_go(db_session):
    _seed(db_session)

    _upgrade(db_session)

    assert _keys(db_session, VariantProviderCode, MOCK) == set()
    assert _keys(db_session, VariantOptionProviderCode, MOCK) == set()
    assert _keys(db_session, MappingGap, MOCK) == set()


def test_codegrpnr_gaps_go_and_the_real_providers_map_codes_and_semantic_gaps_stay(db_session):
    real_rows = _seed(db_session)
    real_map_before = _keys(db_session, ProviderCodeMap, REAL)

    _upgrade(db_session)

    assert _keys(db_session, MappingGap, REAL) == {("01", "vehicle_class", "4")}
    assert _keys(db_session, ProviderCodeMap, REAL) == real_map_before
    assert db_session.query(ProviderCodeMap).filter_by(provider=REAL).count() == real_rows
    # `112 → engine_cycle` (D1): a motorcycle's stroke codes stay mapped.
    assert ("03", "engine_cycle", "4") in real_map_before
    assert _keys(db_session, VariantProviderCode, REAL) == {("01", "fuel_type", "3")}
    assert _keys(db_session, VariantOptionProviderCode, REAL) == {("01", "equipment_feature", "navigation")}


def test_a_second_run_changes_nothing(db_session):
    _seed(db_session)
    _upgrade(db_session)
    before = {
        (r.provider, r.vehicle_kind, r.code_group, r.provider_code, r.canonical_value_code)
        for r in db_session.scalars(select(ProviderCodeMap))
    }

    _upgrade(db_session)

    after = {
        (r.provider, r.vehicle_kind, r.code_group, r.provider_code, r.canonical_value_code)
        for r in db_session.scalars(select(ProviderCodeMap))
    }
    assert after == before
