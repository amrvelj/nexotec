"""The vehicle **specification block** — ADR-071, one declaration, three
carriers.

The ~40-field description of *what a car is* (engine, power, weights,
consumption, emissions, homologation codes, list price) is defined **once**,
here, and carried identically by:

  1. the catalogue variant — `catalogue.ModelVariant` (the type), which
     mixes this class in today;
  2. the configuration — `VehicleConfiguration` (the instance, C-C / KAN-41),
     which will mix in the **same** class, so its columns cannot drift from
     the variant's;
  3. the host's frozen snapshot — `sales.offer.vehicle_snapshot`, a JSON
     column in another bounded context (C-F / KAN-10). It cannot share a
     Python base across the seam, so instead it must round-trip the
     canonical serialiser exposed from `app.vehicle.public`
     (`spec_block_field_names()` / `VehicleSpecBlockSchema`). C-A ships that
     serialiser and the field-name tuple below; C-F wires the snapshot.

`tests/architecture/test_spec_block_carriers_do_not_drift.py` fails if a
field is added to one carrier and not the others, and if `SPEC_BLOCK_FIELDS`
drifts from the mixin's own attributes — a code review will not catch the
fourth omission (PRD risk R-C-3).

Two rules the columns encode:

  * **Every column is nullable.** A freshly-seeded variant, and every
    manual configuration (FR-C-05), carries only a few of these. `0` from
    auto-i-dat means *unknown* and is stored `NULL`, never `0` — enforced
    at the sync / service layer, not by the column.
  * **Coded fields hold a canonical `reference_value.value_code` string**,
    resolved through `provider_code_map` (never a raw provider code — the
    module's reading rule). Not a real FK: reference data is platform-owned
    and cross-context. Same pattern as `ModelVariant.fuel_type`.

**Two tiers.** `SPEC_BLOCK_FIELDS` (below) are the ~38 *specification*
columns — identical on every column carrier by construction (the mixin).
`SPEC_BLOCK_IDENTITY_FIELDS` are the labelling fields every carrier must be
able to *produce* but may represent differently: on the variant they are
structural (`ModelVariant.name`, and the `model_group → brand`
relationship, surfaced as read-only properties); on the configuration and
the snapshot they are denormalised strings captured at capture time.

**Deliberately outside the block entirely:**

  * `energyEfficiencyCategory` + its year — ADR-042: stored per
    `(variant, year)` in `ModelVariantEnergyRating`, *never* as a flat
    property (the A–G scale is re-published annually and must never be
    carried forward). Every carrier links to that table its own way; it is
    not copied into the block. This is a deliberate deviation from
    PRD-Configurator §Data-specification §1, which lists the pair as block
    fields — recorded on KAN-39.
  * `datEuroCode` — a `provider_entity_ref` row (ADR-020), already modelled
    in `provider.py`.
  * brand / model group are canonical and structural on the variant; only
    their denormalised *labels* travel in the identity tier.
"""

from decimal import Decimal
from typing import Any

from pydantic.alias_generators import to_camel
from sqlalchemy import DECIMAL, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

# The canonical field list — the single source of truth the drift test
# checks the mixin, every carrier and the serialiser against. Order is
# grouped for reading only.
SPEC_BLOCK_FIELDS: tuple[str, ...] = (
    # labelling (shared spec — the identity *tier* is the separate tuple below)
    "model_type_name",
    "trim_name",
    # powertrain
    "engine_cycle",
    "displacement_ccm",
    "cylinders",
    "gears",
    "ps",
    "kw",
    "total_ps",
    "total_kw",
    "system_kw",
    # classification
    "vehicle_class",
    "valuation_classification",
    "emission_standard",
    # dimensions / weights
    "doors",
    "seats",
    "weight_empty_kg",
    "weight_total_kg",
    "payload_kg",
    "towing_capacity_kg",
    "wheelbase_mm",
    # consumption / energy
    "consumption_mixed",
    "consumption_urban",
    "consumption_extra_urban",
    "consumption_norm",
    "co2_gkm",
    "energy_consumption_kwh",
    "battery_capacity_kwh",
    "range_km",
    "tank_capacity_l",
    # homologation codes
    "werkscode",
    "importcode",
    # production window / price
    "model_year",
    "production_from",
    "production_to",
    "base_price",
    "base_price_year",
    "price_is_net",
)

# The identity tier: the labelling fields every carrier must be able to
# *produce*, but the type carrier (`ModelVariant`) exposes them as
# properties over its structure rather than as flat columns — see the
# module docstring.
SPEC_BLOCK_IDENTITY_FIELDS: tuple[str, ...] = (
    "brand_display_name",
    "model_group_name",
    "variant_name",
)

SPEC_BLOCK_ALL_FIELDS: tuple[str, ...] = SPEC_BLOCK_FIELDS + SPEC_BLOCK_IDENTITY_FIELDS


class VehicleSpecBlock:
    """Declarative mixin — see module docstring. Every attribute here must
    appear in `SPEC_BLOCK_FIELDS`, and vice versa (drift test)."""

    # -- labelling -----------------------------------------------------------
    model_type_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """`ModellTypDe` — the long descriptive name; used on the offer document,
    not for matching."""
    trim_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """`Ausstattung` — *Progression*, *Sport*, *Avant-garde*. Part of the
    vehicle label everywhere."""

    # -- powertrain --------------------------------------------------------
    engine_cycle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Ref `engine_cycle`. `Antrieb` CodeGrpNr **112** = 2-Takt / 4-Takt /
    Kein Takt — a stroke count, motorcycles only. **Never** written into
    `drivetrain` (PRD risk R-C-5)."""
    displacement_ccm: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Hubraum
    cylinders: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Zylinder
    gears: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Gänge
    ps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # PS
    kw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # KW
    total_ps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # GesamtPS
    total_kw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # GesamtKW
    system_kw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # SystemKW
    """Combustion figure (`ps`/`kw`) and system figure (`total_*`/`system_kw`)
    differ on hybrids; both kept — showing only one advertises a PHEV at the
    wrong power."""

    # -- classification --------------------------------------------------
    vehicle_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Ref `vehicle_class` (`FzKlasse`). Passenger cars only."""
    valuation_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Ref `valuation_classification` (`Einstufung`). Drives whether FR-V-09
    can value the car at all."""
    emission_standard: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Ref `emission_standard` (`EuroNorm`). Gap-logged when unrecognised."""

    # -- dimensions / weights ------------------------------------------
    doors: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Türen
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Plätze
    weight_empty_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)  # GewLeer
    weight_total_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)  # GewGesamt
    payload_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Nutzlast
    towing_capacity_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Anhängelast
    wheelbase_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Radstand

    # -- consumption / energy ----------------------------------------
    consumption_mixed: Mapped[Decimal | None] = mapped_column(DECIMAL(4, 1), nullable=True)  # VerbMix
    consumption_urban: Mapped[Decimal | None] = mapped_column(DECIMAL(4, 1), nullable=True)  # VerbStadt
    consumption_extra_urban: Mapped[Decimal | None] = mapped_column(DECIMAL(4, 1), nullable=True)  # VerbLand
    consumption_norm: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Ref `consumption_norm` (`VerbNorm`). Mandatory whenever any consumption
    figure is present (a service-layer rule) — an NEFZ figure and a WLTP
    figure are not comparable and must never render bare."""
    co2_gkm: Mapped[int | None] = mapped_column(Integer, nullable=True)  # CO2
    energy_consumption_kwh: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 1), nullable=True)  # EnergieVerbrauch
    battery_capacity_kwh: Mapped[Decimal | None] = mapped_column(DECIMAL(6, 2), nullable=True)  # BattKapazität
    range_km: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Reichweite
    tank_capacity_l: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Tankinhalt
    """Electric figures are `NULL` on a combustion vehicle rather than `0`."""

    # -- homologation codes -------------------------------------------
    werkscode: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Werkscode
    importcode: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Importcode

    # -- production window / price -----------------------------------
    model_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    production_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """`ProdVon`, `YYYYMM`. `000000` from the provider means *still in
    production* and is stored `NULL`. (`ModelVariant` also keeps its own
    `model_year_from` / `model_year_to` range — C-0 to reconcile the two.)"""
    production_to: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ProdBis, YYYYMM
    base_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    """`LetzterNP` / `FahrzeugePreise`. The per-model-year price history lives
    in `vehicle_variant_price`; this is the single figure this carrier is
    priced at."""
    base_price_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Which year's new price `base_price` is — stored *with* it. A 2010
    Neupreis presented as today's is wrong by a five-figure sum, and
    `FahrzeugeMatch` (C-D) returns the wrong car for the wrong year."""
    price_is_net: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    """`NettoPreis` — whether `base_price` arrived net or gross. Stored as
    delivered (Q-C-6), converted only for display."""


def spec_block_field_names() -> tuple[str, ...]:
    """The canonical snake_case field list every carrier must expose. C-F's
    `sales` snapshot builder round-trips this (in camelCase) so the JSON
    carrier cannot drift from the two column carriers."""

    return SPEC_BLOCK_ALL_FIELDS


def spec_block_as_dict(carrier: Any) -> dict[str, Any]:
    """Read the spec block off any carrier (a `ModelVariant`, a
    `VehicleConfiguration`, or anything exposing the identity/derived
    accessors) as a **camelCase** dict — the shape the host snapshot
    freezes (C-F) and the read API returns (FR-C-17).

    Every key in `SPEC_BLOCK_ALL_FIELDS` is always present; a value the
    carrier does not have is `None` (a missing attribute is a carrier bug
    the drift test is meant to catch, not something to paper over — so this
    raises rather than defaulting silently)."""

    out: dict[str, Any] = {}
    for name in SPEC_BLOCK_ALL_FIELDS:
        if not hasattr(carrier, name):
            raise AttributeError(
                f"{type(carrier).__name__} is missing spec-block field {name!r} "
                "— see tests/architecture/test_spec_block_carriers_do_not_drift.py"
            )
        out[to_camel(name)] = getattr(carrier, name)
    return out
