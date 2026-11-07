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
        "catalogPrices": [{"modelyear": 2021, "price": 56900}, {"modelyear": 2022, "price": 57900}, {"modelyear": 2023, "price": 58900}],
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
            {"brand": "Testmarke", "brandNr": "911", "models": ["Prüfling", "Muster"], "modelssearch": ["PRUEFLING", "MUSTER"]},
            {"brand": "Zweitmarke", "brandNr": "912", "models": ["Beispiel"], "modelssearch": ["BEISPIEL"]},
        ]
    )


def model_groups_body() -> dict[str, Any]:
    return body(
        [
            {"modelGroupKey": DEMO_MODEL_GROUP_KEY, "productionYearStart": "2021", "productionYearEnd": "0000", "modelSearch": "PRUEFLING", "description": "Prüfling (2021-)"},
            {"modelGroupKey": "104912", "productionYearStart": "2015", "productionYearEnd": "2020", "modelSearch": "MUSTER", "description": "Muster (2015-2020)"},
        ]
    )


def match_body(match_code: int) -> dict[str, Any]:
    return {"data": {"matchCode": match_code, "matchCodeText": ["None", "Exact", "Multiple"][match_code], "vehicle": catalog_variant()}}
