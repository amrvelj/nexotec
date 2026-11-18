"""WP-5 PR-8: BFE energy rating — dated by year, absent when uncovered."""

from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.provider import ProviderEntityRef
from app.vehicle.services.energy_rating import get_energy_rating_for_year, upsert_energy_rating
from scripts.import_bfe_energy_ratings import import_bfe_ratings


def _variant(db_session):
    brand = Brand(code="test", display_name="Test")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Test Group")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(model_group_id=group.id, name="Test Variant", model_year_from=2020)
    db_session.add(variant)
    db_session.flush()
    return variant


def test_same_variant_can_have_different_categories_in_different_years(db_session):
    variant = _variant(db_session)
    upsert_energy_rating(db_session, model_variant_id=variant.id, rating_year=2024, energy_efficiency_category="B",
                          emission_standard=None, consumption_norm=None)
    upsert_energy_rating(db_session, model_variant_id=variant.id, rating_year=2026, energy_efficiency_category="C",
                          emission_standard=None, consumption_norm=None)

    rating_2024 = get_energy_rating_for_year(db_session, model_variant_id=variant.id, rating_year=2024)
    rating_2026 = get_energy_rating_for_year(db_session, model_variant_id=variant.id, rating_year=2026)

    assert rating_2024.energy_efficiency_category == "B"
    assert rating_2026.energy_efficiency_category == "C"


def test_uncovered_year_reads_as_absent_not_guessed(db_session):
    variant = _variant(db_session)
    upsert_energy_rating(db_session, model_variant_id=variant.id, rating_year=2024, energy_efficiency_category="B",
                          emission_standard=None, consumption_norm=None)

    assert get_energy_rating_for_year(db_session, model_variant_id=variant.id, rating_year=2026) is None


def test_rerunning_upsert_for_the_same_year_replaces_not_duplicates(db_session):
    variant = _variant(db_session)
    upsert_energy_rating(db_session, model_variant_id=variant.id, rating_year=2024, energy_efficiency_category="B",
                          emission_standard=None, consumption_norm=None)
    upsert_energy_rating(db_session, model_variant_id=variant.id, rating_year=2024, energy_efficiency_category="A",
                          emission_standard=None, consumption_norm=None)

    from sqlalchemy import select

    from app.vehicle.models.energy_rating import ModelVariantEnergyRating

    rows = list(db_session.scalars(
        select(ModelVariantEnergyRating).where(ModelVariantEnergyRating.model_variant_id == variant.id)
    ).all())
    assert len(rows) == 1
    assert rows[0].energy_efficiency_category == "A"


def test_import_skips_unknown_variant_code_rather_than_guessing(db_session):
    summary = import_bfe_ratings(db_session, [
        {"model_variant_code": "no-such-code", "rating_year": "2024", "energy_efficiency_category": "B"}
    ])
    assert summary.imported == 0
    assert summary.skipped_unknown_variant == ["no-such-code"]


def test_import_matches_via_provider_entity_ref(db_session):
    variant = _variant(db_session)
    db_session.add(ProviderEntityRef(entity_type="model_variant", entity_id=variant.id, provider="bfe", provider_key="BFE-001"))
    db_session.commit()

    summary = import_bfe_ratings(db_session, [
        {"model_variant_code": "BFE-001", "rating_year": "2025", "energy_efficiency_category": "D",
         "emission_standard": "euro_6", "consumption_norm": "wltp"},
    ])

    assert summary.imported == 1
    rating = get_energy_rating_for_year(db_session, model_variant_id=variant.id, rating_year=2025)
    assert rating is not None and rating.energy_efficiency_category == "D"
