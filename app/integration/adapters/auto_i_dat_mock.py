"""MockAutoIDatAdapter (WP-6 PR-2) — "build the provider-gateway against a
mock that returns realistic auto-i-dat shapes... do not skip this step to
save a day." Deterministic, no network call, no credentials needed (it
never touches services/secrets_backend.py at all) — registered as its own
provider_code (`auto_i_dat_mock`), a genuinely separate connection from
the real thing (PR-3's `auto_i_dat`), never a runtime flag on one
connection (the same "never a flag" posture rule 7 already applies to
sandbox/production).

It implements exactly the identAPI's `ProviderAdapter` operations (KAN-550;
`tests/architecture/test_provider_adapter_is_the_identapi.py`), over invented
fixtures (KAN-491) that carry auto-i's own codes as the identAPI sends them
(vehicle kind `01`, gearbox `1`/`5`, …), so the demo provider resolves through
the same code map as the real one (`scripts/seed_demo_provider_code_map.py`).
Every method is one metered request under the identAPI adapter's own
operation name (KAN-486), so a dev or staging call log reads like a real one.
No provider, this one included, is asked for its whole catalogue (KAN-485).
"""

import datetime as dt
import functools
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any, TypeVar, cast
from zoneinfo import ZoneInfo

from app.core.base import utcnow
from app.integration.adapters.base import (
    BestMatchResult,
    BuiltVehicleData,
    CaptionData,
    CatalogueBrandData,
    CatalogueModelGroupData,
    EnergyLabelData,
    PlateInfoData,
    ProviderMessage,
    ResidualTableData,
    ResidualValueRow,
    VariantColourData,
    VariantColoursData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantSearchPage,
    VariantTyreSpecData,
    VehiclePriceData,
    VehicleValuationData,
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
        vehicle_kind_code="01",  # provider code — never resolved here
        fuel_type_code="3",
        body_style_code="5",
        drivetrain_code="1",
        transmission_code="1",
        base_price=Decimal("28900.00"),
        werkscode="ALF14TB",
        type_approval_numbers=["1AB234"],
        # KAN-547: the spec block the identAPI carries, invented figures.
        model_type_name="Alfa Romeo Giulietta 1.4 TB Progression", trim_name="Progression",
        displacement_ccm=1111, cylinders=4, gears=6, ps=111, kw=81, doors=5, seats=5,
        weight_empty_kg=1111, weight_total_kg=1611, consumption_mixed=Decimal("6.1"), consumption_norm_code="4",
        co2_gkm=111, tank_capacity_l=51, production_from=201903, production_to=202112, base_price_year=2021,
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
        vehicle_kind_code="01",
        fuel_type_code="3",
        body_style_code="5",
        drivetrain_code="1",
        transmission_code="5",
        base_price=Decimal("42500.00"),
        werkscode="VW20TSI",
        type_approval_numbers=["2CD456"],
        model_type_name="Volkswagen Golf GTI 2.0 TSI DSG", trim_name="GTI",
        displacement_ccm=2222, cylinders=4, gears=7, ps=222, kw=163, doors=5, seats=5,
        weight_empty_kg=1222, weight_total_kg=1722, consumption_mixed=Decimal("7.2"), consumption_norm_code="4",
        co2_gkm=122, tank_capacity_l=52, production_from=202101, base_price_year=2023, price_is_net=False,
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
        vehicle_kind_code="01",
        fuel_type_code="4",
        body_style_code="4",
        drivetrain_code="2",
        transmission_code="5",
        base_price=Decimal("54900.00"),
        werkscode="BMW320D",
        type_approval_numbers=["3EF789"],
        model_type_name="BMW 320d xDrive Touring M Sport", trim_name="M Sport",
        displacement_ccm=3333, cylinders=6, gears=8, ps=333, kw=245, doors=5, seats=5,
        weight_empty_kg=1333, weight_total_kg=1833, towing_capacity_kg=1300, consumption_mixed=Decimal("5.3"),
        consumption_norm_code="4", co2_gkm=133, tank_capacity_l=53, production_from=202003, base_price_year=2023,
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

# Keyed by FzKey: the identAPI carries a variant's colours on its catalogue
# row (`colorOptionsData`, KAN-211). `price=None` on the grey models the
# common "free" case; `RED`'s price models FR-C-07's "surcharge is a price
# line in build mode". The raw colour-area codes (1 exterior, 2 interior) are
# resolved by `app.vehicle` through the code map, which migration
# `b0a125a0a583` seeds for this provider too (KAN-75).
_COLOURS: dict[str, list[VariantColourData]] = {
    fz_key: [
        VariantColourData(colour_code="BLK", description="Black metallic", colour_area_code="1", price=Decimal("0.00")),
        VariantColourData(
            colour_code="RED", description="Rosso competizione", colour_area_code="1", price=Decimal("1100.00")
        ),
        VariantColourData(colour_code="GRY", description="Grey cloth", colour_area_code="2"),
    ]
    for fz_key in _DEMO_FZ_KEYS
}

# Keyed by Typenschein, as `GET /tyresandrims/typeapproval/{nr}` is; one
# answer carries both seasons. One "both axles" row (axle code 1) models the
# KAN-43 fix; `remark`/`season_code` model FR-C-08. The raw axle codes (2
# front, 3 rear, 1 both) and season codes (121 summer, 122 winter) resolve
# through the code map seeded by `b0a125a0a583` (KAN-75, C1).
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


# ModelGroupKeys are invented but stable per brand, matching each demo
# vehicle's own model_group_name.
_MODEL_GROUPS: dict[str, CatalogueModelGroupData] = {
    master.brand_code: CatalogueModelGroupData(
        model_group_key=str(100000 + index),
        description=master.model_group_name,
        model_search=master.model_group_name[:10],
        production_year_from=master.model_year_from,
        production_year_to=master.model_year_to,
    )
    for index, master in enumerate(_MASTER_DATA.values())
}

# The registered cars behind the demo plates, answered as Vehicle Build rows
# (`_built_vehicle`).
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

# Stored under the size and perspective codes `ImageRef` holds (S small / L
# large; A exterior / I interior), as `identapi_parse` maps the identAPI's
# image rows — values the frontend's BILD_TYP_KEY/BILD_ART_KEY maps know.
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
    CaptionData(group="gearbox", kind="passenger", code="1", label="Demo-Handschaltung", label_short="M"),
    CaptionData(group="gearbox", kind="passenger", code="5", label="Demo-Automat", label_short="A"),
]


# KAN-549 — an invented VIN for the VIN rung, the Golf behind plate BE123456
# (never the specification's sample VIN, KAN-491). Answered only where the
# connection's `vin` row is granted; the demo connection probes `ident_basis`
# and `bewertung` but never `vin`, so the rung says «nicht im Vertrag», as on
# the test licence.
_VINS: dict[str, tuple[str, int]] = {"WVWZZZDEMV0000001": ("BE123456", 0)}


def _built_vehicle(record: PlateInfoData, *, plate: str | None, vin: str | None = None) -> BuiltVehicleData:
    """A mock plate record as the identAPI's Vehicle Build row: the variant
    is the demo variant whose Typenscheine carry the record's number."""

    variant = next((m for m in _MASTER_DATA.values() if record.type_approval_number in m.type_approval_numbers), None)
    return BuiltVehicleData(
        fz_key=variant.fz_key if variant else None,
        vin=vin,
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


def _request(operation: str) -> Callable[[_Method], _Method]:
    """Each mock method is one request under the identAPI adapter's own
    operation name, so a dev or staging call log reads like a real one
    (KAN-486)."""

    def wrap(method: _Method) -> _Method:
        @functools.wraps(method)
        def metered(*args: Any, **kwargs: Any) -> Any:
            with provider_request(operation) as request:
                return request.attempt(lambda: method(*args, **kwargs))

        return cast(_Method, metered)

    return wrap


def _swiss_year() -> int:
    """The year in Switzerland now (the label is a fact of a Swiss year; the
    vehicle context's `swiss_today` is behind the context boundary)."""

    return dt.datetime.now(ZoneInfo("Europe/Zurich")).year


class MockAutoIDatAdapter:
    """The identAPI's shapes over invented fixtures, no live call."""

    @_request("vehicleoption_catalog")
    def fetch_options(self, fz_key: str, *, model_year: int) -> list[VariantOptionData]:
        return list(_OPTIONS.get(fz_key, []))

    @_request("tyresandrims_typeapproval")
    def fetch_tyre_specs(self, *, type_approval_number: str) -> list[VariantTyreSpecData]:
        return list(_TYRE_SPECS.get(type_approval_number, []))

    @_request("image_vehicle")
    def fetch_images(self, fz_key: str) -> list[VariantImageData]:
        return list(_IMAGES.get(fz_key, []))

    # KAN-211 (ADR-123): the local app and the screenshots load details on
    # open the way staging does. Colours ride on the variant answer (one
    # request); the energy label is the master data's letter as this year's
    # class.

    @_request("vehiclecatalog")
    def fetch_variant_with_colours(self, fz_key: str) -> VariantColoursData:
        if fz_key not in _MASTER_DATA:
            raise KeyError(f"Mock adapter has no data for FzKey '{fz_key}'.")
        master = _MASTER_DATA[fz_key]
        return VariantColoursData(master=master, colours=list(_COLOURS.get(fz_key, [])))

    @_request("energylabel_info")
    def fetch_energy_label(self, *, type_approval_number: str) -> list[EnergyLabelData]:
        master = next((m for m in _MASTER_DATA.values() if type_approval_number in m.type_approval_numbers), None)
        if master is None or not master.energy_label:
            return []
        # KAN-597: one row per gearbox, each with its label's PDF link (an
        # invented host, like every demo value).
        return [
            EnergyLabelData(
                energy_class=master.energy_label, calculation_year=_swiss_year(), gearbox=gearbox,
                label_url=f"https://energielabel.example.test/demo/{type_approval_number}-{suffix}.pdf",
            )
            for gearbox, suffix in (("Automat", "a"), ("Manuell", "m"))
        ]

    @_request("vehiclematch")
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

    # -- identification and browse (KAN-547, KAN-548, KAN-549), so the demo
    # provider answers the identification waterfall and the browse screen
    # like the real one.

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

    @_request("vehicle_vin")
    def identify_by_vin(self, vin: str) -> list[BuiltVehicleData]:
        if vin not in _VINS:
            return []
        plate, index = _VINS[vin]
        return [_built_vehicle(_PLATES[plate][index], plate=None, vin=vin)]

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
            if master.vehicle_kind_code != vehicle_kind_code:
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
            groups.append(_MODEL_GROUPS[master.brand_code])
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
            and (model_group_key is None or _MODEL_GROUPS[m.brand_code].model_group_key == model_group_key)
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

    # -- the identAPI's valuation (KAN-552), invented figures on the demo
    # variants' list prices: deterministic, never a real valuation.

    @_request("vehiclevaluation")
    def fetch_vehicle_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, mileage_km: int, option_keys: list[int]
    ) -> VehicleValuationData:
        base = _MASTER_DATA.get(fz_key)
        if base is None or base.base_price is None:
            return VehicleValuationData(
                state=20, state_message="Fahrzeug nicht gefunden (Demo)", manually=False, exchange_price=None,
                purchase_price=None, sale_price=None, vehicle_exchange_price=None, options_exchange_price=None,
                kilometre_correction_exchange=None, kilometre_expected=None, exchange_range_from=None,
                exchange_range_to=None, screen_messages=[], print_messages=[],
            )
        age_years = max(utcnow().date().year - first_registration.year, 0)
        expected_km = 15000 * max(age_years, 1)
        vehicle = (base.base_price * (Decimal("0.86") ** age_years) * Decimal("0.80")).quantize(Decimal(1))
        correction = (Decimal(expected_km - mileage_km) * Decimal("0.05")).quantize(Decimal(1))
        options = Decimal(150 * len(option_keys))
        exchange = vehicle + options
        return VehicleValuationData(
            state=0, state_message="OK", manually=False,
            exchange_price=exchange,
            purchase_price=(exchange * Decimal("0.93")).quantize(Decimal(1)),
            sale_price=(exchange * Decimal("1.18")).quantize(Decimal(1)),
            vehicle_exchange_price=vehicle, options_exchange_price=options,
            kilometre_correction_exchange=correction, kilometre_expected=expected_km,
            exchange_range_from=(exchange * Decimal("0.95")).quantize(Decimal(1)),
            exchange_range_to=(exchange * Decimal("1.05")).quantize(Decimal(1)),
            screen_messages=[
                ProviderMessage(
                    de="Demo-Bewertung — keine echte Bewertung.", fr="Évaluation de démonstration — pas une vraie évaluation.",
                    it="Valutazione dimostrativa — non una vera valutazione.", en="Demo valuation — not a real valuation.",
                )
            ],
            print_messages=[],
        )

    @_request("residualvalue_table")
    def fetch_residual_table(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, mileage_km: int, option_keys: list[int]
    ) -> ResidualTableData:
        base = _MASTER_DATA.get(fz_key)
        if base is None or base.base_price is None:
            return ResidualTableData(state=20, state_message="Fahrzeug nicht gefunden (Demo)", rows=[])
        rows = []
        for km in (10000, 15000, 20000, 25000, 30000):
            for months in (12, 24, 36, 48, 60, 72):
                years = Decimal(months) / Decimal(12)
                percent = (Decimal(100) * Decimal("0.86") ** (months // 12) - Decimal(km - 15000) / Decimal(1000) * Decimal("0.4") * years)
                percent = percent.quantize(Decimal("0.1"))
                rows.append(ResidualValueRow(
                    duration_months=months, km_per_year=km, total_percent=percent,
                    total_price=(base.base_price * percent / Decimal(100)).quantize(Decimal(1)),
                ))
        return ResidualTableData(state=0, state_message="OK", rows=rows)

