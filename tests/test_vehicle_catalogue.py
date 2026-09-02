"""WP-5 PR-1: the catalogue layer + canonical taxonomy.

Model-level tests only here (SQLite fast lane never runs Alembic — see the
migration-smoke-test CI job for the seed data's own verification, and the
WP-3 postmortem this project already learned that lesson from). The
migration's seed-data shape (every new list has exactly 4 non-null labels
per value) is asserted directly against the NEW_LISTS data structure so it
runs in the fast lane too, without needing Postgres.

The seed data itself lives in a platform-branch migration
(6ba0a99ed5c4), not the vehicle-branch one that creates these tables
(74b6c2794698) — reference_list/reference_value is platform-owned data,
and PR #47's Postgres migration-smoke-test caught a real bug from
originally seeding it inline here: this branch's own head has no
ordering guarantee relative to platform's, so a column the seed data
needs (label_en) wasn't reliably present yet. See 6ba0a99ed5c4's
docstring for the full story.
"""

import importlib.util
import uuid
from pathlib import Path

from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, TypeApproval, VariantOption

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "platform"
    / "6ba0a99ed5c4_vehicle_catalogue_reference_lists.py"
)


def _load_migration_module():
    # Migration filenames lead with a revision hash, which isn't a valid
    # Python identifier, so a normal dotted import can't reach them —
    # load by file path instead, the same way Alembic's own script
    # directory loads these files.
    spec = importlib.util.spec_from_file_location("wp5_pr1_reference_lists_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_brand_model_group_variant_chain(db_session):
    brand = Brand(code="alfa-romeo", display_name="Alfa Romeo")
    db_session.add(brand)
    db_session.flush()

    group = ModelGroup(brand_id=brand.id, name="Giulietta")
    db_session.add(group)
    db_session.flush()

    variant = ModelVariant(
        model_group_id=group.id,
        name="1.4 TB Progression",
        model_year_from=2016,
        vehicle_kind="passenger_car",
        fuel_type="petrol",
    )
    db_session.add(variant)
    db_session.flush()

    db_session.refresh(variant)
    assert variant.model_group.brand.code == "alfa-romeo"


def test_variant_option_stores_raw_provider_description(db_session):
    brand = Brand(code="test-brand", display_name="Test Brand")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Test Group")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(model_group_id=group.id, name="Test Variant", model_year_from=2020)
    db_session.add(variant)
    db_session.flush()

    option = VariantOption(
        tenant_id=uuid.uuid4(),  # WP-6 PR-4: tenant-partitioned (ADR-013) — see catalogue.py's own docstring
        model_variant_id=variant.id,
        option_code="MET_PAINT",
        description="Metallic paint",  # as delivered, never translated
        option_group="exterior",
    )
    db_session.add(option)
    db_session.flush()

    assert option.description == "Metallic paint"


def test_type_approval_number_is_unique_across_variants(db_session):
    brand = Brand(code="test-brand-2", display_name="Test Brand 2")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Group 2")
    db_session.add(group)
    db_session.flush()
    variant_a = ModelVariant(model_group_id=group.id, name="Variant A", model_year_from=2018)
    variant_b = ModelVariant(model_group_id=group.id, name="Variant B", model_year_from=2019)
    db_session.add_all([variant_a, variant_b])
    db_session.flush()

    db_session.add(TypeApproval(model_variant_id=variant_a.id, type_approval_number="1AB234"))
    db_session.flush()

    db_session.add(TypeApproval(model_variant_id=variant_b.id, type_approval_number="1AB234"))
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_migration_seed_data_has_four_labels_per_value():
    """Every new reference value the PR-1 migration seeds must carry all
    four mandatory labels — the literal rule CLAUDE.md states for reference
    data ("all four mandatory"). Runs against the migration module's own
    NEW_LISTS constant so it's covered by the fast SQLite lane, not only by
    the Postgres migration-smoke-test job.
    """

    module = _load_migration_module()
    assert len(module.NEW_LISTS) == 16
    seen_list_codes = set()
    for list_code, values in module.NEW_LISTS:
        assert list_code not in seen_list_codes, f"duplicate list_code {list_code}"
        seen_list_codes.add(list_code)
        assert values, f"{list_code} has no seed values"
        seen_value_codes = set()
        for value_code, label_de, label_fr, label_it, label_en in values:
            assert value_code not in seen_value_codes, f"duplicate value_code {value_code} in {list_code}"
            seen_value_codes.add(value_code)
            for label in (label_de, label_fr, label_it, label_en):
                assert label, f"{list_code}.{value_code} is missing a label"
