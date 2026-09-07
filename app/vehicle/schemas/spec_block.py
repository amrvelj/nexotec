"""The read-model form of the vehicle specification block (ADR-071).

`VehicleSpecBlockRead` is the camelCase wire shape of
`app.vehicle.models.spec_block.VehicleSpecBlock` — one typed model the
catalogue variant read (C-B), the configuration read (C-C) and the FR-C-17
API all share. It is kept in lockstep with the mixin by
`tests/architecture/test_spec_block_carriers_do_not_drift.py`, which
asserts its field set (by alias) equals `SPEC_BLOCK_FIELDS`.
"""

from decimal import Decimal

from app.core.schemas import CamelModel


class VehicleSpecBlockRead(CamelModel):
    # labelling
    model_type_name: str | None = None
    trim_name: str | None = None
    # powertrain
    engine_cycle: str | None = None
    displacement_ccm: int | None = None
    cylinders: int | None = None
    gears: int | None = None
    ps: int | None = None
    kw: int | None = None
    total_ps: int | None = None
    total_kw: int | None = None
    system_kw: int | None = None
    # classification
    vehicle_class: str | None = None
    valuation_classification: str | None = None
    emission_standard: str | None = None
    # dimensions / weights
    doors: int | None = None
    seats: int | None = None
    weight_empty_kg: int | None = None
    weight_total_kg: int | None = None
    payload_kg: int | None = None
    towing_capacity_kg: int | None = None
    wheelbase_mm: int | None = None
    # consumption / energy
    consumption_mixed: Decimal | None = None
    consumption_urban: Decimal | None = None
    consumption_extra_urban: Decimal | None = None
    consumption_norm: str | None = None
    co2_gkm: int | None = None
    energy_consumption_kwh: Decimal | None = None
    battery_capacity_kwh: Decimal | None = None
    range_km: int | None = None
    tank_capacity_l: int | None = None
    # homologation codes
    werkscode: str | None = None
    importcode: str | None = None
    # production window / price
    model_year: int | None = None
    production_from: int | None = None
    production_to: int | None = None
    base_price: Decimal | None = None
    base_price_year: int | None = None
    price_is_net: bool | None = None


class VehicleSpecBlockInput(VehicleSpecBlockRead):
    """Same fields — a distinct class so FastAPI does not split
    `VehicleSpecBlockRead` into `-Input`/`-Output` schemas (it would, the
    moment the read model is also used as a request body). C-C's
    configuration create/update use this; every read keeps
    `VehicleSpecBlockRead`. Inherits `model_fields`, so the drift test
    covers it for free."""

