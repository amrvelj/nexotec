"""KAN-547 exit criterion 3, storage side: every variant a lookup returns is
upserted with the **full** specification block (ADR-071), its list prices
per model year, its Typenscheine, its FzKey and model-group refs, and the
identAPI's energy label as this year's rating (ADR-042, Anto 2026-11-07).
A provider variant has no NULL in the fields the fixture supplies; the coded
fields resolve through the seeded code map (`7c4e9a2b6d13`), whose codes are
the identAPI's own; a code the map lacks is a mapping gap; a motorcycle's
`drive` is an engine cycle, never a drivetrain (R-C-5). Fill-if-NULL on a
second visit (KAN-83) holds for the widened block.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.integration.adapters import identapi_parse as parse
from app.vehicle.models.catalogue import ModelVariant, VariantPrice
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.provider import MappingGap, ProviderEntityRef, VariantProviderCode
from app.vehicle.models.spec_block import SPEC_BLOCK_FIELDS
from app.vehicle.services import catalogue_sync
from app.vehicle.services.plate import swiss_today
from tests import identapi_fixtures as fix
from tests.test_provider_code_map_seed import _seed_migration
from tests.test_vehicle_catalogue_sync import _seed_reference_list

PROVIDER = "auto_i_dat"

# What the fixture carries, per spec-block column (None = the identAPI has no
# counterpart, left NULL and listed in the PR: system_kw, valuation_classification).
_UNSUPPLIED = {
    # no identAPI counterpart
    "system_kw", "valuation_classification",
    # `0` on the wire = unknown, stored NULL
    "total_ps", "total_kw", "consumption_urban", "consumption_extra_urban", "energy_consumption_kwh",
    "battery_capacity_kwh", "range_km",
    # not on a catalogue row (a built vehicle's), in production (NULL end), a car has no engine cycle
    "model_year", "production_to", "engine_cycle",
    # codes the seeded map lacks: mapping gaps (asserted below)
    "vehicle_class", "emission_standard",
}


def _seed_code_map(db_session) -> None:
    _seed_migration()._seed(db_session.connection())
    _seed_reference_list(db_session, "energy_efficiency_category", ("a", "b", "c", "d", "e", "f", "g"))
    db_session.commit()


def _upsert(db_session, item: dict) -> ModelVariant:
    master = parse.parse_variant(item)
    assert master is not None
    variant = catalogue_sync.upsert_model_variant(db_session, provider_code=PROVIDER, master=master)
    db_session.commit()
    db_session.refresh(variant)
    return variant


def test_a_looked_up_variant_carries_the_full_spec_block_with_no_null_where_the_provider_answered(db_session):
    _seed_code_map(db_session)

    variant = _upsert(db_session, fix.catalog_variant())

    supplied = [field for field in SPEC_BLOCK_FIELDS if field not in _UNSUPPLIED]
    missing = [field for field in supplied if getattr(variant, field) is None]
    assert not missing, f"NULL although the provider answered: {missing}"
    assert (variant.model_type_name, variant.trim_name) == ("Testmarke Prüfling 2.0 TDI Avant quattro", "Sport")
    assert (variant.ps, variant.kw, variant.displacement_ccm, variant.gears) == (204, 150, 1968, 7)
    assert (variant.doors, variant.seats, variant.weight_empty_kg, variant.towing_capacity_kg) == (5, 5, 1790, 2000)
    assert variant.consumption_mixed == Decimal("5.4") and variant.co2_gkm == 142
    assert (variant.production_from, variant.production_to, variant.model_year_from, variant.model_year_to) == (202103, None, 2021, None)
    assert (variant.base_price, variant.base_price_year, variant.price_is_net) == (Decimal("58900.00"), 2023, False)
    assert (variant.werkscode, variant.importcode) == (fix.DEMO_WERKSCODE, "IMP911")
    # the coded fields, through the seeded map: the identAPI's codes are auto-i's own
    assert (variant.vehicle_kind, variant.fuel_type, variant.body_style) == ("passenger_car", "diesel", "estate")
    assert (variant.drivetrain, variant.transmission, variant.consumption_norm) == ("awd", "automatic", "wltp")
    assert variant.engine_cycle is None
    # the identity tier
    assert (variant.brand_display_name, variant.model_group_name, variant.variant_name) == ("Testmarke", "Prüfling", "2.0 TDI Avant quattro")


def test_codes_the_map_lacks_become_gaps_and_leave_the_field_null(db_session):
    _seed_code_map(db_session)

    variant = _upsert(db_session, fix.catalog_variant())

    # `category` (FzKlasse) has no seeded rows (the reference list holds homologation classes — a
    # follow-up ticket), and the Euro norm string is not mapped: both are gaps, never guesses.
    gaps = {(g.code_group, g.provider_code) for g in db_session.scalars(select(MappingGap).where(MappingGap.provider == PROVIDER))}
    assert gaps == {("vehicle_class", "4"), ("emission_standard", "6d")}
    assert variant.vehicle_class is None and variant.emission_standard is None
    raw = {(r.code_group, r.provider_code) for r in db_session.scalars(select(VariantProviderCode).where(VariantProviderCode.model_variant_id == variant.id))}
    assert raw == {
        ("vehicle_kind", "01"), ("fuel_type", "3"), ("body_style", "5"), ("drivetrain", "5"), ("transmission", "5"),
        ("vehicle_class", "4"), ("emission_standard", "6d"), ("consumption_norm", "4"),
    }


def test_a_motorcycles_drive_is_an_engine_cycle_never_a_drivetrain(db_session):
    _seed_code_map(db_session)

    variant = _upsert(db_session, fix.catalog_variant(fzkey="03911001", type="03", drive="4", fuel="3", body="3", gearbox=None, category=None))

    assert variant.engine_cycle == "four_stroke" and variant.drivetrain is None
    assert variant.fuel_type == "petrol", "code 3 is petrol for a motorcycle, diesel for a car"
    raw = {r.code_group: r.provider_code for r in db_session.scalars(select(VariantProviderCode).where(VariantProviderCode.model_variant_id == variant.id))}
    assert raw["engine_cycle"] == "4" and "drivetrain" not in raw


def test_list_prices_type_approvals_refs_and_the_energy_label_are_stored(db_session):
    _seed_code_map(db_session)

    variant = _upsert(db_session, fix.catalog_variant())

    prices = db_session.scalars(select(VariantPrice).where(VariantPrice.model_variant_id == variant.id).order_by(VariantPrice.model_year)).all()
    assert [(p.model_year, p.new_price, p.price_is_net, p.source) for p in prices] == [
        (2021, Decimal("56900.00"), False, "provider"), (2022, Decimal("57900.00"), False, "provider"), (2023, Decimal("58900.00"), False, "provider"),
    ]
    assert sorted(catalogue_sync_type_approvals(variant)) == [fix.DEMO_TYPE_APPROVAL, "1ZZ912"]
    refs = {(r.entity_type, r.provider_key) for r in db_session.scalars(select(ProviderEntityRef).where(ProviderEntityRef.provider == PROVIDER))}
    assert refs == {("model_variant", fix.DEMO_FZ_KEY)}, "the provider's model-group key is not stored (one row per model name here)"
    (rating,) = db_session.scalars(select(ModelVariantEnergyRating).where(ModelVariantEnergyRating.model_variant_id == variant.id)).all()
    assert (rating.rating_year, rating.energy_efficiency_category, rating.source) == (swiss_today().year, "b", "auto_i_dat")


def catalogue_sync_type_approvals(variant: ModelVariant) -> list[str]:
    return [link.type_approval.type_approval_number for link in variant.type_approval_links]


def test_a_second_visit_fills_only_what_is_null_and_never_overwrites(db_session):
    _seed_code_map(db_session)
    first = _upsert(db_session, fix.catalog_variant(ps=0, doors=0, energylabel=None, catalogPrices=[]))
    assert first.ps is None and first.doors is None
    first.seats = 7  # an admin's correction, or an earlier provider value — never replaced by a lookup
    db_session.commit()

    second = _upsert(db_session, fix.catalog_variant(seats=5))

    assert second.id == first.id
    assert (second.ps, second.doors) == (204, 5), "NULL fields are filled on the next visit (KAN-83)"
    assert second.seats == 7, "a held value is never overwritten by a visit"
    prices = db_session.scalars(select(VariantPrice.model_year).where(VariantPrice.model_variant_id == second.id)).all()
    assert sorted(prices) == [2021, 2022, 2023]
    ratings = db_session.scalars(select(ModelVariantEnergyRating).where(ModelVariantEnergyRating.model_variant_id == second.id)).all()
    assert [r.energy_efficiency_category for r in ratings] == ["b"]


def test_an_energy_rating_already_held_for_the_year_is_left_alone(db_session):
    _seed_code_map(db_session)
    variant = _upsert(db_session, fix.catalog_variant(energylabel=None))
    db_session.add(ModelVariantEnergyRating(model_variant_id=variant.id, rating_year=swiss_today().year, energy_efficiency_category="a", source="bfe"))
    db_session.commit()

    _upsert(db_session, fix.catalog_variant(energylabel="C"))

    ratings = db_session.scalars(select(ModelVariantEnergyRating).where(ModelVariantEnergyRating.model_variant_id == variant.id)).all()
    assert [(r.energy_efficiency_category, r.source) for r in ratings] == [("a", "bfe")], "the official BFE row wins"


def test_a_label_the_reference_list_does_not_carry_and_a_row_without_a_kind_write_nothing_wrong(db_session):
    _seed_code_map(db_session)

    variant = _upsert(db_session, fix.catalog_variant(energylabel="H", type=None, fzkey="01911777"))

    assert db_session.scalars(select(ModelVariantEnergyRating).where(ModelVariantEnergyRating.model_variant_id == variant.id)).all() == []
    # no `type`: the kind is unknown — the FzKey's digits are never read for it — so the coded fields
    # stay NULL and no gap is filed under an invented kind
    assert variant.vehicle_kind is None and variant.fuel_type is None and variant.drivetrain is None and variant.engine_cycle is None
    gaps = {(g.vehicle_kind, g.code_group) for g in db_session.scalars(select(MappingGap).where(MappingGap.provider == PROVIDER))}
    assert not {g for g in gaps if g[0] == ""}, gaps


def test_the_bfe_import_takes_over_a_row_the_provider_label_filled(db_session):
    from app.vehicle.services.energy_rating import upsert_energy_rating

    _seed_code_map(db_session)
    variant = _upsert(db_session, fix.catalog_variant())

    upsert_energy_rating(
        db_session, model_variant_id=variant.id, rating_year=swiss_today().year, energy_efficiency_category="a",
        emission_standard=None, consumption_norm=None,
    )
    db_session.commit()

    (rating,) = db_session.scalars(select(ModelVariantEnergyRating).where(ModelVariantEnergyRating.model_variant_id == variant.id)).all()
    assert (rating.energy_efficiency_category, rating.source) == ("a", "bfe")
