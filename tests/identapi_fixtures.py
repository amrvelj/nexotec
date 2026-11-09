"""Invented identAPI bodies for the KAN-547 / KAN-548 tests (KAN-491: no
value here comes from an account sheet or a specification example; the
shapes follow auto-i's public OpenAPI document). Served by
`tests/identapi_support.FakeAutoi.api_bodies`."""

from __future__ import annotations

from typing import Any

# A catalogue variant as `VehicleCatalogData`: every spec-block field the
# identAPI carries, with the provider's own code vocabulary.
DEMO_FZ_KEY = "01911001"
DEMO_MODEL_GROUP_KEY = "104911"
DEMO_TYPE_APPROVAL = "1ZZ911"
DEMO_WERKSCODE = "TP911ZZ"


def catalog_variant(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "description": "Testmarke Prüfling 2.0 TDI Avant quattro",
        "descriptionShort": "Prüfling 2.0 TDI",
        "factoryCode": DEMO_WERKSCODE,
        "importCode": "IMP911",
        "typeApprovalNr": DEMO_TYPE_APPROVAL,
        "fzkey": DEMO_FZ_KEY,
        "type": "01",
        "brand": "Testmarke",
        "brandNr": "911",
        "model": "Prüfling",
        "modelGroupKey": DEMO_MODEL_GROUP_KEY,
        "modelSearch": "PRUEFLING",
        "version": "2.0 TDI Avant quattro",
        "edition": "Sport",
        "specialVersion": False,
        "productionStart": "2021-03-01T00:00:00",
        "productionEnd": None,
        "newPriceCatalog": 58900,
        "nettoPriceFlag": False,
        "body": "5",
        "displacement": 1968,
        "cylinders": 4,
        "ps": 204,
        "totalPs": 0,
        "kw": 150,
        "totalKw": 0,
        "category": 4,
        "fuel": "3",
        "drive": "5",
        "gearbox": "5",
        "gears": 7,
        "doors": 5,
        "seats": 5,
        "emptyWeight": 1790,
        "totalWeight": 2310,
        "cargoWeight": 520,
        "trailingWeight": 2000,
        "wheelBase": 2820,
        "consumptionMix": 5.4,
        "consumptionCity": 0,
        "consumptionCountry": 0,
        "consumptionNorm": 4,
        "co2": 142,
        "energylabel": "B",
        "fuelCapacity": 58,
        "reachElectric": 0,
        "euronorm": "6d",
        "batteryCapacity": 0,
        "powerConsumption": 0,
        "catalogPrices": [
            {"modelyear": 2021, "price": 56900},
            {"modelyear": 2022, "price": 57900},
            {"modelyear": 2023, "price": 58900},
        ],
        "typeApprovalNrs": [DEMO_TYPE_APPROVAL, "1ZZ912"],
        "typeApprovalNrsGrayImport": [],
        "factoryCodes": [DEMO_WERKSCODE],
    }
    item.update(overrides)
    return item


def built_vehicle(**overrides: Any) -> dict[str, Any]:
    """A registered car as `VehicleSpecificationData`: the catalogue part plus
    the registration (invented Stammnummer, plate and VIN)."""

    item = catalog_variant()
    item.update(
        {
            "vrn": "900.911.001",
            "vin": "TESTVIN0000000911",
            "licensePlate": "ZH 911001",
            "firstAdmissionDate": "2022-05-17T00:00:00",
            "modelyear": "2022",
        }
    )
    item.update(overrides)
    return item


def body(items: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    return {
        "data": items,
        "links": {"self": "https://api.ident.autoi.ch/x"},
        "meta": {"totalCount": total if total is not None else len(items), "dataIsEmpty": not items},
    }


def captions_body(lang: str) -> dict[str, Any]:
    suffix = {"de": "", "fr": " (fr)", "it": " (it)", "en": " (en)"}[lang]
    return {
        "data": [
            {
                "type": {
                    "global": [
                        {"code": "01", "captionLong": f"Personenwagen{suffix}", "captionShort": "PW"},
                        {"code": "02", "captionLong": f"Nutzfahrzeug{suffix}", "captionShort": "NF"},
                    ]
                },
                "fuel": {
                    "passenger": [{"code": "3", "captionLong": f"Diesel{suffix}", "captionShort": "D"}],
                    "moto": [{"code": "3", "captionLong": f"Bleifrei Kat.{suffix}", "captionShort": "BK"}],
                },
                "gearbox": {"passenger": [{"code": "5", "captionLong": f"Automat{suffix}", "captionShort": None}]},
            }
        ]
    }


def brands_body() -> dict[str, Any]:
    return body(
        [
            {
                "brand": "Testmarke",
                "brandNr": "911",
                "models": ["Prüfling", "Muster"],
                "modelssearch": ["PRUEFLING", "MUSTER"],
            },
            {"brand": "Zweitmarke", "brandNr": "912", "models": ["Beispiel"], "modelssearch": ["BEISPIEL"]},
        ]
    )


def model_groups_body() -> dict[str, Any]:
    return body(
        [
            {
                "modelGroupKey": DEMO_MODEL_GROUP_KEY,
                "productionYearStart": "2021",
                "productionYearEnd": "0000",
                "modelSearch": "PRUEFLING",
                "description": "Prüfling (2021-)",
            },
            {
                "modelGroupKey": "104912",
                "productionYearStart": "2015",
                "productionYearEnd": "2020",
                "modelSearch": "MUSTER",
                "description": "Muster (2015-2020)",
            },
        ]
    )


def match_body(match_code: int) -> dict[str, Any]:
    return {
        "data": {
            "matchCode": match_code,
            "matchCodeText": ["None", "Exact", "Multiple"][match_code],
            "vehicle": catalog_variant(),
        }
    }


# -- KAN-211: the details loaded when a car is opened ---------------------------------


def colour_options() -> list[dict[str, Any]]:
    """`VehicleCatalogData.colorOptionsData`: two exterior colours (one free, one
    with a surcharge) and one upholstery colour."""

    return [
        {"colorOptionCode": "C01", "colorType": 1, "colorDescription": "Mythosschwarz Metallic", "colorPrice": 0},
        {"colorOptionCode": "C02", "colorType": 1, "colorDescription": "Tangorot Metallic", "colorPrice": 1150},
        {"colorOptionCode": "U07", "colorType": 2, "colorDescription": "Leder schwarz", "colorPrice": 0},
    ]


def option(key: str, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "fzKey": DEMO_FZ_KEY,
        "year": 0,
        "optionKey": key,
        "description": f"Option {key}",
        "descriptionDe": f"Option {key}",
        "descriptionFr": f"Option {key} (fr)",
        "descriptionIt": f"Option {key} (it)",
        "descriptionEn": f"Option {key} (en)",
        "included": False,
        "price": 500,
        "optionCode": f"M{key}",
        "pack": False,
        "group": "Komfort",
        "group2": None,
        "group3": [],
        "searchCode": [],
        "packOptions": [],
        "addition": [],
        "exclusions": [],
    }
    item.update(overrides)
    return item


def options_page(items: list[dict[str, Any]], *, page: int, total: int, has_next: bool) -> dict[str, Any]:
    """One json:api page of `VehicleOptionData`; `links.next` while more follow."""

    links = {
        "self": f"https://api.ident.autoi.ch/vehicleoption/catalog/autoi/{DEMO_FZ_KEY}/modelyear/0?page[number]={page}"
    }
    if has_next:
        links["next"] = (
            f"https://api.ident.autoi.ch/vehicleoption/catalog/autoi/{DEMO_FZ_KEY}/modelyear/0?page[number]={page + 1}"
        )
    return {"data": items, "links": links, "meta": {"totalCount": total, "dataIsEmpty": not items}}


def images_body() -> dict[str, Any]:
    return body(
        [
            {
                "key": f"{DEMO_FZ_KEY}-01",
                "url": f"https://images.example.test/{DEMO_FZ_KEY}/01-thumb.jpg",
                "size": "Thumbnail",
                "perspective": "Outside",
            },
            {
                "key": f"{DEMO_FZ_KEY}-02",
                "url": f"https://images.example.test/{DEMO_FZ_KEY}/01-full.jpg",
                "size": "FullSize",
                "perspective": "Outside",
            },
            {
                "key": f"{DEMO_FZ_KEY}-03",
                "url": f"https://images.example.test/{DEMO_FZ_KEY}/02-full.jpg",
                "size": "FullSize",
                "perspective": "Inside",
            },
        ]
    )


def tyres_body() -> dict[str, Any]:
    """`TyresAndRimsDimensionData`: both seasons in one answer, each row naming
    its own `tyreType` (121 summer, 122 winter) and `axisCode` (1 front+rear)."""

    def row(tyre_type: int, dimension: str, **overrides: Any) -> dict[str, Any]:
        item = {
            "typeApprovalNr": DEMO_TYPE_APPROVAL,
            "tyreType": tyre_type,
            "gearbox": 5,
            "axisCode": 1,
            "dimension": dimension,
            "standardTyres": 1,
            "width": 225,
            "height": 45,
            "radius": 18,
            "loadIndex": 95,
            "speedIndex": "Y",
            "extraLoad": False,
            "commercial": False,
            "rims": "7.5Jx18",
            "rimWidth": 7.5,
            "rimSize": 18,
            "rimOffset": 39,
            "rimRemark": None,
            "remark": "nur mit Leichtmetallfelgen" if tyre_type == 121 else None,
        }
        item.update(overrides)
        return item

    return {
        "data": {
            "info": {"state": 0, "stateMsg": "OK"},
            "values": [row(121, "225/45 R18"), row(122, "205/55 R17", loadIndex=91, speedIndex="H")],
            "vehicleInfo": {"techPermAxleLoadFront": 1100, "techPermAxleLoadRear": 1200},
        },
        "links": {"self": "https://api.ident.autoi.ch/x"},
        "meta": {"dataIsEmpty": False},
    }


def energy_label_body(*, energy_class: str = "B", year: int = 2026) -> dict[str, Any]:
    return body(
        [
            {
                "typeApprovalNr": DEMO_TYPE_APPROVAL,
                "brand": "Testmarke",
                "description": "Prüfling 2.0 TDI",
                "fuel": "Diesel",
                "gearbox": "Automat",
                "co2": 142,
                "energyEfficiencyClass": energy_class,
                "calculationYear": year,
                "energyLabelLink": "https://energielabel.example.test/1ZZ911.pdf",
            }
        ]
    )


VARIANT_PATH = f"/vehiclecatalog/autoi/{DEMO_FZ_KEY}"
OPTIONS_PATH = f"/vehicleoption/catalog/autoi/{DEMO_FZ_KEY}/modelyear/0"
IMAGES_PATH = f"/image/vehicle/{DEMO_FZ_KEY}"
TYRES_PATH = f"/tyresandrims/typeapproval/{DEMO_TYPE_APPROVAL}"
ENERGY_LABEL_PATH = f"/energylabel/info/typeapproval/{DEMO_TYPE_APPROVAL}"


def detail_bodies(*, options: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Every detail answer of the demo variant, for `FakeAutoi.api_bodies`."""

    items = (
        options
        if options is not None
        else [option("1001", searchCode=["navigation"]), option("1002", included=True, price=0, group="Serie")]
    )
    return {
        VARIANT_PATH: body([catalog_variant(colorOptionsData=colour_options())]),
        OPTIONS_PATH: options_page(items, page=1, total=len(items), has_next=False),
        IMAGES_PATH: images_body(),
        TYRES_PATH: tyres_body(),
        # 2025: a year the variant row itself (`energylabel: "B"`, stored as THIS
        # year by the master-data visit) does not write, so a test sees the
        # label endpoint's own row.
        ENERGY_LABEL_PATH: energy_label_body(year=2025),
    }


# -- valuation (KAN-552) — invented figures, never a specification example ------


def valuation_body(**overrides: Any) -> dict[str, Any]:
    """An `AutoiBewertungData` item answer: info + one valuation row."""

    row = {
        "vehicleExchangePrice": 18400, "vehiclePurchasePrice": 17100, "vehicleSalePrice": 21900,
        "optionsExchangePrice": 600, "optionsPurchasePrice": 500, "optionsSalePrice": 800,
        "kilometreExpected": 45000, "kilometreCorrectionExchange": -350,
        "vehiclePriceRangeFrom": 17800, "vehiclePriceRangeTo": 19100,
        "fzKey": "01911001", "modelYear": 2022, "firstAdmission": "2022-05-17", "kilometreState": 52000,
    }
    info = {
        "state": "OK", "stateCode": 0, "stateMsg": "OK", "manually": False,
        "screenMsg": [{"textDe": "Testhinweis", "textFr": "Note de test", "textIt": "Nota di prova", "textEn": "Test note"}],
        "printMsg": [],
    }
    info.update(overrides.pop("info", {}))
    row.update(overrides)
    return {"data": {"info": info, "valuation": [row]}, "links": {"self": "https://api.ident.autoi.ch/x"}}


def residual_table_body() -> dict[str, Any]:
    rows = [
        {"duration": months, "kilometreYear": km, "totalPercent": 60.5 - months / 4 - km / 5000, "totalPrice": 20000 - months * 100,
         "vehiclePercent": 0, "vehiclePrice": 0, "optionsPercent": 0, "optionsPrice": 0, "totalKilometre": months * km // 12}
        for km in (10000, 20000)
        for months in (12, 24, 36)
    ]
    return {"data": {"info": {"state": "OK", "stateCode": 0, "stateMsg": "OK"}, "vehicle": {"fzKey": "01911001"}, "residualvalue": rows}}
