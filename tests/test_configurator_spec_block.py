"""Configurator C-A (KAN-39) — the specification block on the catalogue
variant, ``vehicle_variant_price``, the ``vehicle_variant_option``
extension, and the three new canonical lists.

Model-level / migration-data-shape tests only (SQLite fast lane never runs
Alembic — same convention as ``test_vehicle_catalogue.py``). The seed
data's own application is covered by the Postgres migration-smoke-test CI
job; the drift invariant lives in
``tests/architecture/test_spec_block_carriers_do_not_drift.py``.
"""

import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.vehicle.models.catalogue import (
    Brand,
    ModelGroup,
    ModelVariant,
    VariantOption,
    VariantOptionEquipmentFeature,
    VariantPrice,
)
from app.vehicle.models.spec_block import SPEC_BLOCK_FIELDS
from app.vehicle.services.provider import resolve_provider_code

_CANONICAL_LISTS_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "platform"
    / "a3d9c1e58f27_configurator_canonical_lists.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("configurator_c_a_canonical_lists", _CANONICAL_LISTS_MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _variant(db_session, name: str = "Spec 1.0") -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:8]}", display_name="Brand")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Group")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(model_group_id=group.id, name=name, model_year_from=2022)
    db_session.add(variant)
    db_session.flush()
    return variant


# --- 1 · the specification block --------------------------------------


def test_model_variant_can_hold_a_full_specification(db_session):
    variant = _variant(db_session)

    # A representative value in every spec-block column, of the right kind.
    values: dict[str, object] = {}
    for name in SPEC_BLOCK_FIELDS:
        column = ModelVariant.__table__.columns[name]
        type_name = column.type.__class__.__name__
        if type_name == "String":
            values[name] = "x"
        elif type_name == "Integer":
            values[name] = 1
        elif type_name in {"DECIMAL", "Numeric"}:
            values[name] = Decimal("1.0")
        elif type_name == "Boolean":
            values[name] = True
        else:  # pragma: no cover - a new column kind should be handled explicitly
            raise AssertionError(f"unhandled spec-block column type {type_name} on {name}")
        setattr(variant, name, values[name])

    db_session.flush()
    db_session.expire(variant)

    for name, expected in values.items():
        assert getattr(variant, name) == expected


def test_spec_block_columns_are_all_nullable_so_a_bare_variant_still_saves(db_session):
    variant = _variant(db_session, name="Bare")
    db_session.flush()
    db_session.refresh(variant)
    for name in SPEC_BLOCK_FIELDS:
        assert getattr(variant, name) is None


# --- 2 · vehicle_variant_price ---------------------------------------


def test_a_variant_can_carry_a_price_per_model_year(db_session):
    variant = _variant(db_session)
    db_session.add_all(
        [
            VariantPrice(model_variant_id=variant.id, model_year=2022, new_price=Decimal("34990.00")),
            VariantPrice(model_variant_id=variant.id, model_year=2023, new_price=Decimal("36490.00")),
        ]
    )
    db_session.flush()
    db_session.refresh(variant)

    by_year = {p.model_year: p for p in variant.prices}
    assert by_year[2022].new_price == Decimal("34990.00")
    assert by_year[2023].new_price == Decimal("36490.00")
    assert by_year[2022].price_is_net is False
    assert by_year[2022].source == "provider"


def test_variant_price_is_unique_per_variant_and_year(db_session):
    variant = _variant(db_session)
    db_session.add(VariantPrice(model_variant_id=variant.id, model_year=2022, new_price=Decimal("1.00")))
    db_session.flush()
    db_session.add(VariantPrice(model_variant_id=variant.id, model_year=2022, new_price=Decimal("2.00")))
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- 3 · vehicle_variant_option extension ----------------------------


def test_variant_option_carries_the_flags_and_year_c_e_needs(db_session):
    variant = _variant(db_session)
    tenant_id = uuid.uuid4()
    option = VariantOption(
        tenant_id=tenant_id,
        model_variant_id=variant.id,
        option_code="PACK-CITY",
        description="City Pack",
        option_group="comfort",
        is_included=False,
        is_package=True,
        model_year=2023,
    )
    db_session.add(option)
    db_session.flush()
    option.equipment_feature_links.append(
        VariantOptionEquipmentFeature(tenant_id=tenant_id, feature_value_code="navigation")
    )
    option.equipment_feature_links.append(
        VariantOptionEquipmentFeature(tenant_id=tenant_id, feature_value_code="parking_sensors")
    )
    db_session.flush()
    db_session.refresh(option)

    assert option.is_package is True
    assert option.model_year == 2023
    assert {link.feature_value_code for link in option.equipment_feature_links} == {"navigation", "parking_sensors"}


def test_variant_option_defaults_keep_the_existing_sync_path_working(db_session):
    """The existing `catalogue_sync` option upsert sets only
    code/description/group/price — the new columns must default, not error."""

    variant = _variant(db_session)
    option = VariantOption(
        tenant_id=uuid.uuid4(),
        model_variant_id=variant.id,
        option_code="LEGACY",
        description="unchanged sync path",
    )
    db_session.add(option)
    db_session.flush()
    db_session.refresh(option)
    assert option.is_included is False
    assert option.is_package is False
    assert option.model_year is None


# --- 4 · the three new canonical lists -------------------------------


def test_new_lists_are_the_three_the_prd_names_with_four_non_null_labels():
    module = _load_migration_module()
    lists = {code: values for code, values in module.NEW_LISTS}

    assert set(lists) == {"engine_cycle", "valuation_classification", "option_relation_type"}
    for code, values in lists.items():
        assert values, code
        for row in values:
            value_code, *labels = row
            assert len(labels) == 4, f"{code}/{value_code} must have DE/FR/IT/EN"
            assert all(label.strip() for label in labels), f"{code}/{value_code} has an empty label"


def test_engine_cycle_is_the_stroke_count_not_a_drivetrain():
    module = _load_migration_module()
    lists = {code: values for code, values in module.NEW_LISTS}
    codes = {row[0] for row in lists["engine_cycle"]}
    assert codes == {"two_stroke", "four_stroke", "no_stroke"}
    # none of the drivetrain codes leaked in (PRD risk R-C-5)
    assert not codes & {"fwd", "rwd", "awd", "front", "rear", "all"}


def test_option_relation_type_covers_aktion_plus_pack_and_exclusion():
    module = _load_migration_module()
    lists = {code: values for code, values in module.NEW_LISTS}
    codes = {row[0] for row in lists["option_relation_type"]}
    assert {"excludes", "contains"} <= codes  # OptionenAusschluss / OptionenPack
    assert "only_in_combination_with" in codes  # Aktion (047)


def test_new_lists_appear_in_the_frontend_admin_screen_constant():
    """The FR-V-11 screen drives off a hand-maintained constant
    (frontend/apps/dms/src/referenceLists.ts). A list seeded server-side
    but missing there is invisible to the admin — ADR-044 defeated."""

    ref_lists_ts = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "apps"
        / "dms"
        / "src"
        / "referenceLists.ts"
    ).read_text()
    for code in ("engine_cycle", "valuation_classification", "option_relation_type"):
        assert f"'{code}'" in ref_lists_ts, f"{code} missing from REFERENCE_LIST_CODES"


# --- 5 · provider_code_map — unmapped code surfaces as a gap, not a blank


@pytest.mark.parametrize("code_group", ["engine_cycle", "valuation_classification", "emission_standard"])
def test_unmapped_new_coded_field_is_a_mapping_gap_not_a_blank(db_session, code_group):
    from app.vehicle.models.provider import MappingGap

    result = resolve_provider_code(
        db_session,
        provider="auto_i_dat",
        vehicle_kind="1",
        code_group=code_group,
        provider_code="99",
    )

    # Not a blank / empty string — an explicit None the caller stores as NULL.
    assert result is None
    gap = (
        db_session.query(MappingGap)
        .filter_by(provider="auto_i_dat", code_group=code_group, provider_code="99")
        .one()
    )
    assert gap.resolved is False
    assert gap.occurrences == 1
