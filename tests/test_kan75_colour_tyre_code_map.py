"""KAN-75 — FarbArt, AchsenCode and PneuTyp resolve through the provider code
map like every other coded field (FR-C-10): an unknown code is a mapping gap
and leaves the value NULL, never a guess; resolving the gap fills the cached
colours and tyres at once (Anto, 2026-10-23, B1); the mock provider is seeded
and sends raw codes (C1)."""

import dataclasses
import uuid

from sqlalchemy import select

from app.core.audit import list_audit_events
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.models.provider import IntegrationProvider
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.catalogue_mirror import ColourCache, TyreSpecCache
from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services import catalogue_admin, catalogue_entitlements, catalogue_sync
from tests.catalogue_lookup_support import look_up_demo_variants
from tests.colour_tyre_code_map_support import (
    code_map_migration,
    reference_values_migration,
    seed_colour_tyre_code_map,
)
from tests.test_provider_code_map_seed import _canonical_values
from tests.test_vehicle_catalogue_sync import _make_connection, _make_mock_provider, _seed_reference_list

MOCK = "auto_i_dat_mock"
GROUPS = ("colour_area", "axle_position", "tyre_type")


def _sync_tenant(db_session) -> uuid.UUID:
    """A Betrieb with a mock connection, its catalogue synced."""

    provider = db_session.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == MOCK))
    if provider is None:
        provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    look_up_demo_variants(db_session, tenant_id)
    return tenant_id


def _golf(db_session) -> ModelVariant:
    return db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()


def _colours(db_session, tenant_id) -> dict[str, ColourCache]:
    rows = db_session.scalars(
        select(ColourCache).where(ColourCache.tenant_id == tenant_id, ColourCache.model_variant_id == _golf(db_session).id)
    )
    return {row.colour_code: row for row in rows}


def _tyres(db_session, tenant_id) -> dict[tuple[str | None, str | None], TyreSpecCache]:
    rows = db_session.scalars(
        select(TyreSpecCache).where(
            TyreSpecCache.tenant_id == tenant_id, TyreSpecCache.model_variant_id == _golf(db_session).id
        )
    )
    return {(row.axle_code, row.season_code): row for row in rows}


def _gaps(db_session) -> set[tuple[str, str, str]]:
    rows = db_session.scalars(select(MappingGap).where(MappingGap.code_group.in_(GROUPS)))
    return {(row.vehicle_kind, row.code_group, row.provider_code) for row in rows}


def _gap(db_session, code_group: str, provider_code: str) -> MappingGap:
    gap = db_session.scalar(
        select(MappingGap).where(MappingGap.code_group == code_group, MappingGap.provider_code == provider_code)
    )
    assert gap is not None
    return gap


# --- the seed -----------------------------------------------------------------


def test_the_seed_maps_the_seven_documented_codes_for_both_providers():
    rows = {(p, k, g, c): v for p, k, g, c, v in code_map_migration().SEED_ROWS}
    for kind in ("01", "02", "03"):
        assert rows[("auto_i_dat", kind, "colour_area", "1")] == "exterior"
        assert rows[("auto_i_dat", kind, "colour_area", "2")] == "interior"
        assert rows[("auto_i_dat", kind, "axle_position", "1")] == "both"
        assert rows[("auto_i_dat", kind, "axle_position", "2")] == "front"
        assert rows[("auto_i_dat", kind, "axle_position", "3")] == "rear"
        assert rows[("auto_i_dat", kind, "tyre_type", "121")] == "summer"
        assert rows[("auto_i_dat", kind, "tyre_type", "122")] == "winter"
    # C1: the mock's demo variants carry FzArt "1".
    assert {(g, c, v) for (p, _k, g, c), v in rows.items() if p == MOCK} == {
        (g, c, v) for (p, k, g, c), v in rows.items() if p == "auto_i_dat" and k == "01"
    }
    assert {k for (p, k, _g, _c) in rows if p == MOCK} == {"1"}
    assert len(rows) == 7 * 4


def test_every_seeded_target_is_a_canonical_value():
    values = _canonical_values()
    for list_code, rows in reference_values_migration().NEW_VALUES:
        values.setdefault(list_code, set()).update(row[0] for row in rows)
    assert values["colour_area"] == {"exterior", "interior"}
    assert values["axle_position"] == {"front", "rear", "both"}
    for _provider, _kind, code_group, _code, value in code_map_migration().SEED_ROWS:
        assert value in values[code_group], (code_group, value)


def test_the_seed_is_insert_if_absent_and_leaves_an_admins_row_alone(db_session):
    migration = code_map_migration()
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat", vehicle_kind="01", code_group="axle_position", provider_code="2",
            canonical_list_code="axle_position", canonical_value_code="rear",
        )
    )
    db_session.flush()

    first = migration._seed(db_session.connection())
    second = migration._seed(db_session.connection())

    assert first.inserted == 27
    assert [c[0] for c in first.conflicting] == [("auto_i_dat", "01", "axle_position", "2")]
    assert second.inserted == 0 and second.identical == 27
    kept = db_session.scalar(
        select(ProviderCodeMap.canonical_value_code).where(
            ProviderCodeMap.provider == "auto_i_dat", ProviderCodeMap.vehicle_kind == "01",
            ProviderCodeMap.code_group == "axle_position", ProviderCodeMap.provider_code == "2",
        )
    )
    assert kept == "rear"
    assert migration._unseed(db_session.connection()) == 27


# --- the sync -------------------------------------------------------------------


def test_with_the_seed_the_sync_resolves_colours_and_tyres_and_keeps_the_raw_codes(db_session):
    seed_colour_tyre_code_map(db_session)
    tenant_id = _sync_tenant(db_session)

    colours = _colours(db_session, tenant_id)
    assert {code: (c.colour_type, c.colour_area_code) for code, c in colours.items()} == {
        "BLK": ("exterior", "1"), "RED": ("exterior", "1"), "GRY": ("interior", "2"),
    }
    tyres = _tyres(db_session, tenant_id)
    assert {key: (t.axle, t.season) for key, t in tyres.items()} == {
        ("2", "121"): ("front", "summer"), ("3", "121"): ("rear", "summer"), ("1", "122"): ("both", "winter"),
    }
    assert _gaps(db_session) == set()


def test_an_unmapped_code_is_a_gap_and_its_value_stays_empty_never_a_guess(db_session):
    """No seed: every code is unknown. Before KAN-75 the colour would have
    been "exterior" and the axle "front" whatever the provider sent."""

    tenant_id = _sync_tenant(db_session)

    assert {c.colour_type for c in _colours(db_session, tenant_id).values()} == {None}
    assert {(t.axle, t.season) for t in _tyres(db_session, tenant_id).values()} == {(None, None)}
    assert _gaps(db_session) == {
        ("1", "colour_area", "1"), ("1", "colour_area", "2"),
        ("1", "axle_position", "1"), ("1", "axle_position", "2"), ("1", "axle_position", "3"),
        ("1", "tyre_type", "121"), ("1", "tyre_type", "122"),
    }


def test_an_unknown_code_beside_known_ones_is_a_gap_and_a_missing_code_is_no_value(db_session, monkeypatch):
    seed_colour_tyre_code_map(db_session)
    real_colours = MockAutoIDatAdapter.fetch_colours
    real_tyres = MockAutoIDatAdapter.fetch_tyre_specs

    def colours(self, *, werkscode):
        rows = real_colours(self, werkscode=werkscode)
        return [
            dataclasses.replace(rows[0], colour_area_code="7"),
            dataclasses.replace(rows[1], colour_area_code=None),
            rows[2],
        ]

    def tyres(self, *, type_approval_number):
        rows = real_tyres(self, type_approval_number=type_approval_number)
        return [dataclasses.replace(rows[0], axle_code="5", season_code="129"), dataclasses.replace(rows[1], axle_code=None)]

    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_colours", colours)
    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_tyre_specs", tyres)
    tenant_id = _sync_tenant(db_session)

    assert {code: c.colour_type for code, c in _colours(db_session, tenant_id).items()} == {
        "BLK": None, "RED": None, "GRY": "interior",
    }
    assert {key: (t.axle, t.season) for key, t in _tyres(db_session, tenant_id).items()} == {
        ("5", "129"): (None, None), (None, "121"): (None, "summer"),
    }
    # A code nobody mapped is a gap; an absent code is no code, so no gap.
    assert _gaps(db_session) == {("1", "colour_area", "7"), ("1", "axle_position", "5"), ("1", "tyre_type", "129")}


def test_an_unclassified_colour_reaches_the_configurator_without_a_group(db_session):
    """A2: the configurator offers a colour under Exterior or Interior only,
    so one whose FarbArt is a gap is not offered until the gap is resolved."""

    tenant_id = _sync_tenant(db_session)
    spec = catalogue_entitlements.get_catalogue_specification(
        db_session, tenant_id=tenant_id, model_variant_id=_golf(db_session).id
    )
    assert {c.colour_type for c in spec.colours} == {None}
    assert {t.axle for t in spec.tyre_specs} == {None}


# --- resolving a gap (B1) -----------------------------------------------------------


def test_resolving_a_gap_fills_the_cached_colours_and_tyres_of_every_tenant_at_once(db_session):
    first = _sync_tenant(db_session)
    second = _sync_tenant(db_session)
    _seed_reference_list(db_session, "colour_area", ("exterior", "interior"))
    _seed_reference_list(db_session, "axle_position", ("front", "rear", "both"))
    _seed_reference_list(db_session, "tyre_type", ("summer", "winter", "all_season"))
    actor = uuid.uuid4()

    exterior = _gap(db_session, "colour_area", "1")
    catalogue_admin.resolve_gap(
        db_session, gap=exterior, canonical_list_code="colour_area", canonical_value_code="exterior", actor_id=actor
    )
    catalogue_admin.resolve_gap(
        db_session, gap=_gap(db_session, "axle_position", "2"), canonical_list_code="axle_position",
        canonical_value_code="front", actor_id=actor,
    )
    catalogue_admin.resolve_gap(
        db_session, gap=_gap(db_session, "tyre_type", "121"), canonical_list_code="tyre_type",
        canonical_value_code="summer", actor_id=actor,
    )

    for tenant_id in (first, second):
        assert {code: c.colour_type for code, c in _colours(db_session, tenant_id).items()} == {
            "BLK": "exterior", "RED": "exterior", "GRY": None,  # FarbArt 2 is another gap
        }
        assert {key: (t.axle, t.season) for key, t in _tyres(db_session, tenant_id).items()} == {
            ("2", "121"): ("front", "summer"), ("3", "121"): (None, "summer"), ("1", "122"): (None, None),
        }
    events = list_audit_events(db_session, entity_type="vehicle_mapping_gap", entity_id=exterior.id, tenant_id=None)
    # BLK and RED on each of the three demo variants, in each of two tenants.
    assert events[0].after["coloursAndTyresFilled"] == 12


def test_resolving_a_gap_never_overwrites_a_value_and_stays_within_its_vehicle_kind(db_session):
    tenant_id = _sync_tenant(db_session)
    _seed_reference_list(db_session, "colour_area", ("exterior", "interior"))
    kept = _colours(db_session, tenant_id)["RED"]
    kept.colour_type = "interior"
    db_session.commit()

    filled = catalogue_sync.apply_resolved_code_to_colours_and_tyres(
        db_session, provider=MOCK, vehicle_kind="02", code_group="colour_area", provider_code="1",
        value_code="exterior",
    )
    assert filled == 0  # the demo variants are FzArt "1", not "02"

    catalogue_admin.resolve_gap(
        db_session, gap=_gap(db_session, "colour_area", "1"), canonical_list_code="colour_area",
        canonical_value_code="exterior", actor_id=uuid.uuid4(),
    )
    colours = _colours(db_session, tenant_id)
    assert colours["RED"].colour_type == "interior"
    assert colours["BLK"].colour_type == "exterior"


def test_two_raw_axle_codes_mapped_onto_one_value_stay_two_rows(db_session, monkeypatch):
    """The tyre row is keyed by the raw codes: an admin may map an
    undocumented AchsenCode onto "front", and its row must not collide with
    the documented front row on the next sync."""

    seed_colour_tyre_code_map(db_session)
    db_session.add(
        ProviderCodeMap(
            provider=MOCK, vehicle_kind="1", code_group="axle_position", provider_code="5",
            canonical_list_code="axle_position", canonical_value_code="front",
        )
    )
    db_session.commit()
    real_tyres = MockAutoIDatAdapter.fetch_tyre_specs

    def tyres(self, *, type_approval_number):
        rows = real_tyres(self, type_approval_number=type_approval_number)
        return [rows[0], dataclasses.replace(rows[0], axle_code="5", size="235/40 R18")]

    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_tyre_specs", tyres)
    tenant_id = _sync_tenant(db_session)
    look_up_demo_variants(db_session, tenant_id)  # a re-sync keeps the two rows

    assert {key: (t.axle, t.size) for key, t in _tyres(db_session, tenant_id).items()} == {
        ("2", "121"): ("front", "225/45 R18"), ("5", "121"): ("front", "235/40 R18"),
    }
