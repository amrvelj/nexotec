"""ADR-071 / PRD-Configurator risk R-C-3 — **one spec block, three carriers,
and a test that fails if a field is added to one carrier and not the rest**.
"A code review will not catch the fourth omission."

The carriers:

  1. ``vehicle_model_variant``            — the *type*.     Live (C-A / KAN-39).
  2. ``vehicle_configuration``            — the *instance*. C-C / KAN-41.
  3. ``sales`` offer ``vehicle_snapshot`` — frozen JSON.    C-F / KAN-10.

Carriers 1 and 2 mix in the **same** ``VehicleSpecBlock`` declaration, so
their spec columns are identical *by construction*. Carrier 3 is a JSON
column in another bounded context; it cannot share a Python base, so it
round-trips ``spec_block_as_dict`` (the camelCase serialiser) and the
canonical field list ``spec_block_field_names()``.

As C-C and C-F land, they append their carrier to ``_COLUMN_CARRIERS`` /
add a snapshot round-trip assertion here — nothing else in this file
changes.

Two tiers (see ``app.vehicle.models.spec_block``):
  * ``SPEC_BLOCK_FIELDS``          — the ~38 specification columns.
  * ``SPEC_BLOCK_IDENTITY_FIELDS`` — labelling; structural (properties) on
    the variant, denormalised columns on the other carriers.
The energy-efficiency label is *outside* the block on purpose (ADR-042 —
per ``(variant, year)`` in ``ModelVariantEnergyRating``, never carried
flat).
"""

from pydantic.alias_generators import to_camel
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.spec_block import (
    SPEC_BLOCK_ALL_FIELDS,
    SPEC_BLOCK_FIELDS,
    SPEC_BLOCK_IDENTITY_FIELDS,
    VehicleSpecBlock,
    spec_block_as_dict,
    spec_block_field_names,
)

# Carriers that hold the block as ORM columns (the mixin). C-C appends
# ``VehicleConfiguration``.
_COLUMN_CARRIERS = [ModelVariant]


def _mixin_mapped_attributes(cls: type) -> tuple[str, ...]:
    return tuple(
        name for name, value in vars(cls).items() if not name.startswith("_") and not callable(value)
    )


def test_field_tuples_are_disjoint_and_their_union_is_all_fields():
    assert not set(SPEC_BLOCK_FIELDS) & set(SPEC_BLOCK_IDENTITY_FIELDS)
    assert set(SPEC_BLOCK_ALL_FIELDS) == set(SPEC_BLOCK_FIELDS) | set(SPEC_BLOCK_IDENTITY_FIELDS)
    assert spec_block_field_names() == SPEC_BLOCK_ALL_FIELDS


def test_spec_block_fields_tuple_matches_the_mixin_exactly():
    """The tuple the migrations and the serialiser trust cannot silently
    drift from the mixin's own columns."""

    assert _mixin_mapped_attributes(VehicleSpecBlock) == SPEC_BLOCK_FIELDS


def test_every_column_carrier_carries_every_specification_field():
    for carrier in _COLUMN_CARRIERS:
        columns = {c.name for c in carrier.__table__.columns}
        missing = set(SPEC_BLOCK_FIELDS) - columns
        assert not missing, f"{carrier.__name__} is missing spec-block columns: {sorted(missing)}"


def test_every_column_carrier_maps_the_block_columns_nullable():
    """The block is copy-on-capture and hand-fillable — a NOT NULL column
    would make a bare manual configuration (FR-C-05) impossible to save."""

    for carrier in _COLUMN_CARRIERS:
        for column in carrier.__table__.columns:
            if column.name in SPEC_BLOCK_FIELDS:
                assert column.nullable, f"{carrier.__name__}.{column.name} must be nullable"


def test_every_carrier_can_produce_the_full_block_as_a_camelcase_dict():
    """The carrier-3 (JSON snapshot) contract, enforced on every carrier:
    ``spec_block_as_dict`` raises if a field is missing, and the key set is
    exactly the camelCase of every block field — so C-F's snapshot builder
    and C-C's read model cannot quietly drop one."""

    variant = ModelVariant(
        name="Demo 1.0",
        model_group=ModelGroup(name="Demo Group", brand=Brand(code="demo", display_name="Demo")),
        model_year_from=2020,
    )

    produced = spec_block_as_dict(variant)

    assert set(produced) == {to_camel(name) for name in SPEC_BLOCK_ALL_FIELDS}
    assert produced["brandDisplayName"] == "Demo"
    assert produced["variantName"] == "Demo 1.0"


def test_the_drift_check_actually_catches_a_field_added_to_only_one_carrier():
    """Guard the guard — a mixin attribute with no ``SPEC_BLOCK_FIELDS``
    entry must make ``test_spec_block_fields_tuple_matches_the_mixin`` fail,
    not pass silently."""

    class _Drifted(VehicleSpecBlock):
        smuggled_field: Mapped[str | None] = mapped_column(String(8), nullable=True)

    drifted = _mixin_mapped_attributes(_Drifted)
    assert "smuggled_field" in drifted
    assert drifted != SPEC_BLOCK_FIELDS
    assert "smuggled_field" not in SPEC_BLOCK_FIELDS
