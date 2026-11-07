"""Readers for the identAPI's json:api bodies (KAN-547, KAN-548) — pure
functions from the wire shapes auto-i documents in its public OpenAPI
document (`VehicleCatalogData`, `VehicleSpecificationData`,
`CodeCaptionCollectionGroup…`, `BrandCaptionCollection`,
`ModelGroupCatalogData`) to the adapter dataclasses `base.py` declares.

They interpret nothing: every coded field (`type`, `body`, `fuel`, `drive`,
`gearbox`, `category`, `euronorm`, `consumptionNorm`) is handed on as the
raw string the provider sent, for `app.vehicle` to resolve through the code
map. The two normalisations here are the provider's own conventions — a `0`
means *unknown* and is `None`, a `productionEnd` of null (or `000000`) means
*still in production* — plus one spelling rule for the identifiers a built
vehicle carries (Stammnummer without dots, plate upper-cased without spaces),
which is how the rest of the application stores them.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.integration.adapters.base import (
    BuiltVehicleData,
    CaptionData,
    CatalogueBrandData,
    CatalogueModelGroupData,
    VariantMasterData,
    VehiclePriceData,
)

# The identAPI's vehicle types: the raw `type` code (what every coded field's
# vocabulary is read for, the code map's `vehicle_kind` qualifier) and the
# path segment the catalogue endpoints take.
VEHICLE_TYPE_SEGMENTS: dict[str, str] = {"01": "passenger", "02": "utility", "03": "moto"}
VEHICLE_TYPE_CODES: dict[str, str] = {segment: code for code, segment in VEHICLE_TYPE_SEGMENTS.items()}
# The lists a caption group holds, as the wire names them.
CAPTION_KINDS: tuple[str, ...] = ("passenger", "utility", "moto", "global")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _code(value: Any) -> str | None:
    """A coded field as the provider sent it. Codes arrive as strings on
    most fields and as integers on a few (`category`, `consumptionNorm`,
    the version shapes); both read as the same string."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return _text(value)


def _int(value: Any) -> int | None:
    """`0` is the provider's *unknown* (PRD-Configurator §Data specification:
    "0 from the provider means unknown and is stored as null, never as
    zero")."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number != 0 else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number != 0 else None


def _price(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number > 0 else None


def _date(value: Any) -> dt.date | None:
    """A date-time string (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS…`) as a date."""

    text = _text(value)
    if text is None:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _yyyymm(value: Any) -> int | None:
    """A production bound as the six-digit `YYYYMM` the spec block stores;
    `None` for null and for the `000000` / `0000` the provider uses as
    *still in production*."""

    text = _text(value)
    if text is None or set(text) <= {"0"}:
        return None
    if re.fullmatch(r"\d{6}", text):
        return int(text)
    if re.fullmatch(r"\d{4}", text):
        return int(text) * 100 + 1
    day = _date(text)
    return day.year * 100 + day.month if day is not None else None


def _year(yyyymm: int | None) -> int | None:
    return yyyymm // 100 if yyyymm is not None else None


def normalise_stammnummer(value: Any) -> str | None:
    """`651.331.122` on the wire, `651331122` everywhere in Nexotec."""

    text = _text(value)
    return re.sub(r"[\s.\-]", "", text) if text else None


def normalise_plate(value: Any) -> str | None:
    """`LU 124493` / `lu124493` on the wire, `LU124493` in the plate cache."""

    text = _text(value)
    return re.sub(r"[\s.\-]", "", text).upper() if text else None


def _strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [text for text in (_text(v) for v in values) if text]


def _prices(values: Any) -> list[VehiclePriceData]:
    prices: list[VehiclePriceData] = []
    if not isinstance(values, list):
        return prices
    for item in values:
        if not isinstance(item, Mapping):
            continue
        year = _int(item.get("modelyear"))
        price = _int(item.get("price"))
        if year is not None and price is not None and price > 0:
            prices.append(VehiclePriceData(year=year, price=price))
    return prices


def data_items(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The `data` array of a json:api answer; an item answer (`data` is one
    object) reads as a one-element list, an empty answer as none."""

    data = body.get("data")
    if isinstance(data, Mapping):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    return []


def total_count(body: Mapping[str, Any]) -> int | None:
    meta = body.get("meta")
    if not isinstance(meta, Mapping):
        return None
    total = meta.get("totalCount")
    return int(total) if isinstance(total, int) and not isinstance(total, bool) else None


def parse_variant(item: Mapping[str, Any]) -> VariantMasterData | None:
    """One `VehicleCatalogData` (or the catalogue part of a
    `VehicleSpecificationData`) as master data. A row without an FzKey
    describes no catalogue variant and reads as `None`."""

    fz_key = _text(item.get("fzkey"))
    if fz_key is None:
        return None
    production_from = _yyyymm(item.get("productionStart"))
    production_to = _yyyymm(item.get("productionEnd"))
    prices = _prices(item.get("catalogPrices"))
    kind = _code(item.get("type")) or (fz_key[:2] if len(fz_key) >= 2 else "")
    return VariantMasterData(
        fz_key=fz_key,
        brand_code=_text(item.get("brandNr")) or "",
        brand_display_name=_text(item.get("brand")) or "",
        model_group_name=_text(item.get("model")) or "",
        variant_name=_text(item.get("version")) or _text(item.get("description")) or "",
        model_year_from=_year(production_from) or 0,
        model_year_to=_year(production_to),
        vehicle_kind_code=kind,
        fuel_type_code=_code(item.get("fuel")),
        body_style_code=_code(item.get("body")),
        drivetrain_code=_code(item.get("drive")),
        transmission_code=_code(item.get("gearbox")),
        base_price=_price(item.get("newPriceCatalog")),
        werkscode=_text(item.get("factoryCode")),
        type_approval_numbers=[n.upper() for n in _strings(item.get("typeApprovalNrs"))],
        model_type_name=_text(item.get("description")),
        trim_name=_text(item.get("edition")),
        displacement_ccm=_int(item.get("displacement")),
        cylinders=_int(item.get("cylinders")),
        gears=_int(item.get("gears")),
        ps=_int(item.get("ps")),
        kw=_int(item.get("kw")),
        total_ps=_int(item.get("totalPs")),
        total_kw=_int(item.get("totalKw")),
        system_kw=None,
        doors=_int(item.get("doors")),
        seats=_int(item.get("seats")),
        weight_empty_kg=_int(item.get("emptyWeight")),
        weight_total_kg=_int(item.get("totalWeight")),
        payload_kg=_int(item.get("cargoWeight")),
        towing_capacity_kg=_int(item.get("trailingWeight")),
        wheelbase_mm=_int(item.get("wheelBase")),
        consumption_mixed=_decimal(item.get("consumptionMix")),
        consumption_urban=_decimal(item.get("consumptionCity")),
        consumption_extra_urban=_decimal(item.get("consumptionCountry")),
        co2_gkm=_int(item.get("co2")),
        energy_consumption_kwh=_decimal(item.get("powerConsumption")),
        battery_capacity_kwh=_decimal(item.get("batteryCapacity")),
        range_km=_int(item.get("reachElectric")),
        tank_capacity_l=_int(item.get("fuelCapacity")),
        importcode=_text(item.get("importCode")),
        model_year=_int(item.get("modelyear")),
        production_from=production_from,
        production_to=production_to,
        base_price_year=max((p.year for p in prices), default=None),
        price_is_net=item.get("nettoPriceFlag") if isinstance(item.get("nettoPriceFlag"), bool) else None,
        vehicle_class_code=_code(item.get("category")),
        emission_standard_code=_text(item.get("euronorm")),
        consumption_norm_code=_code(item.get("consumptionNorm")),
        energy_label=_text(item.get("energylabel")),
        model_group_key=_text(item.get("modelGroupKey")),
        catalogue_prices=prices,
    )


def parse_variants(body: Mapping[str, Any]) -> list[VariantMasterData]:
    return [variant for variant in (parse_variant(item) for item in data_items(body)) if variant is not None]


def parse_built_vehicle(item: Mapping[str, Any]) -> BuiltVehicleData:
    """One `VehicleSpecificationData` row of a Vehicle Build answer: the
    registered car plus, when the provider resolved one, its variant."""

    production_from = _yyyymm(item.get("productionStart"))
    production_to = _yyyymm(item.get("productionEnd"))
    fz_key = _text(item.get("fzkey"))
    return BuiltVehicleData(
        fz_key=fz_key,
        vin=(_text(item.get("vin")) or "").upper() or None,
        stammnummer=normalise_stammnummer(item.get("vrn")),
        plate=normalise_plate(item.get("licensePlate")),
        first_registration_date=_date(item.get("firstAdmissionDate")),
        type_approval_number=(_text(item.get("typeApprovalNr")) or "").upper() or None,
        model_year=_int(item.get("modelyear")),
        vehicle_kind_code=_code(item.get("type")) or (fz_key[:2] if fz_key and len(fz_key) >= 2 else ""),
        brand_name=_text(item.get("brand")) or "",
        model_description=_text(item.get("description")) or _text(item.get("model")) or "",
        production_from=_year(production_from),
        production_to=_year(production_to),
        variant=parse_variant(item),
    )


def parse_built_vehicles(body: Mapping[str, Any]) -> list[BuiltVehicleData]:
    return [parse_built_vehicle(item) for item in data_items(body)]


def parse_captions(body: Mapping[str, Any]) -> list[CaptionData]:
    """`CodeCaptionCollectionGroupOfCodeCaptionOneLanguage`: one object whose
    keys are the caption groups, each holding the four lists."""

    captions: list[CaptionData] = []
    for group_object in data_items(body):
        for group, lists in group_object.items():
            if not isinstance(lists, Mapping):
                continue
            for kind in CAPTION_KINDS:
                entries = lists.get(kind)
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        continue
                    code = _code(entry.get("code"))
                    if code is None:
                        continue
                    captions.append(
                        CaptionData(
                            group=str(group),
                            kind=kind,
                            code=code,
                            label=_text(entry.get("captionLong")),
                            label_short=_text(entry.get("captionShort")),
                        )
                    )
    return captions


def parse_brands(body: Mapping[str, Any]) -> list[CatalogueBrandData]:
    brands: list[CatalogueBrandData] = []
    for item in data_items(body):
        name = _text(item.get("brand"))
        if name is None:
            continue
        brands.append(CatalogueBrandData(name=name, brand_nr=_text(item.get("brandNr")), models=_strings(item.get("models"))))
    return brands


def _year_bound(value: Any) -> int | None:
    text = _text(value)
    if text is None or set(text) <= {"0"}:
        return None
    return int(text) if text.isdigit() else None


def parse_model_groups(body: Mapping[str, Any]) -> list[CatalogueModelGroupData]:
    groups: list[CatalogueModelGroupData] = []
    for item in data_items(body):
        key = _text(item.get("modelGroupKey"))
        if key is None:
            continue
        groups.append(
            CatalogueModelGroupData(
                model_group_key=key,
                description=_text(item.get("description")) or _text(item.get("modelSearch")) or key,
                model_search=_text(item.get("modelSearch")),
                production_year_from=_year_bound(item.get("productionYearStart")),
                production_year_to=_year_bound(item.get("productionYearEnd")),
            )
        )
    return groups


def parse_distinct_values(body: Mapping[str, Any], field: str) -> list[str]:
    """`fields=<field> distinct`: each `data` item carries the one field (a
    scalar item reads as the value itself). Order kept, duplicates dropped."""

    values: list[str] = []
    data = body.get("data")
    items: Iterable[Any] = data if isinstance(data, list) else []
    wanted = field.lower()
    for item in items:
        if isinstance(item, Mapping):
            match = next((v for k, v in item.items() if str(k).lower() == wanted), None)
            value = _code(match)
        else:
            value = _code(item)
        if value is not None and value not in values:
            values.append(value)
    return values
