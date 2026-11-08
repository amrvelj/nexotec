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
    EnergyLabelData,
    OptionPriceWithData,
    VariantColourData,
    VariantColoursData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantTyreSpecData,
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


def _money(value: Any) -> Decimal | None:
    """An amount where zero means *free*, not *unknown* (KAN-211): a colour
    or an option the manufacturer lists at CHF 0.00 keeps its zero, so the
    screen can say so instead of showing no price."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


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
    if re.fullmatch(r"\d{4}-\d{2}", text):  # the public spec's own `productionStart` example, `2020-08`
        return int(text[:4]) * 100 + int(text[5:7])
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
    # The vehicle kind is the `type` field and nothing else: an FzKey's digits
    # are not read for it (adapters decode nothing). A row without `type`
    # carries no kind and its codes resolve under none (`""`).
    kind = _code(item.get("type")) or ""
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
        vehicle_kind_code=_code(item.get("type")) or "",
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


# -- KAN-211: the details loaded when a car is opened (ADR-123) -----------------

# The identAPI's image enums, stored under the two-letter codes `ImageRef`
# has always held (`bild_typ` S/L, `bild_art` A/I — the SOAP service's own),
# so the frontend's existing maps keep reading them.
_IMAGE_SIZE_CODES = {"thumbnail": "S", "fullsize": "L"}
_IMAGE_PERSPECTIVE_CODES = {"outside": "A", "inside": "I"}


def parse_colours(item: Mapping[str, Any]) -> list[VariantColourData]:
    """`VehicleCatalogData.colorOptionsData` — the variant's colour options,
    on the variant row itself (the identAPI has no colour endpoint). The raw
    `colorType` (1 exterior, 2 upholstery) is the `colour_area` code
    `app.vehicle` resolves through the code map; a zero price is a free
    colour and is kept as such (FR-C-07's surcharge line)."""

    colours: list[VariantColourData] = []
    for raw in item.get("colorOptionsData") or []:
        if not isinstance(raw, Mapping):
            continue
        code = _text(raw.get("colorOptionCode"))
        description = _text(raw.get("colorDescription"))
        if code is None or description is None:
            continue
        colours.append(
            VariantColourData(
                colour_code=code,
                description=description,
                colour_area_code=_code(raw.get("colorType")),
                price=_money(raw.get("colorPrice")),
            )
        )
    return colours


def parse_variant_with_colours(body: Mapping[str, Any], *, fz_key: str) -> VariantColoursData | None:
    """The one `GET /vehiclecatalog/autoi/{fzKey}` answer as master data plus
    colours; `None` when no row carries that FzKey."""

    for item in data_items(body):
        variant = parse_variant(item)
        if variant is not None and variant.fz_key == fz_key:
            return VariantColoursData(master=variant, colours=parse_colours(item))
    return None


def _option_keys(values: Any) -> list[str]:
    """`packOptions` / `addition` rows carry `optionKey`; `exclusions` is a
    list of keys already."""

    keys: list[str] = []
    if not isinstance(values, list):
        return keys
    for value in values:
        key = _text(value.get("optionKey")) if isinstance(value, Mapping) else _text(value)
        if key:
            keys.append(key)
    return keys


def parse_option(item: Mapping[str, Any]) -> VariantOptionData | None:
    """One `VehicleOptionData`. `optionKey` is auto-i's identifier and the
    natural key (`option_code`); `optionCode` is the manufacturer's code the
    advisor searches by (`manufacturer_code`). `description` is the text in
    the language asked for, `descriptionDe` the fallback. `pack` says the
    option is *inside* a pack; an option *is* a pack when it lists
    `packOptions`."""

    key = _text(item.get("optionKey"))
    description = _text(item.get("description")) or _text(item.get("descriptionDe"))
    if key is None or description is None:
        return None
    pack_options = item.get("packOptions")
    priced_with: list[OptionPriceWithData] = []
    for addition in item.get("addition") or []:
        if not isinstance(addition, Mapping):
            continue
        related = _text(addition.get("optionKey"))
        if related:
            priced_with.append(OptionPriceWithData(option_code=related, price=_money(addition.get("price"))))
    return VariantOptionData(
        option_code=key,
        description=description,
        option_group=_text(item.get("group")),
        price=_money(item.get("price")),
        is_included=item.get("included") is True,
        is_package=isinstance(pack_options, list) and len(pack_options) > 0,
        equipment_feature_codes=_strings(item.get("searchCode")),
        manufacturer_code=_text(item.get("optionCode")),
        pack_option_codes=_option_keys(pack_options),
        excluded_option_codes=_option_keys(item.get("exclusions")),
        priced_with=priced_with,
    )


def parse_options(body: Mapping[str, Any]) -> list[VariantOptionData]:
    return [option for option in (parse_option(item) for item in data_items(body)) if option is not None]


def has_next_page(body: Mapping[str, Any]) -> bool:
    """json:api `links.next` — present while the provider holds more rows."""

    links = body.get("links")
    return isinstance(links, Mapping) and bool(_text(links.get("next")))


def parse_tyre_specs(body: Mapping[str, Any]) -> list[VariantTyreSpecData]:
    """`TyresAndRimsDimensionData`: `info.state` 0 = data, 1 = no data,
    2 = no vehicle (both read as no rows); `values` one row per axle, season
    and gearbox. The raw `axisCode` and `tyreType` are the `axle_position`
    and `tyre_type` codes `app.vehicle` resolves; the dimension is kept as
    the provider spells it."""

    data = body.get("data")
    if not isinstance(data, Mapping):
        return []
    tyres: list[VariantTyreSpecData] = []
    for raw in data.get("values") or []:
        if not isinstance(raw, Mapping):
            continue
        size = _text(raw.get("dimension"))
        if size is None:
            continue
        load_index = _int(raw.get("loadIndex"))
        tyres.append(
            VariantTyreSpecData(
                axle_code=_code(raw.get("axisCode")),
                size=size,
                load_index=str(load_index) if load_index is not None else None,
                speed_rating=_text(raw.get("speedIndex")),
                remark=_text(raw.get("remark")) or _text(raw.get("rimRemark")),
                season_code=_code(raw.get("tyreType")),
            )
        )
    return tyres


def parse_images(body: Mapping[str, Any]) -> list[VariantImageData]:
    """`VehicleImageData` rows: key, URL, size, perspective. The order the
    provider lists them is the sequence."""

    images: list[VariantImageData] = []
    for sequence, raw in enumerate(data_items(body)):
        key = _text(raw.get("key"))
        url = _text(raw.get("url"))
        if key is None or url is None:
            continue
        size = (_text(raw.get("size")) or "").lower()
        perspective = (_text(raw.get("perspective")) or "").lower()
        images.append(
            VariantImageData(
                bild_typ=_IMAGE_SIZE_CODES.get(size, size[:16] or "?"),
                bild_art=_IMAGE_PERSPECTIVE_CODES.get(perspective, perspective[:16] or "?"),
                image_key=key,
                sequence=sequence,
                image_url=url,
            )
        )
    return images


def parse_energy_labels(body: Mapping[str, Any]) -> list[EnergyLabelData]:
    """`EnergyLabelInfoData` rows: the class for its `calculationYear`; a row
    without either says nothing and is left out."""

    labels: list[EnergyLabelData] = []
    for raw in data_items(body):
        energy_class = _text(raw.get("energyEfficiencyClass"))
        year = _int(raw.get("calculationYear"))
        if energy_class is None or year is None:
            continue
        labels.append(
            EnergyLabelData(
                energy_class=energy_class.upper(),
                calculation_year=year,
                gearbox=_text(raw.get("gearbox")),
                label_url=_text(raw.get("energyLabelLink")),
            )
        )
    return labels
