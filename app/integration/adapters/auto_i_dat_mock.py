"""MockAutoIDatAdapter (WP-6 PR-2) — "build the provider-gateway against a
mock that returns realistic auto-i-dat shapes... do not skip this step to
save a day." Deterministic, no network call, no credentials needed (it
never touches services/secrets_backend.py at all) — registered as its own
provider_code (`auto_i_dat_mock`), a genuinely separate connection from
the real thing (PR-3's `auto_i_dat`), never a runtime flag on one
connection (the same "never a flag" posture rule 7 already applies to
sandbox/production).

`system_watermark_date` is injectable so PR-4/PR-6's sync-age-alarm tests
(A-12: alarm at >7 days) can control staleness precisely without
monkeypatching a clock.
"""

import datetime as dt
from decimal import Decimal

from app.core.base import utcnow
from app.integration.adapters.base import (
    ForecastResult,
    SystemWatermark,
    ValuationResult,
    VariantColourData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantTyreSpecData,
)

_DEMO_FZ_KEYS = ["FZ100001", "FZ100002", "FZ100003"]

_MASTER_DATA: dict[str, VariantMasterData] = {
    "FZ100001": VariantMasterData(
        fz_key="FZ100001",
        brand_code="ALF",
        brand_display_name="Alfa Romeo",
        model_group_name="Giulietta",
        variant_name="Giulietta 1.4 TB Progression",
        model_year_from=2019,
        model_year_to=2021,
        vehicle_kind_code="1",  # provider code — never resolved here
        fuel_type_code="3",
        body_style_code="5",
        drivetrain_code="1",
        transmission_code="M",
        base_price=Decimal("28900.00"),
        type_approval_numbers=["1AB234"],
    ),
    "FZ100002": VariantMasterData(
        fz_key="FZ100002",
        brand_code="VWN",
        brand_display_name="Volkswagen",
        model_group_name="Golf",
        variant_name="Golf GTI 2.0 TSI DSG",
        model_year_from=2021,
        model_year_to=None,
        vehicle_kind_code="1",
        fuel_type_code="3",
        body_style_code="5",
        drivetrain_code="1",
        transmission_code="A",
        base_price=Decimal("42500.00"),
        type_approval_numbers=["2CD456"],
    ),
    "FZ100003": VariantMasterData(
        fz_key="FZ100003",
        brand_code="BMW",
        brand_display_name="BMW",
        model_group_name="3er",
        variant_name="320d xDrive Touring M Sport",
        model_year_from=2020,
        model_year_to=None,
        vehicle_kind_code="1",
        fuel_type_code="4",
        body_style_code="4",
        drivetrain_code="2",
        transmission_code="A",
        base_price=Decimal("54900.00"),
        type_approval_numbers=["3EF789"],
    ),
}

_OPTIONS: dict[str, list[VariantOptionData]] = {
    "FZ100001": [VariantOptionData(option_code="MET", description="Metallic paint", option_group="exterior", price=Decimal("800.00"))],
    "FZ100002": [
        VariantOptionData(option_code="LED", description="LED headlights", option_group="exterior", price=Decimal("1200.00")),
        VariantOptionData(option_code="NAV", description="Navigation Pro", option_group="infotainment", price=Decimal("1800.00")),
    ],
    "FZ100003": [
        VariantOptionData(option_code="PAN", description="Panoramic sunroof", option_group="exterior", price=Decimal("1600.00")),
    ],
}

_COLOURS: dict[str, list[VariantColourData]] = {
    fz_key: [
        VariantColourData(colour_code="BLK", description="Black metallic", colour_type="exterior"),
        VariantColourData(colour_code="GRY", description="Grey cloth", colour_type="interior"),
    ]
    for fz_key in _DEMO_FZ_KEYS
}

_TYRE_SPECS: dict[str, list[VariantTyreSpecData]] = {
    fz_key: [
        VariantTyreSpecData(axle="front", size="225/45 R18", load_index="95", speed_rating="Y"),
        VariantTyreSpecData(axle="rear", size="225/45 R18", load_index="95", speed_rating="Y"),
    ]
    for fz_key in _DEMO_FZ_KEYS
}

_IMAGES: dict[str, list[VariantImageData]] = {
    fz_key: [VariantImageData(bild_typ="1", bild_art="1", image_key=f"{fz_key}-front.jpg", sequence=0)]
    for fz_key in _DEMO_FZ_KEYS
}


class MockAutoIDatAdapter:
    """Realistic auto-i-dat shapes, no live call. `system_watermark_date`
    defaults to today (a healthy mirror); tests pass an older date to
    exercise the sync-age alarm's exact >7-day boundary.
    """

    def __init__(self, *, system_watermark_date: dt.date | None = None) -> None:
        self._system_watermark_date = system_watermark_date or utcnow().date()

    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData:
        if fz_key not in _MASTER_DATA:
            raise KeyError(f"Mock adapter has no data for FzKey '{fz_key}'.")
        return _MASTER_DATA[fz_key]

    def list_changed_keys(self, *, since: dt.date) -> list[str]:
        return list(_DEMO_FZ_KEYS)

    def get_system_watermark(self) -> SystemWatermark:
        return SystemWatermark(current_model_year=utcnow().year, update_date=self._system_watermark_date)

    def fetch_options(self, fz_key: str) -> list[VariantOptionData]:
        return list(_OPTIONS.get(fz_key, []))

    def fetch_colours(self, fz_key: str) -> list[VariantColourData]:
        return list(_COLOURS.get(fz_key, []))

    def fetch_tyre_specs(self, fz_key: str) -> list[VariantTyreSpecData]:
        return list(_TYRE_SPECS.get(fz_key, []))

    def fetch_images(self, fz_key: str) -> list[VariantImageData]:
        return list(_IMAGES.get(fz_key, []))

    def fetch_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, valuation_date: dt.date, mileage: int
    ) -> ValuationResult:
        base = _MASTER_DATA.get(fz_key)
        base_price = (base.base_price if base else None) or Decimal("20000.00")
        age_years = max(valuation_date.year - first_registration.year, 0)
        depreciation = min(Decimal("0.12") * age_years + Decimal(mileage) / Decimal(100000) * Decimal("0.05"), Decimal("0.85"))
        return ValuationResult(provider_value=(base_price * (Decimal(1) - depreciation)).quantize(Decimal("0.01")), status_code="ok")

    def fetch_forecast(self, *, fz_key: str, model_year: int) -> ForecastResult:
        base = _MASTER_DATA.get(fz_key)
        base_price = (base.base_price if base else None) or Decimal("20000.00")
        return ForecastResult(
            residual_value=(base_price * Decimal("0.55")).quantize(Decimal("0.01")),
            forecast_date=utcnow().date() + dt.timedelta(days=365),
        )
