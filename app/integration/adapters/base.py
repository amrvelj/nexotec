"""ProviderAdapter (WP-6 PR-2) — the shape every provider integration
implements, modeled on auto-i-dat's own capability set (Integrations &
API Credentials v0.1's entitlement matrix; PRD-Vehicles' mirror/sync-
strategy table). `MockAutoIDatAdapter` (this PR) and
`AutoIDatSoapAdapter` (PR-3) both implement this Protocol — callers
(app/vehicle/services/catalogue_sync.py, PR-4) never know which one they
have; only `services/gateway.py` resolves that, by `provider_code`.

Return shapes are plain, frozen dataclasses — never a raw provider code
anywhere in a field (PRD's own reading rule: "application code never sees
a provider code"). Resolving a `*_code` field through
`app.vehicle.public`'s `resolve_provider_code`-equivalent is the caller's
job (PR-4), not the adapter's — the adapter's job is only to speak the
provider's protocol and hand back what it said, unresolved.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class VariantMasterData:
    fz_key: str
    brand_code: str
    brand_display_name: str
    model_group_name: str
    variant_name: str
    model_year_from: int
    model_year_to: int | None
    vehicle_kind_code: str
    fuel_type_code: str | None
    body_style_code: str | None
    drivetrain_code: str | None
    transmission_code: str | None
    base_price: Decimal | None
    # `Fahrzeuge.Werkscode` (Herstellercode, p10). Carried here because
    # `OptionenFarben` keys on it, not on `FzKey` (KAN-38 PR 1) — the
    # catalogue sync persists it to `ModelVariant.werkscode` (a C-A
    # spec-block column) so the colour fetch has it.
    werkscode: str | None = None
    type_approval_numbers: list[str] = field(default_factory=list)

    # -- KAN-547: the full specification block (ADR-071), as the identAPI's
    # `VehicleCatalogData` delivers it. Every field is optional: the SOAP
    # parser and the mock fill what they know, and a `0` the provider means
    # as "unknown" is already `None` here (the adapter's one normalisation,
    # not an interpretation). Attribute names equal the spec-block column
    # names on `ModelVariant`, so `catalogue_sync` copies them by name.
    model_type_name: str | None = None  # `description` — the long name
    trim_name: str | None = None  # `edition`
    displacement_ccm: int | None = None
    cylinders: int | None = None
    gears: int | None = None
    ps: int | None = None
    kw: int | None = None
    total_ps: int | None = None
    total_kw: int | None = None
    system_kw: int | None = None  # no identAPI counterpart: always None from the identAPI
    doors: int | None = None
    seats: int | None = None
    weight_empty_kg: int | None = None
    weight_total_kg: int | None = None
    payload_kg: int | None = None
    towing_capacity_kg: int | None = None
    wheelbase_mm: int | None = None
    consumption_mixed: Decimal | None = None
    consumption_urban: Decimal | None = None
    consumption_extra_urban: Decimal | None = None
    co2_gkm: int | None = None
    energy_consumption_kwh: Decimal | None = None
    battery_capacity_kwh: Decimal | None = None
    range_km: int | None = None
    tank_capacity_l: int | None = None
    importcode: str | None = None
    model_year: int | None = None
    production_from: int | None = None  # YYYYMM
    production_to: int | None = None  # YYYYMM; None = still in production
    base_price_year: int | None = None
    price_is_net: bool | None = None
    # Raw provider codes of the spec block's reference fields — resolved by
    # `app.vehicle` through the code map, never here (the reading rule).
    vehicle_class_code: str | None = None  # `category`
    emission_standard_code: str | None = None  # `euronorm`
    consumption_norm_code: str | None = None  # `consumptionNorm`
    valuation_classification_code: str | None = None  # `Einstufung`: no identAPI counterpart, always None there
    # The identAPI's current A–G label (no year on the wire); `app.vehicle`
    # decides how it is stored (ADR-042).
    energy_label: str | None = None
    # The provider's model-group key (`modelGroupKey`). Not stored by the
    # sync (our `ModelGroup` is one row per model name, the provider's are
    # per generation); KAN-548 scopes a browse page by it in the URL.
    model_group_key: str | None = None
    # `catalogPrices` — the list price per model year.
    catalogue_prices: list["VehiclePriceData"] = field(default_factory=list)


@dataclass(frozen=True)
class BuiltVehicleData:
    """One registered vehicle as the identAPI's Vehicle Build resource
    describes it (`GET /vehiclebuild/{vrn|licenseplate|…}/{id}`,
    `VehicleSpecificationData`, KAN-547). A plate lookup returns one of these
    per vehicle on a Wechselschild; `variant` is the catalogue variant the
    provider resolved it to (`needAutoiKeyIfExists`: `None` when it found
    none). Personal data (VIN, Stammnummer, plate) travels here and only
    here; the caller decides what it stores (the plate-lookup cache)."""

    fz_key: str | None
    vin: str | None
    stammnummer: str | None
    plate: str | None
    first_registration_date: dt.date | None
    type_approval_number: str | None
    model_year: int | None
    vehicle_kind_code: str
    brand_name: str
    model_description: str
    production_from: int | None  # year
    production_to: int | None  # year
    variant: VariantMasterData | None


@dataclass(frozen=True)
class CaptionData:
    """One caption of `GET /caption/code?lang=` (KAN-547): `group` is the
    provider's caption group (`fuel`, `body`, `drive`, …), `kind` the list it
    sits in (`passenger` / `utility` / `moto` / `global`), as delivered."""

    group: str
    kind: str
    code: str
    label: str | None
    label_short: str | None


@dataclass(frozen=True)
class CatalogueBrandData:
    """`GET /vehiclecatalog/brand/{vehicleType}` — one brand with its model
    names (KAN-548). Not stored: the browse screen's drill-down reads it live
    (ADR-112)."""

    name: str
    brand_nr: str | None
    models: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CatalogueModelGroupData:
    """`GET /vehiclecatalog/modelgroup/{vehicleType}/{brand}` — one model
    group (KAN-548). `production_year_to` is `None` while in production
    (the wire's `0000`)."""

    model_group_key: str
    description: str
    model_search: str | None
    production_year_from: int | None
    production_year_to: int | None


@dataclass(frozen=True)
class VariantSearchPage:
    """One provider page of a catalogue query (KAN-548, ADR-112 (4)): the
    variants on it, the page number asked for, the provider's page size, and
    `total` from `includeCount` (None when the provider did not count)."""

    items: list[VariantMasterData]
    page_number: int
    page_size: int
    total: int | None


@dataclass(frozen=True)
class VariantOptionData:
    option_code: str
    description: str
    option_group: str | None
    price: Decimal | None
    # KAN-43 (C-E) — `Inklusiv`/`PackCode`/`SuchCode` exist on the raw
    # `Optionen` response but were parsed by no one until now.
    # `equipment_feature_codes` are auto-i-dat's own mapping from this
    # concrete option to one or more of the ~70 curated SuchCode
    # marketplace features (CodeGrpNr 045, comma-separated at the
    # provider — one option can carry several) — not an alternative to
    # selecting this option, a TAG on it (an advisor always selects real
    # options, in both build and record mode; SuchCode is what Stock's
    # marketplace publishing reads later, and the mapping is
    # provider-supplied but user-correctable, never authoritative).
    is_included: bool = False
    is_package: bool = False
    equipment_feature_codes: list[str] = field(default_factory=list)
    # KAN-211 — the identAPI carries two identifiers per option: `optionKey`
    # (auto-i's own, the natural key, in `option_code`) and `optionCode`, the
    # manufacturer's code the advisor knows from the price list and searches
    # by (Anto, 2026-11-08). None where the provider sends none.
    manufacturer_code: str | None = None
    # KAN-211 — the option's relations as the identAPI delivers them, by the
    # related option's key: the options this one is a package of (`packOptions`),
    # the ones it excludes (`exclusions`) and the ones it is priced in
    # combination with (`addition`, with that price). Stored and shown, never
    # enforced (ADR-072).
    pack_option_codes: list[str] = field(default_factory=list)
    excluded_option_codes: list[str] = field(default_factory=list)
    priced_with: list["OptionPriceWithData"] = field(default_factory=list)


@dataclass(frozen=True)
class OptionPriceWithData:
    """KAN-211 — `VehicleOptionAdditionData`: this option costs `price` when
    taken together with `option_code`."""

    option_code: str
    price: Decimal | None


@dataclass(frozen=True)
class VariantColoursData:
    """KAN-211 — what one `GET /vehiclecatalog/autoi/{fzKey}` answers: the
    variant's master data and, on the same row, its colour options
    (`colorOptionsData`). The identAPI has no colour endpoint of its own, so
    colours ride on the variant request (ADR-123)."""

    master: VariantMasterData
    colours: list["VariantColourData"]


@dataclass(frozen=True)
class EnergyLabelData:
    """KAN-211 — one `EnergyLabelInfoData` row of `GET /energylabel/info/
    typeapproval/{nr}`: the A–G class valid for `calculation_year`; several
    rows per Typenschein where gearboxes differ."""

    energy_class: str
    calculation_year: int
    gearbox: str | None = None
    label_url: str | None = None


@dataclass(frozen=True)
class VariantColourData:
    colour_code: str
    description: str
    # KAN-75 — the provider's raw `FarbArt` (auto-i-dat: 1 Aussenfarbe,
    # 2 Polsterfarbe), never decoded here: the catalogue sync resolves it
    # through the provider code map (`colour_area`), and an unknown code is a
    # mapping gap, never a guessed "exterior".
    colour_area_code: str | None
    # KAN-43 (C-E) — FR-C-07: "the surcharge is a price line in build
    # mode." `Preis` is on the raw `OptionenFarben` response but was
    # parsed by no one until now.
    price: Decimal | None = None


@dataclass(frozen=True)
class VariantTyreSpecData:
    # KAN-75 — the provider's raw `AchsenCode` (auto-i-dat: 1 Vorne+Hinten,
    # 2 Vorne, 3 Hinten, 4-7 Varianten), resolved by the catalogue sync
    # through the code map (`axle_position`); None when the row carries none.
    axle_code: str | None
    size: str
    load_index: str | None
    speed_rating: str | None
    # KAN-43 (C-E) — FR-C-08: "the BemDe remark ... is displayed with the
    # dimension, because a dimension valid only with alloy wheels and
    # shown without that caveat is how the wrong tyre gets ordered." Also
    # FR-C-08's own "PneuTyp (summer/winter)". Both parsed by no one until
    # now — `remark` and `season` were entirely dropped from the pipeline.
    remark: str | None = None
    # KAN-75: the raw `PneuTyp` (auto-i-dat: 121 Sommer, 122 Winter),
    # resolved through the code map (`tyre_type`).
    season_code: str | None = None


@dataclass(frozen=True)
class VariantImageData:
    bild_typ: str
    bild_art: str
    image_key: str
    sequence: int
    # KAN-43 (C-E) — the full `BildURL`, kept alongside `image_key` (the
    # basename-only sync key) so a caller can actually render the photo.
    # Previously discarded at ingestion because `ImageRef` had nowhere to
    # put it (WP-6's own Open Item).
    image_url: str | None = None


@dataclass(frozen=True)
class VehicleKindData:
    """`FahrzeugArten` (Configurator C-0 / KAN-38 PR 2, p5) — the three
    top-level `FzArt` values (Personenwagen/Nutzfahrzeuge/Motorräder)
    every other search's `FzArt` parameter is drawn from."""

    code: str
    label: str


@dataclass(frozen=True)
class BrandData:
    """`Marken` (p6) — every brand for one `FzArt`."""

    code: str
    name: str


@dataclass(frozen=True)
class ModelGroupData:
    """`ModellGruppen` (p7) — full shape, keyed by the numeric
    `ModGrpKey` every other per-model-group search accepts.
    `production_from`/`production_to` are plain 4-digit years here
    (unlike `Fahrzeuge`'s own `ProdVon`/`ProdBis`, which are 6-digit
    `JJJJMM`) — `0000` means "still produced" -> `None`.
    """

    model_group_key: int
    name_de: str
    short_name: str
    production_from: int | None
    production_to: int | None


@dataclass(frozen=True)
class ModelGroupShortData:
    """`ModellGruppenKurz` (p8) — a deliberately thinner shape than
    `ModellGruppen`: brand + short model name only, meant for a
    type-ahead/search list, not a spec-carrying lookup. Not for
    motorcycles (spec's own restriction).
    """

    brand_code: str
    brand_name: str
    short_name: str


@dataclass(frozen=True)
class VehiclePriceData:
    """`FahrzeugePreise` (p11) — the new-price-by-model-year table a
    `Fahrzeuge` row's own `LetzterNP` is only the latest entry of.
    """

    year: int
    price: int


@dataclass(frozen=True)
class PlateInfoData:
    """`KontrollschildInfo` (p14) — **can return more than one row**: a
    Wechselschild (one plate, two vehicles) or the same plate legitimately
    existing on both a car and a motorcycle. Never assume `.first`; a
    caller identifying a vehicle from a plate must show a picker on >1
    row (C-D's own FR-C-02 waterfall).
    """

    vehicle_kind_code: str
    brand_name: str
    model_description: str
    production_from: int | None
    production_to: int | None
    type_approval_number: str
    first_registration_date: dt.date | None
    stammnummer: str


@dataclass(frozen=True)
class BestMatchResult:
    """`FahrzeugeMatch` (p27) — the row fields are "siehe Datenname
    Fahrzeuge" (same shape as `VariantMasterData`); `match_code` is a
    sibling of `Status`/`StatusMsg` in the response's `Info` block, not a
    row field: `1` = eindeutig (unique), `2` = bestmöglich (best-effort —
    C-D's own `ConfigurationMatchStatus.BEST_MATCH_CONFIRMED` records a
    human accepting exactly this case, never applying it silently).
    """

    vehicle: VariantMasterData
    match_code: int


@dataclass(frozen=True)
class TypeApprovalDataResult:
    """`FzgDatenTS` (p28) — Typenschein-linked consumption/emission data
    for a used vehicle with no `FzKey` of its own. Searching by
    `TypSchNr` alone returns only `euro_norm`; adding `Getriebe`+`Gänge`
    returns every field (spec's own note).
    """

    euro_norm: str | None
    fuel_consumption_mixed: Decimal | None
    emission_standard_code: str | None
    energy_efficiency_category: str | None
    co2: int | None
    kerb_weight: int | None
    energy_consumption: Decimal | None


@dataclass(frozen=True)
class OptionPackageContentData:
    """`OptionenPack` (p17) — one content line of an option package."""

    opt_key: int
    description: str


@dataclass(frozen=True)
class OptionConditionData:
    """`OptionenZusatz` (p19) — an extra condition attached to an
    option (`Aktion` is CodeGrpNr 047 — ADR-072's own `option_relation_type`
    canonical list, e.g. "nur mit"/"nicht in Kombination mit").
    """

    opt_key: int
    description: str
    aktion_code: str | None
    price: Decimal | None


@dataclass(frozen=True)
class CodeMapEntryData:
    """`Codes` (p24) — one `(CodeGrpNr, CodeNr)` -> label row, as delivered.
    Nothing resolves this into `app.vehicle`'s `ProviderCodeMap`: that table
    was seeded from the specification's own tables (migration 7c4e9a2b6d13),
    and a refresh from a live `Codes` call is not built — it needs a real
    account and a ruling on whose credentials refresh a global table
    (ADR-013). Its only reader today is `scripts/verify_auto_i_dat.py`, which
    diffs it against the seed.
    """

    code_group_nr: str
    code_nr: str
    label_de: str
    label_short_de: str | None


@dataclass(frozen=True)
class SystemWatermark:
    """`System` — the current model year plus the provider's own last
    update date. PR-4's sync-age alarm (A-12) compared `update_date`
    against wall-clock time; it was retired with the full catalogue
    download (KAN-485, KAN-497). The gateway's Test-connection probe still
    calls `get_system_watermark()` to prove the account answers.
    """

    current_model_year: int
    update_date: dt.date


@dataclass(frozen=True)
class ValuationResult:
    provider_value: Decimal
    status_code: str  # "ok" | "too_new" | "too_old" | "mileage_out_of_range" | "no_classification"


@dataclass(frozen=True)
class ForecastResult:
    residual_value: Decimal
    forecast_date: dt.date


# -- the identAPI's valuation (KAN-552) ---------------------------------------


@dataclass(frozen=True)
class ProviderMessage:
    """A screen or print message of a valuation answer, in the four languages
    the request asked for — provider text, shown as delivered (ADR-044)."""

    de: str | None
    fr: str | None
    it: str | None
    en: str | None


@dataclass(frozen=True)
class VehicleValuationData:
    """`GET /vehiclevaluation/{fzKey}/{modelYear}/{firstAdmission}/{kilometreState}`:
    `state` 0 is a valuation; any other is the provider's own reason it could
    not value the car (`state_message`, as delivered). Prices are CHF; the
    trade-in (exchange) price is what the valuation is based on — purchase
    below it, sale above. Each `*_price` is the vehicle's plus its options'."""

    state: int
    state_message: str | None
    manually: bool
    exchange_price: Decimal | None
    purchase_price: Decimal | None
    sale_price: Decimal | None
    vehicle_exchange_price: Decimal | None
    options_exchange_price: Decimal | None
    kilometre_correction_exchange: Decimal | None
    kilometre_expected: int | None
    exchange_range_from: Decimal | None
    exchange_range_to: Decimal | None
    screen_messages: list[ProviderMessage]
    print_messages: list[ProviderMessage]


@dataclass(frozen=True)
class ResidualValueRow:
    """One cell of the predefined residual-value table: after `duration_months`
    at `km_per_year`, the car keeps `total_percent` of its new price."""

    duration_months: int
    km_per_year: int
    total_percent: Decimal | None
    total_price: Decimal | None


@dataclass(frozen=True)
class ResidualTableData:
    """`GET /residualvalue/{fzKey}/{modelYear}` — the whole table in one request."""

    state: int
    state_message: str | None
    rows: list[ResidualValueRow]


class ProviderAdapter(Protocol):
    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData: ...

    # No `FzKeyChanged` (the changed-keys list a full mirror is built on):
    # the provider is never asked for its whole catalogue (ADR-023 as amended
    # 2026-10-29, KAN-485; tests/architecture/test_no_bulk_catalogue_retrieval.py).

    def get_system_watermark(self) -> SystemWatermark: ...

    # `fetch_options` / `fetch_colours` / `fetch_tyre_specs` take more than
    # an `FzKey` (KAN-38 PR 1): the real Datennamen require `Jahr`
    # (Optionen, p15), `Werkscode`/`Importcode` (OptionenFarben, p20) and
    # `TypSchNr` (PneuDimTS, p21) respectively. The caller
    # (`catalogue_sync`) holds all three on the variant it is syncing.
    def fetch_options(self, fz_key: str, *, model_year: int) -> list[VariantOptionData]: ...
    def fetch_colours(self, *, werkscode: str) -> list[VariantColourData]: ...
    def fetch_tyre_specs(self, *, type_approval_number: str) -> list[VariantTyreSpecData]: ...
    def fetch_images(self, fz_key: str) -> list[VariantImageData]: ...

    # KAN-211 (ADR-123) — the details loaded once per Betrieb when a car is
    # opened. Colours come with the variant on the identAPI (one request for
    # both); the energy label is its own endpoint, keyed by Typenschein.
    def fetch_variant_with_colours(self, fz_key: str) -> VariantColoursData: ...
    def fetch_energy_label(self, *, type_approval_number: str) -> list[EnergyLabelData]: ...

    def fetch_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, valuation_date: dt.date, mileage: int
    ) -> ValuationResult: ...

    def fetch_forecast(self, *, fz_key: str, model_year: int) -> ForecastResult: ...

    def fetch_vehicle_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, mileage_km: int, option_keys: list[int]
    ) -> VehicleValuationData:
        """KAN-552: the identAPI's current value of one car — one request."""
        ...

    def fetch_residual_table(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, mileage_km: int, option_keys: list[int]
    ) -> ResidualTableData:
        """KAN-552: the predefined residual-value table — one request, never a
        loop over durations or km bands."""
        ...

    # -- fourteen new Datennamen (Configurator C-0 / KAN-38 PR 2) ---------

    def list_vehicle_kinds(self) -> list[VehicleKindData]: ...

    def list_brands(self, *, fz_art: str) -> list[BrandData]: ...

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
    ) -> list[ModelGroupData]: ...

    def list_model_groups_short(
        self,
        *,
        fz_art: str,
        marken_nr: str | None = None,
        marke: str | None = None,
        nur_neue: bool = False,
        page: int | None = None,
        per_page: int | None = None,
    ) -> list[ModelGroupShortData]: ...

    def search_vehicles(self, criteria: dict[str, str | int | Sequence[str | int]]) -> list[VariantMasterData]:
        """`Fahrzeuge` widened to its full search parameter set (p9) —
        `TypSchNr`/`Werkscode`/`ModGrpKey`/the facet fields — as a
        parameter variant of the existing single-`FzKey` call, not a new
        Datenname. Deliberately a raw criteria dict rather than ~25 typed
        kwargs: no caller exists yet (that's C-D), and a passthrough dict
        lets it ask for exactly what it needs without this Protocol
        guessing its shape ahead of time.
        """
        ...

    def fetch_vehicle_prices(self, fz_key: str, *, year: int | None = None) -> list[VehiclePriceData]: ...

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
        """`FzgWerteGruppiert` (p12) — a sync-side completeness check
        only (KAN-38's own ruling): compares the provider's own coded
        values for one dimension against the mirror's. **Never a
        browse-facet source** — it needs a brand chosen first and
        answers one dimension per call, both disqualifying for FR-C-01.
        """
        ...

    def fetch_type_approvals(self, fz_key: str) -> list[str]: ...

    def lookup_plate(self, plate: str, *, fz_art: str | None = None) -> list[PlateInfoData]:
        """`KontrollschildInfo` (p14). Returns a **list** — a Wechselschild
        or a plate shared across FzArt genuinely returns more than one
        row; the caller (C-D's FR-C-02 waterfall) shows a picker on >1,
        never guesses.
        """
        ...

    def find_best_match(
        self,
        *,
        typ_sch_nr: str,
        neupreis: int,
        modell_bez: str | None = None,
        eurotax_code: str | None = None,
        getriebe: str | None = None,
        tueren: int | None = None,
    ) -> BestMatchResult: ...

    def fetch_type_approval_data(
        self, typ_sch_nr: str, *, getriebe: str | None = None, gaenge: int | None = None
    ) -> TypeApprovalDataResult: ...

    def fetch_option_package_contents(self, opt_key: int) -> list[OptionPackageContentData]: ...

    def fetch_option_exclusions(self, fz_key: str, *, year: int, opt_key: int) -> list[int]: ...

    def fetch_option_conditions(self, fz_key: str, *, year: int, opt_key: int) -> list[OptionConditionData]: ...

    def fetch_codes(
        self, *, code_groups: list[str] | None = None, active_only: bool = False
    ) -> list[CodeMapEntryData]: ...

    # -- the identAPI's identification and browse operations (KAN-547, KAN-548) --
    # Each is one provider request. None of them walks pages: a search
    # returns the one page it was asked for (ADR-112 (4)).

    def identify_by_vrn(self, stammnummer: str) -> list[BuiltVehicleData]:
        """`GET /vehiclebuild/vrn/{stammnummer}?needAutoiKeyIfExists` — the
        registered vehicle(s) with the catalogue variant each resolves to."""
        ...

    def identify_by_plate(self, plate: str) -> list[BuiltVehicleData]:
        """`GET /vehiclebuild/licenseplate/{plate}?needAutoiKeyIfExists` —
        several records for a Wechselschild; the caller shows a picker."""
        ...

    def identify_by_vin(self, vin: str) -> list[BuiltVehicleData]:
        """`GET /vehiclebuild/vin/{vin}?needAutoiKeyIfExists` (KAN-549) —
        behind the licence option `vin`; an empty list when the provider
        does not know the VIN."""
        ...

    def find_variants_by_type_approval(self, type_approval_number: str) -> list[VariantMasterData]:
        """`GET /vehiclecatalog/typeapproval/{nr}` — the first page (10) of
        catalogue variants homologated under the Typenschein."""
        ...

    def find_variants_by_werkscode(self, werkscode: str) -> list[VariantMasterData]:
        """`GET /vehiclecatalog/factorycode/{code}` — the first page (10)."""
        ...

    def fetch_variant(self, fz_key: str) -> VariantMasterData | None:
        """`GET /vehiclecatalog/autoi/{fzKey}` — one variant with its full
        spec block, or `None` when the key names nothing."""
        ...

    def fetch_captions(self, lang: str) -> list[CaptionData]:
        """`GET /caption/code?lang=` — every caption group in one language."""
        ...

    def list_catalogue_brands(self, *, vehicle_kind_code: str) -> list[CatalogueBrandData]:
        """`GET /vehiclecatalog/brand/{vehicleType}` for the raw kind code
        (`01` → passenger, `02` → utility, `03` → moto)."""
        ...

    def list_catalogue_model_groups(
        self, *, vehicle_kind_code: str, brand: str, in_production: bool | None = None
    ) -> list[CatalogueModelGroupData]:
        """`GET /vehiclecatalog/modelgroup/{vehicleType}/{brand}`."""
        ...

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
        """One page of `GET /vehiclecatalog…` with `includeCount`. Scoped to
        a brand or a model group at least — an unscoped query is refused
        before anything is sent (the anti-scraping guard, ADR-112 (4))."""
        ...

    def facet_values(
        self, *, field: str, vehicle_kind_code: str, brand: str, model_group_key: str | None = None
    ) -> list[str]:
        """`fields=<field> distinct` on the same scope — the values that
        exist for the brand or model group in view."""
        ...
