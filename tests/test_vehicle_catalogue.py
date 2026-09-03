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

import datetime as dt
import importlib.util
import uuid
from pathlib import Path

from app.vehicle.models.catalogue import (
    Brand,
    ModelGroup,
    ModelVariant,
    TypeApproval,
    VariantOption,
    VariantTypeApproval,
)
from app.vehicle.services.catalogue import find_model_variants_by_type_approval

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


def _variants(db_session, *names: str) -> list[ModelVariant]:
    brand = Brand(code=f"brand-{names[0].lower()}", display_name="Test Brand")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name=f"Group {names[0]}")
    db_session.add(group)
    db_session.flush()
    variants = [
        ModelVariant(model_group_id=group.id, name=name, model_year_from=2018 + i)
        for i, name in enumerate(names)
    ]
    db_session.add_all(variants)
    db_session.flush()
    return variants


def test_two_variants_share_one_type_approval(db_session):
    """One Typenschein names several variants — both insert, and a lookup
    by that number returns both (FR-C-02 step 4: 1..n → picker)."""

    variant_a, variant_b = _variants(db_session, "Share-A", "Share-B")

    approval = TypeApproval(type_approval_number="1AB234")
    approval.variant_links = [
        VariantTypeApproval(model_variant_id=variant_a.id),
        VariantTypeApproval(model_variant_id=variant_b.id),
    ]
    db_session.add(approval)
    db_session.flush()

    found = find_model_variants_by_type_approval(db_session, "1AB234")
    assert {v.id for v in found} == {variant_a.id, variant_b.id}


def test_one_variant_carries_two_type_approvals(db_session):
    """auto-i-dat's `Typenscheine` returns a list for one FzKey — a variant
    carries several Typenscheine, and each resolves back to it."""

    (variant_c,) = _variants(db_session, "Multi-C")

    db_session.add_all(
        [
            TypeApproval(
                type_approval_number="4GH012",
                variant_links=[VariantTypeApproval(model_variant_id=variant_c.id)],
            ),
            TypeApproval(
                type_approval_number="5IJ345",
                variant_links=[VariantTypeApproval(model_variant_id=variant_c.id)],
            ),
        ]
    )
    db_session.flush()
    db_session.refresh(variant_c)

    numbers = {link.type_approval.type_approval_number for link in variant_c.type_approval_links}
    assert numbers == {"4GH012", "5IJ345"}
    assert find_model_variants_by_type_approval(db_session, "4GH012") == [variant_c]
    assert find_model_variants_by_type_approval(db_session, "5IJ345") == [variant_c]


def test_first_registration_from_lives_on_the_link(db_session):
    """The date qualifies the (variant, Typenschein) pair, so two variants
    sharing one Typenschein can hold different first-registration dates."""

    variant_a, variant_b = _variants(db_session, "Date-A", "Date-B")

    approval = TypeApproval(
        type_approval_number="9ZZ999",
        variant_links=[
            VariantTypeApproval(model_variant_id=variant_a.id, first_registration_from=dt.date(2016, 3, 1)),
            VariantTypeApproval(model_variant_id=variant_b.id, first_registration_from=dt.date(2019, 9, 1)),
        ],
    )
    db_session.add(approval)
    db_session.flush()
    db_session.expire_all()

    by_variant = {
        link.model_variant_id: link.first_registration_from
        for link in db_session.get(TypeApproval, approval.id).variant_links
    }
    assert by_variant == {variant_a.id: dt.date(2016, 3, 1), variant_b.id: dt.date(2019, 9, 1)}


def test_type_approval_number_is_not_unique(db_session):
    """Two separate `TypeApproval` rows may carry the same number — the
    column is indexed but deliberately not unique (PRD-Vehicles: "Not
    unique — many cars share one")."""

    (variant,) = _variants(db_session, "NonUnique")
    db_session.add_all(
        [
            TypeApproval(
                type_approval_number="1AB234",
                variant_links=[VariantTypeApproval(model_variant_id=variant.id)],
            ),
            TypeApproval(
                type_approval_number="1AB234",
                variant_links=[VariantTypeApproval(model_variant_id=variant.id)],
            ),
        ]
    )
    db_session.flush()  # no IntegrityError


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
