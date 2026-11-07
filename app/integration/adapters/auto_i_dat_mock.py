"""MockAutoIDatAdapter (WP-6 PR-2) — "build the provider-gateway against a
mock that returns realistic auto-i-dat shapes... do not skip this step to
save a day." Deterministic, no network call, no credentials needed (it
never touches services/secrets_backend.py at all) — registered as its own
provider_code (`auto_i_dat_mock`), a genuinely separate connection from
the real thing (PR-3's `auto_i_dat`), never a runtime flag on one
connection (the same "never a flag" posture rule 7 already applies to
sandbox/production).

`system_watermark_date` is injectable so a test can control the `System`
answer without monkeypatching a clock. Every method is one metered request
under the real adapter's Datenname (KAN-486). There is no `FzKeyChanged`:
no provider, this one included, is asked for its whole catalogue (KAN-485).
"""

import datetime as dt
import functools
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any, TypeVar, cast

from app.core.base import utcnow
from app.integration.adapters.base import (
    BestMatchResult,
    BrandData,
    BuiltVehicleData,
    CaptionData,
    CatalogueBrandData,
    CatalogueModelGroupData,
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
    VariantSearchPage,
    VariantTyreSpecData,
    VehicleKindData,
    VehiclePriceData,
)
from app.integration.services.metering import provider_request

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
        # KAN-547: the spec block the identAPI carries, invented figures.
        model_type_name="Alfa Romeo Giulietta 1.4 TB Progression", trim_name="Progression",
        displacement_ccm=1368, cylinders=4, gears=6, ps=120, kw=88, doors=5, seats=5,
        weight_empty_kg=1355, weight_total_kg=1850, consumption_mixed=Decimal("5.6"), consumption_norm_code="4",
        co2_gkm=129, tank_capacity_l=60, production_from=201903, production_to=202112, base_price_year=2021,
        price_is_net=False, energy_label="C",
        catalogue_prices=[VehiclePriceData(year=2019, price=28900), VehiclePriceData(year=2021, price=29400)],
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
        model_type_name="Volkswagen Golf GTI 2.0 TSI DSG", trim_name="GTI",
        displacement_ccm=1984, cylinders=4, gears=7, ps=245, kw=180, doors=5, seats=5,
        weight_empty_kg=1463, weight_total_kg=1960, consumption_mixed=Decimal("7.1"), consumption_norm_code="4",
        co2_gkm=162, tank_capacity_l=50, production_from=202101, base_price_year=2023, price_is_net=False,
        energy_label="E",
        catalogue_prices=[VehiclePriceData(year=2021, price=41900), VehiclePriceData(year=2023, price=42500)],
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
        model_type_name="BMW 320d xDrive Touring M Sport", trim_name="M Sport",
        displacement_ccm=1995, cylinders=4, gears=8, ps=190, kw=140, doors=5, seats=5,
        weight_empty_kg=1745, weight_total_kg=2290, towing_capacity_kg=1800, consumption_mixed=Decimal("5.3"),
        consumption_norm_code="4", co2_gkm=139, tank_capacity_l=59, production_from=202003, base_price_year=2023,
        price_is_net=False, energy_label="B",
        catalogue_prices=[VehiclePriceData(year=2020, price=53900), VehiclePriceData(year=2023, price=54900)],
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
# KAN-75 (Anto, C1): raw `FarbArt` codes like the real service (1 Aussenfarbe,
# 2 Polsterfarbe), resolved by the sync through the code map, which migration
# `b0a125a0a583` seeds for this provider too.
_COLOURS: dict[str, list[VariantColourData]] = {
    master.werkscode: [
        VariantColourData(colour_code="BLK", description="Black metallic", colour_area_code="1", price=Decimal("0.00")),
        VariantColourData(
            colour_code="RED", description="Rosso competizione", colour_area_code="1", price=Decimal("1100.00")
        ),
        VariantColourData(colour_code="GRY", description="Grey cloth", colour_area_code="2"),
    ]
    for master in _MASTER_DATA.values()
    if master.werkscode
}

# Keyed by TypSchNr now — PneuDimTS's real search value (KAN-38 PR 1).
# One "both" row (AchsenCode 1) models the KAN-43 fix; `remark`/`season_code`
# model FR-C-08's BemDe/PneuTyp requirements. KAN-75 (C1): raw `AchsenCode`
# (2 Vorne, 3 Hinten, 1 Vorne+Hinten) and `PneuTyp` (121 Sommer, 122 Winter)
# codes, as the real service sends them.
_TYRE_SPECS: dict[str, list[VariantTyreSpecData]] = {
    master.type_approval_numbers[0]: [
        VariantTyreSpecData(
            axle_code="2", size="225/45 R18", load_index="95", speed_rating="Y",
            remark="nur mit Leichtmetallfelgen", season_code="121",
        ),
        VariantTyreSpecData(
            axle_code="3", size="225/45 R18", load_index="95", speed_rating="Y",
            remark="nur mit Leichtmetallfelgen", season_code="121",
        ),
        VariantTyreSpecData(axle_code="1", size="205/55 R16", load_index="91", speed_rating="H", season_code="122"),
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
    # KAN-491: invented, like every record here — never the specification's
    # own KontrollschildInfo example.
    "ZH123456": [
        PlateInfoData(
            vehicle_kind_code="01", brand_name="BMW", model_description="318i Limousine",
            production_from=2003, production_to=2009, type_approval_number="4GH901",
            first_registration_date=dt.date(2004, 6, 14), stammnummer="900100200",
        )
    ],
    # KAN-42 — one record whose Typenschein the mock catalogue carries, so a
    # plate resolves all the way to a variant.
    "BE123456": [
        PlateInfoData(
            vehicle_kind_code="01", brand_name="Volkswagen", model_description="Golf GTI 2.0 TSI DSG",
            production_from=2021, production_to=None, type_approval_number="2CD456",
            first_registration_date=dt.date(2022, 3, 1), stammnummer="777888999",
        )
    ],
    # KAN-42 — a genuine conflict: one Stammnummer reported under two
    # different Typenscheine (a data-quality event, never a picker of two
    # legitimate cars).
    "GE111111": [
        PlateInfoData(
            vehicle_kind_code="01", brand_name="Alfa Romeo", model_description="Giulietta 1.4 TB Progression",
            production_from=2010, production_to=2013, type_approval_number="1AB234",
            first_registration_date=dt.date(2012, 1, 9), stammnummer="123123123",
        ),
        PlateInfoData(
            vehicle_kind_code="01", brand_name="BMW", model_description="320d Touring",
            production_from=2019, production_to=None, type_approval_number="3EF789",
            first_registration_date=dt.date(2012, 1, 9), stammnummer="123123123",
        ),
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

# OptKeys 9xxxxx are invented (KAN-491): no real or specification OptKey
# is in this range, so a mock key can never be read as one.
_OPTION_PACKAGES: dict[int, list[OptionPackageContentData]] = {
    900101: [
        OptionPackageContentData(opt_key=900101, description="Lordosenstütze Fahrersitz"),
        OptionPackageContentData(opt_key=900102, description="Sitzheizung vorne"),
    ],
}

_OPTION_EXCLUSIONS: dict[int, list[int]] = {
    900100: [900101, 900102],
}

_OPTION_CONDITIONS: dict[int, list[OptionConditionData]] = {
    900200: [
        OptionConditionData(opt_key=900200, description="Aktionspaket Winter", aktion_code="1", price=Decimal(0)),
    ],
}

# `Codes` (p24) — a small representative slice of the real CodeGrpNr
# vocabulary, transcribed from the spec's own "Kodierte Felder" tables
# (pages 32-34): Aufbau (010), Antrieb (012, and 112 — the R-C-5 stroke-count
# trap), Einstufung (041). Labels are the spec's own German text. The spec
# prints exactly one short label anywhere (010/1 "Gpw", p24), so none is
# invented here.
#
# KAN-38 PR 2c corrected this slice. PR 2a had 112 code 2 labelled "4 Takt"
# and code 4 missing (the spec's 112 is 2 = 2 Takt, 4 = 4 Takt, 9 = Kein
# Takt), a truncated 041 label, and short labels the spec does not carry —
# on exactly the trap the ticket names, pinned by tests.
_CODES: list[CodeMapEntryData] = [
    CodeMapEntryData(code_group_nr="010", code_nr="6", label_de="Limousine", label_short_de=None),
    CodeMapEntryData(code_group_nr="010", code_nr="5", label_de="Kombi", label_short_de=None),
    CodeMapEntryData(code_group_nr="012", code_nr="1", label_de="Hinten", label_short_de=None),
    CodeMapEntryData(code_group_nr="012", code_nr="2", label_de="Vorne", label_short_de=None),
    CodeMapEntryData(code_group_nr="112", code_nr="2", label_de="2 Takt", label_short_de=None),
    CodeMapEntryData(code_group_nr="112", code_nr="4", label_de="4 Takt", label_short_de=None),
    CodeMapEntryData(code_group_nr="112", code_nr="9", label_de="Kein Takt", label_short_de=None),
    CodeMapEntryData(
        code_group_nr="041", code_nr="0", label_de="Definitive Einstufung (Bewertung möglich)", label_short_de=None
    ),
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


def _model_groups(
    *, marken_nr: str | None = None, marke: str | None = None, mod_grp_key: int | None = None
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


_Method = TypeVar("_Method", bound=Callable[..., Any])


# KAN-547 — the identAPI's caption groups as the mock speaks them: the codes
# the demo variants carry, with invented wording (KAN-491). `kind` is the
# list a caption sits in; `global` holds a code every vehicle type shares.
_CAPTIONS: list[CaptionData] = [
    CaptionData(group="type", kind="global", code="01", label="Demo-Personenwagen", label_short="PW"),
    CaptionData(group="type", kind="global", code="02", label="Demo-Nutzfahrzeug", label_short="NF"),
    CaptionData(group="type", kind="global", code="03", label="Demo-Motorrad", label_short="MR"),
    CaptionData(group="fuel", kind="passenger", code="3", label="Demo-Diesel", label_short="D"),
    CaptionData(group="fuel", kind="passenger", code="4", label="Demo-Diesel Filter", label_short="DF"),
    CaptionData(group="body", kind="passenger", code="4", label="Demo-Van", label_short=None),
    CaptionData(group="body", kind="passenger", code="5", label="Demo-Kombi", label_short=None),
    CaptionData(group="drive", kind="passenger", code="1", label="Demo-Heck", label_short=None),
    CaptionData(group="drive", kind="passenger", code="2", label="Demo-Front", label_short=None),
    CaptionData(group="gearbox", kind="passenger", code="A", label="Demo-Automat", label_short="A"),
    CaptionData(group="gearbox", kind="passenger", code="M", label="Demo-Handschaltung", label_short="M"),
]


def _built_vehicle(record: PlateInfoData, *, plate: str | None) -> BuiltVehicleData:
    """A mock plate record as the identAPI's Vehicle Build row: the variant
    is the demo variant whose Typenscheine carry the record's number."""

    variant = next((m for m in _MASTER_DATA.values() if record.type_approval_number in m.type_approval_numbers), None)
    return BuiltVehicleData(
        fz_key=variant.fz_key if variant else None,
        vin=None,
        stammnummer=record.stammnummer,
        plate=plate,
        first_registration_date=record.first_registration_date,
        type_approval_number=record.type_approval_number,
        model_year=record.first_registration_date.year if record.first_registration_date else None,
        vehicle_kind_code=record.vehicle_kind_code,
        brand_name=record.brand_name,
        model_description=record.model_description,
        production_from=record.production_from,
        production_to=record.production_to,
        variant=variant,
    )


def _request(datenname: str) -> Callable[[_Method], _Method]:
    """Each mock method is one request under the real adapter's Datenname,
    so a dev or staging call log reads like a real one (KAN-486)."""

    def wrap(method: _Method) -> _Method:
        @functools.wraps(method)
        def metered(*args: Any, **kwargs: Any) -> Any:
            with provider_request(datenname) as request:
                return request.attempt(lambda: method(*args, **kwargs))

        return cast(_Method, metered)

    return wrap


class MockAutoIDatAdapter:
    """Realistic auto-i-dat shapes, no live call. `system_watermark_date`
    defaults to today; a test may pass another date. (The sync-age alarm
    that compared it was retired with the full catalogue download, KAN-485 /
    KAN-497.)
    """

    def __init__(self, *, system_watermark_date: dt.date | None = None) -> None:
        self._system_watermark_date = system_watermark_date or utcnow().date()

    @_request("Fahrzeuge")
    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData:
        if fz_key not in _MASTER_DATA:
            raise KeyError(f"Mock adapter has no data for FzKey '{fz_key}'.")
        return _MASTER_DATA[fz_key]

    @_request("System")
    def get_system_watermark(self) -> SystemWatermark:
        return SystemWatermark(current_model_year=utcnow().year, update_date=self._system_watermark_date)

    @_request("Optionen")
    def fetch_options(self, fz_key: str, *, model_year: int) -> list[VariantOptionData]:
        return list(_OPTIONS.get(fz_key, []))

    @_request("OptionenFarben")
    def fetch_colours(self, *, werkscode: str) -> list[VariantColourData]:
        return list(_COLOURS.get(werkscode, []))

    @_request("PneuDimTS")
    def fetch_tyre_specs(self, *, type_approval_number: str) -> list[VariantTyreSpecData]:
        return list(_TYRE_SPECS.get(type_approval_number, []))

    @_request("Bilder")
    def fetch_images(self, fz_key: str) -> list[VariantImageData]:
        return list(_IMAGES.get(fz_key, []))

    # -- fourteen new Datennamen (Configurator C-0 / KAN-38 PR 2) -------

    @_request("FahrzeugArten")
    def list_vehicle_kinds(self) -> list[VehicleKindData]:
        return list(_VEHICLE_KINDS)

    @_request("Marken")
    def list_brands(self, *, fz_art: str) -> list[BrandData]:
        return list(_BRANDS.get(fz_art, []))

    @_request("ModellGruppen")
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
        return _model_groups(marken_nr=marken_nr, marke=marke, mod_grp_key=mod_grp_key)

    @_request("ModellGruppenKurz")
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
        # Not `self.list_model_groups`: one request here, not a second one for ModellGruppen.
        groups = _model_groups(marken_nr=marken_nr, marke=marke)
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

    @_request("Fahrzeuge")
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

    @_request("FahrzeugePreise")
    def fetch_vehicle_prices(self, fz_key: str, *, year: int | None = None) -> list[VehiclePriceData]:
        prices = _VEHICLE_PRICES.get(fz_key, [])
        return [p for p in prices if year is None or p.year == year]

    @_request("FzgWerteGruppiert")
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

    @_request("Typenscheine")
    def fetch_type_approvals(self, fz_key: str) -> list[str]:
        master = _MASTER_DATA.get(fz_key)
        return list(master.type_approval_numbers) if master else []

    @_request("KontrollschildInfo")
    def lookup_plate(self, plate: str, *, fz_art: str | None = None) -> list[PlateInfoData]:
        return list(_PLATES.get(plate, []))

    @_request("FahrzeugeMatch")
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

    @_request("FzgDatenTS")
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

    @_request("OptionenPack")
    def fetch_option_package_contents(self, opt_key: int) -> list[OptionPackageContentData]:
        return list(_OPTION_PACKAGES.get(opt_key, []))

    @_request("OptionenAusschluss")
    def fetch_option_exclusions(self, fz_key: str, *, year: int, opt_key: int) -> list[int]:
        return list(_OPTION_EXCLUSIONS.get(opt_key, []))

    @_request("OptionenZusatz")
    def fetch_option_conditions(self, fz_key: str, *, year: int, opt_key: int) -> list[OptionConditionData]:
        return list(_OPTION_CONDITIONS.get(opt_key, []))

    @_request("Codes")
    def fetch_codes(
        self, *, code_groups: list[str] | None = None, active_only: bool = False
    ) -> list[CodeMapEntryData]:
        if code_groups is None:
            return list(_CODES)
        wanted = set(code_groups)
        return [c for c in _CODES if c.code_group_nr in wanted]

    # -- the identAPI-shaped operations (KAN-547, KAN-548), over the same
    # fixtures, so the demo provider answers the identification waterfall and
    # the browse screen like the real one. Operation names are the identAPI's.

    @_request("vehiclebuild")
    def identify_by_vrn(self, stammnummer: str) -> list[BuiltVehicleData]:
        return [
            _built_vehicle(record, plate=plate)
            for plate, records in _PLATES.items()
            for record in records
            if record.stammnummer == stammnummer
        ]

    @_request("vehiclebuild")
    def identify_by_plate(self, plate: str) -> list[BuiltVehicleData]:
        return [_built_vehicle(record, plate=plate) for record in _PLATES.get(plate, [])]

    @_request("vehiclecatalog")
    def find_variants_by_type_approval(self, type_approval_number: str) -> list[VariantMasterData]:
        return [m for m in _MASTER_DATA.values() if type_approval_number in m.type_approval_numbers]

    @_request("vehiclecatalog")
    def find_variants_by_werkscode(self, werkscode: str) -> list[VariantMasterData]:
        return [m for m in _MASTER_DATA.values() if m.werkscode == werkscode]

    @_request("vehiclecatalog")
    def fetch_variant(self, fz_key: str) -> VariantMasterData | None:
        return _MASTER_DATA.get(fz_key)

    @_request("caption_code")
    def fetch_captions(self, lang: str) -> list[CaptionData]:
        suffix = "" if lang == "de" else f" ({lang})"
        return [
            CaptionData(
                group=c.group, kind=c.kind, code=c.code,
                label=f"{c.label}{suffix}" if c.label else None, label_short=c.label_short,
            )
            for c in _CAPTIONS
        ]

    @_request("vehiclecatalog")
    def list_catalogue_brands(self, *, vehicle_kind_code: str) -> list[CatalogueBrandData]:
        brands: dict[str, CatalogueBrandData] = {}
        for master in _MASTER_DATA.values():
            if master.vehicle_kind_code.lstrip("0") != vehicle_kind_code.lstrip("0"):
                continue
            brand = brands.setdefault(
                master.brand_display_name,
                CatalogueBrandData(name=master.brand_display_name, brand_nr=master.brand_code, models=[]),
            )
            if master.model_group_name not in brand.models:
                brand.models.append(master.model_group_name)
        return list(brands.values())

    @_request("vehiclecatalog")
    def list_catalogue_model_groups(
        self, *, vehicle_kind_code: str, brand: str, in_production: bool | None = None
    ) -> list[CatalogueModelGroupData]:
        groups: list[CatalogueModelGroupData] = []
        for master in _MASTER_DATA.values():
            if master.brand_display_name.lower() != brand.lower():
                continue
            if in_production and master.model_year_to is not None:
                continue
            group = _MODEL_GROUPS[master.brand_code]
            groups.append(
                CatalogueModelGroupData(
                    model_group_key=str(group.model_group_key), description=group.name_de,
                    model_search=group.short_name, production_year_from=group.production_from,
                    production_year_to=group.production_to,
                )
            )
        return groups

    @_request("vehiclecatalog")
    def search_catalogue_variants(
        self,
        *,
        vehicle_kind_code: str,
        brand: str | None = None,
        model_group_key: str | None = None,
        exfilters: Sequence[str] = (),
        sort: str | None = None,
        page_number: int = 1,
    ) -> VariantSearchPage:
        if not brand and not model_group_key:
            raise KeyError("A catalogue query needs a brand or a model group (ADR-112 (4)).")
        matches = [
            m
            for m in _MASTER_DATA.values()
            if (brand is None or m.brand_display_name.lower() == brand.lower())
            and (model_group_key is None or str(_MODEL_GROUPS[m.brand_code].model_group_key) == model_group_key)
        ]
        page_size = 10
        start = (page_number - 1) * page_size
        return VariantSearchPage(
            items=matches[start : start + page_size], page_number=page_number, page_size=page_size, total=len(matches)
        )

    @_request("vehiclecatalog")
    def facet_values(
        self, *, field: str, vehicle_kind_code: str, brand: str, model_group_key: str | None = None
    ) -> list[str]:
        attribute = {
            "fuel": "fuel_type_code", "body": "body_style_code", "drive": "drivetrain_code",
            "gearbox": "transmission_code", "type": "vehicle_kind_code",
        }.get(field)
        if attribute is None:
            return []
        values: list[str] = []
        for m in _MASTER_DATA.values():
            if m.brand_display_name.lower() != brand.lower():
                continue
            value = getattr(m, attribute)
            if value is not None and value not in values:
                values.append(value)
        return values

    @_request("Fahrzeuge")
    def fetch_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, valuation_date: dt.date, mileage: int
    ) -> ValuationResult:
        base = _MASTER_DATA.get(fz_key)
        base_price = (base.base_price if base else None) or Decimal("20000.00")
        age_years = max(valuation_date.year - first_registration.year, 0)
        depreciation = min(Decimal("0.12") * age_years + Decimal(mileage) / Decimal(100000) * Decimal("0.05"), Decimal("0.85"))
        return ValuationResult(provider_value=(base_price * (Decimal(1) - depreciation)).quantize(Decimal("0.01")), status_code="ok")

    @_request("Fahrzeuge")
    def fetch_forecast(self, *, fz_key: str, model_year: int) -> ForecastResult:
        base = _MASTER_DATA.get(fz_key)
        base_price = (base.base_price if base else None) or Decimal("20000.00")
        return ForecastResult(
            residual_value=(base_price * Decimal("0.55")).quantize(Decimal("0.01")),
            forecast_date=utcnow().date() + dt.timedelta(days=365),
        )
