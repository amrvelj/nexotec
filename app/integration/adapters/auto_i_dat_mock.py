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
from collections.abc import Mapping, Sequence
from decimal import Decimal

from app.core.base import utcnow
from app.integration.adapters.base import (
    BestMatchResult,
    BrandData,
    CodeMapEntryData,
    ForecastResult,
    ModelGroupData,
    ModelGroupShortData,
    OptionConditionData,
    OptionPackageContentData,
    PlateInfoData,
    SystemWatermark,
    TypeApprovalDataResult,
    ValuationResult,
    VariantColourData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantTyreSpecData,
    VehicleKindData,
    VehiclePriceData,
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
        werkscode="ALF14TB",
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
        werkscode="VW20TSI",
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
        werkscode="BMW320D",
        type_approval_numbers=["3EF789"],
    ),
}

_OPTIONS: dict[str, list[VariantOptionData]] = {
    "FZ100001": [
        VariantOptionData(
            option_code="MET", description="Metallic paint", option_group="exterior", price=Decimal("800.00")
        ),
    ],
    "FZ100002": [
        VariantOptionData(
            option_code="LED",
            description="LED headlights",
            option_group="exterior",
            price=Decimal("1200.00"),
            equipment_feature_codes=["LED_HEADLIGHTS"],
        ),
        VariantOptionData(
            option_code="NAV",
            description="Navigation Pro",
            option_group="infotainment",
            price=Decimal("1800.00"),
            equipment_feature_codes=["navigation", "APPLE_CARPLAY"],
        ),
        # KAN-43 — a package example: `is_package` offers to add its
        # contents, never silently (FR-C-06); `is_included` demonstrates
        # the "standard on this variant, still listed, zero price" case.
        VariantOptionData(
            option_code="WNTR",
            description="Winter package",
            option_group="comfort",
            price=Decimal("450.00"),
            is_package=True,
        ),
        VariantOptionData(
            option_code="AC",
            description="Air conditioning",
            option_group="comfort",
            price=Decimal("0.00"),
            is_included=True,
            equipment_feature_codes=["air_conditioning"],
        ),
    ],
    "FZ100003": [
        VariantOptionData(
            option_code="PAN", description="Panoramic sunroof", option_group="exterior", price=Decimal("1600.00")
        ),
    ],
}

# Keyed by Werkscode now, not FzKey — OptionenFarben's real search value
# (KAN-38 PR 1). Every demo variant carries a werkscode in _MASTER_DATA.
# `price=None` on the exterior colour models the common "free" case;
# `GRY`'s price models FR-C-07's "surcharge is a price line in build mode".
_COLOURS: dict[str, list[VariantColourData]] = {
    master.werkscode: [
        VariantColourData(colour_code="BLK", description="Black metallic", colour_type="exterior", price=Decimal("0.00")),
        VariantColourData(
            colour_code="RED", description="Rosso competizione", colour_type="exterior", price=Decimal("1100.00")
        ),
        VariantColourData(colour_code="GRY", description="Grey cloth", colour_type="interior"),
    ]
    for master in _MASTER_DATA.values()
    if master.werkscode
}

# Keyed by TypSchNr now — PneuDimTS's real search value (KAN-38 PR 1).
# One "both" row models the AchsenCode "1" fix; `remark`/`season` model
# FR-C-08's BemDe/PneuTyp requirements.
_TYRE_SPECS: dict[str, list[VariantTyreSpecData]] = {
    master.type_approval_numbers[0]: [
        VariantTyreSpecData(
            axle="front", size="225/45 R18", load_index="95", speed_rating="Y",
            remark="nur mit Leichtmetallfelgen", season="summer",
        ),
        VariantTyreSpecData(
            axle="rear", size="225/45 R18", load_index="95", speed_rating="Y",
            remark="nur mit Leichtmetallfelgen", season="summer",
        ),
        VariantTyreSpecData(axle="both", size="205/55 R16", load_index="91", speed_rating="H", season="winter"),
    ]
    for master in _MASTER_DATA.values()
    if master.type_approval_numbers
}


# -- fourteen new Datennamen (Configurator C-0 / KAN-38 PR 2) fixtures ----

_VEHICLE_KINDS = [
    VehicleKindData(code="01", label="Personenwagen"),
    VehicleKindData(code="02", label="Nutzfahrzeuge"),
    VehicleKindData(code="03", label="Motorräder"),
]

# All three demo vehicles are FzArt "01" (passenger cars) — matches
# vehicle_kind_code="1" on every _MASTER_DATA row above.
_BRANDS: dict[str, list[BrandData]] = {
    "01": [
        BrandData(code=master.brand_code, name=master.brand_display_name) for master in _MASTER_DATA.values()
    ],
}

# ModGrpKey values are invented (no real account to read them from) but
# stable per brand, matching each demo vehicle's own model_group_name.
_MODEL_GROUPS: dict[str, ModelGroupData] = {
    master.brand_code: ModelGroupData(
        model_group_key=100000 + index,
        name_de=master.model_group_name,
        short_name=master.model_group_name[:10],
        production_from=master.model_year_from,
        production_to=master.model_year_to,
    )
    for index, master in enumerate(_MASTER_DATA.values())
}

_VEHICLE_PRICES: dict[str, list[VehiclePriceData]] = {
    fz_key: [
        VehiclePriceData(year=master.model_year_from, price=int(master.base_price or 0)),
        VehiclePriceData(year=master.model_year_from + 1, price=int((master.base_price or 0) * Decimal("0.92"))),
    ]
    for fz_key, master in _MASTER_DATA.items()
}

# Sync-side completeness check fixture (KAN-38's own ruling — never a
# browse-facet source) — the provider's own coded values for one
# dimension, for a brand that has produced more variety than our three
# demo vehicles individually show.
_GROUPED_VALUES: dict[str, list[str]] = {
    "Aufbau": ["4", "5", "6", "8"],
    "Treibstoff": ["2", "3", "4"],
    "Antrieb": ["1", "2"],
}

_PLATES: dict[str, list[PlateInfoData]] = {
    "ZH123456": [
        PlateInfoData(
            vehicle_kind_code="01", brand_name="Subaru", model_description="G3X Justy Limousine",
            production_from=2003, production_to=2009, type_approval_number="1SC653",
            first_registration_date=dt.date(2003, 10, 3), stammnummer="626702193",
        )
    ],
    # A Wechselschild — one plate, two vehicles (FR-V-06 picker case).
    "ZH999999": [
        PlateInfoData(
            vehicle_kind_code="01", brand_name="Alfa Romeo", model_description="Giulietta 1.4 TB Progression",
            production_from=2010, production_to=2013, type_approval_number="1AB234",
            first_registration_date=dt.date(2011, 5, 12), stammnummer="111222333",
        ),
        PlateInfoData(
            vehicle_kind_code="01", brand_name="Volkswagen", model_description="Golf GTI 2.0 TSI DSG",
            production_from=2021, production_to=None, type_approval_number="2CD456",
            first_registration_date=dt.date(2022, 3, 1), stammnummer="444555666",
        ),
    ],
}

_TYPE_APPROVAL_DATA: dict[str, TypeApprovalDataResult] = {
    master.type_approval_numbers[0]: TypeApprovalDataResult(
        euro_norm="6b", fuel_consumption_mixed=Decimal("6.4"), emission_standard_code="3",
        energy_efficiency_category="D", co2=149, kerb_weight=1355, energy_consumption=Decimal(0),
    )
    for master in _MASTER_DATA.values()
    if master.type_approval_numbers
}

_OPTION_PACKAGES: dict[int, list[OptionPackageContentData]] = {
    100369: [
        OptionPackageContentData(opt_key=100369, description="Höhenverstellbarer Beifahrersitz"),
        OptionPackageContentData(opt_key=100370, description="Sitzheizung vorne"),
    ],
}

_OPTION_EXCLUSIONS: dict[int, list[int]] = {
    100017: [100369, 100370],
}

_OPTION_CONDITIONS: dict[int, list[OptionConditionData]] = {
    108181: [
        OptionConditionData(opt_key=108181, description="Pack Family", aktion_code="1", price=Decimal(0)),
    ],
}

# `Codes` (p24) — a small representative slice of the real CodeGrpNr
# vocabulary this session already read from the spec's own "Kodierte
# Felder" appendix (pages 32-34): Aufbau (010/020/110), Antrieb
# (012/022/112 — the R-C-5 stroke-count trap), Einstufung (041).
_CODES: list[CodeMapEntryData] = [
    CodeMapEntryData(code_group_nr="010", code_nr="6", label_de="Limousine", label_short_de="Lim"),
    CodeMapEntryData(code_group_nr="010", code_nr="5", label_de="Kombi", label_short_de="Kombi"),
    CodeMapEntryData(code_group_nr="012", code_nr="1", label_de="Hinten", label_short_de="Hinten"),
    CodeMapEntryData(code_group_nr="012", code_nr="2", label_de="Vorne", label_short_de="Vorne"),
    CodeMapEntryData(code_group_nr="112", code_nr="2", label_de="4 Takt", label_short_de="4T"),
    CodeMapEntryData(code_group_nr="112", code_nr="9", label_de="Kein Takt", label_short_de=None),
    CodeMapEntryData(code_group_nr="041", code_nr="0", label_de="Definitive Einstufung", label_short_de=None),
]


# BildTyp: S (small, 210px) / L (large, 1200px). BildArt: A (Aussen/
# exterior) / I (Innen/interior) — matching the real provider's own
# two-letter codes (Webservice Fahrzeuge p23), not a placeholder value the
# frontend's BILD_TYP_KEY/BILD_ART_KEY maps wouldn't recognise.
_IMAGES: dict[str, list[VariantImageData]] = {
    fz_key: [
        VariantImageData(
            bild_typ="S", bild_art="A", image_key=f"{fz_key}-front.jpg", sequence=0,
            image_url=f"https://images.autoi.ch/img/{fz_key}-front.jpg",
        ),
        VariantImageData(
            bild_typ="L", bild_art="I", image_key=f"{fz_key}-interior.jpg", sequence=1,
            image_url=f"https://images.autoi.ch/img/{fz_key}-interior.jpg",
        ),
    ]
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

    def fetch_options(self, fz_key: str, *, model_year: int) -> list[VariantOptionData]:
        return list(_OPTIONS.get(fz_key, []))

    def fetch_colours(self, *, werkscode: str) -> list[VariantColourData]:
        return list(_COLOURS.get(werkscode, []))

    def fetch_tyre_specs(self, *, type_approval_number: str) -> list[VariantTyreSpecData]:
        return list(_TYRE_SPECS.get(type_approval_number, []))

    def fetch_images(self, fz_key: str) -> list[VariantImageData]:
        return list(_IMAGES.get(fz_key, []))

    # -- fourteen new Datennamen (Configurator C-0 / KAN-38 PR 2) -------

    def list_vehicle_kinds(self) -> list[VehicleKindData]:
        return list(_VEHICLE_KINDS)

    def list_brands(self, *, fz_art: str) -> list[BrandData]:
        return list(_BRANDS.get(fz_art, []))

    def list_model_groups(
        self,
        *,
        fz_art: str | None = None,
        marken_nr: str | None = None,
        marke: str | None = None,
        mod_grp_key: int | None = None,
        mod_kurz_bez: str | None = None,
        nur_neue: bool = False,
        prod_von: int | None = None,
        prod_bis: int | None = None,
    ) -> list[ModelGroupData]:
        if mod_grp_key is not None:
            return [g for g in _MODEL_GROUPS.values() if g.model_group_key == mod_grp_key]
        # `marken_nr` matches this mock's own brand_code convention
        # (e.g. "ALF") — see _BRANDS/_MASTER_DATA above; the real
        # provider's MarkenNr is likewise an opaque code, never resolved
        # by this adapter (rule 3).
        if marken_nr is not None:
            group = _MODEL_GROUPS.get(marken_nr)
            return [group] if group is not None else []
        if marke is not None:
            master = next((m for m in _MASTER_DATA.values() if m.brand_display_name == marke), None)
            group = _MODEL_GROUPS.get(master.brand_code) if master is not None else None
            return [group] if group is not None else []
        return list(_MODEL_GROUPS.values())

    def list_model_groups_short(
        self,
        *,
        fz_art: str,
        marken_nr: str | None = None,
        marke: str | None = None,
        nur_neue: bool = False,
        page: int | None = None,
        per_page: int | None = None,
    ) -> list[ModelGroupShortData]:
        groups = self.list_model_groups(fz_art=fz_art, marken_nr=marken_nr, marke=marke, nur_neue=nur_neue)
        matched_keys = {g.model_group_key for g in groups}
        result: list[ModelGroupShortData] = []
        for master in _MASTER_DATA.values():
            group = _MODEL_GROUPS.get(master.brand_code)
            if group is not None and group.model_group_key in matched_keys:
                result.append(
                    ModelGroupShortData(
                        brand_code=master.brand_code, brand_name=master.brand_display_name, short_name=group.short_name
                    )
                )
        return result

    def search_vehicles(self, criteria: Mapping[str, str | int | Sequence[str | int]]) -> list[VariantMasterData]:
        fz_key = criteria.get("FzKey")
        if fz_key is not None:
            master = _MASTER_DATA.get(str(fz_key))
            return [master] if master is not None else []
        typ_sch_nr = criteria.get("TypSchNr")
        if typ_sch_nr is not None:
            return [m for m in _MASTER_DATA.values() if str(typ_sch_nr) in m.type_approval_numbers]
        werkscode = criteria.get("Werkscode")
        if werkscode is not None:
            return [m for m in _MASTER_DATA.values() if m.werkscode == werkscode]
        return list(_MASTER_DATA.values())

    def fetch_vehicle_prices(self, fz_key: str, *, year: int | None = None) -> list[VehiclePriceData]:
        prices = _VEHICLE_PRICES.get(fz_key, [])
        return [p for p in prices if year is None or p.year == year]

    def fetch_grouped_values(
        self,
        *,
        fz_art: str,
        marken_nr: str | None = None,
        marke: str | None = None,
        mod_kurz_bez: str | None = None,
        typ_sch_nr: str | None = None,
        gruppiert: str,
    ) -> list[str]:
        return list(_GROUPED_VALUES.get(gruppiert, []))

    def fetch_type_approvals(self, fz_key: str) -> list[str]:
        master = _MASTER_DATA.get(fz_key)
        return list(master.type_approval_numbers) if master else []

    def lookup_plate(self, plate: str, *, fz_art: str | None = None) -> list[PlateInfoData]:
        return list(_PLATES.get(plate, []))

    def find_best_match(
        self,
        *,
        typ_sch_nr: str,
        neupreis: int,
        modell_bez: str | None = None,
        eurotax_code: str | None = None,
        getriebe: str | None = None,
        tueren: int | None = None,
    ) -> BestMatchResult:
        for master in _MASTER_DATA.values():
            if typ_sch_nr in master.type_approval_numbers:
                match_code = 1 if modell_bez is not None else 2
                return BestMatchResult(vehicle=master, match_code=match_code)
        raise KeyError(f"Mock adapter has no vehicle for Typenschein '{typ_sch_nr}'.")

    def fetch_type_approval_data(
        self, typ_sch_nr: str, *, getriebe: str | None = None, gaenge: int | None = None
    ) -> TypeApprovalDataResult:
        found = _TYPE_APPROVAL_DATA.get(typ_sch_nr)
        if found is None:
            return TypeApprovalDataResult(
                euro_norm=None, fuel_consumption_mixed=None, emission_standard_code=None,
                energy_efficiency_category=None, co2=None, kerb_weight=None, energy_consumption=None,
            )
        if getriebe is None or gaenge is None:
            # "Bei Suche mit TypSchNr wird nur die EuroNorm zurückgegeben" (p28).
            return TypeApprovalDataResult(
                euro_norm=found.euro_norm, fuel_consumption_mixed=None, emission_standard_code=None,
                energy_efficiency_category=None, co2=None, kerb_weight=None, energy_consumption=None,
            )
        return found

    def fetch_option_package_contents(self, opt_key: int) -> list[OptionPackageContentData]:
        return list(_OPTION_PACKAGES.get(opt_key, []))

    def fetch_option_exclusions(self, fz_key: str, *, year: int, opt_key: int) -> list[int]:
        return list(_OPTION_EXCLUSIONS.get(opt_key, []))

    def fetch_option_conditions(self, fz_key: str, *, year: int, opt_key: int) -> list[OptionConditionData]:
        return list(_OPTION_CONDITIONS.get(opt_key, []))

    def fetch_codes(
        self, *, code_groups: list[str] | None = None, active_only: bool = False
    ) -> list[CodeMapEntryData]:
        if code_groups is None:
            return list(_CODES)
        wanted = set(code_groups)
        return [c for c in _CODES if c.code_group_nr in wanted]

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
