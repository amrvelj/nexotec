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


@dataclass(frozen=True)
class VariantColourData:
    colour_code: str
    description: str
    colour_type: str  # "exterior" | "interior"
    # KAN-43 (C-E) — FR-C-07: "the surcharge is a price line in build
    # mode." `Preis` is on the raw `OptionenFarben` response but was
    # parsed by no one until now.
    price: Decimal | None = None


@dataclass(frozen=True)
class VariantTyreSpecData:
    axle: str  # "front" | "rear" | "both" | "variant"
    size: str
    load_index: str | None
    speed_rating: str | None
    # KAN-43 (C-E) — FR-C-08: "the BemDe remark ... is displayed with the
    # dimension, because a dimension valid only with alloy wheels and
    # shown without that caveat is how the wrong tyre gets ordered." Also
    # FR-C-08's own "PneuTyp (summer/winter)". Both parsed by no one until
    # now — `remark` and `season` were entirely dropped from the pipeline.
    remark: str | None = None
    season: str | None = None  # "summer" | "winter"


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
    update date. PR-4's sync-age alarm (A-12) compares `update_date`
    against wall-clock time; it never derives staleness from whether the
    delta job itself reported success.
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


class ProviderAdapter(Protocol):
    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData: ...

    def list_changed_keys(self, *, since: dt.date) -> list[str]:
        """`FzKeyChanged`. Callers must enforce the 3-month `ChangedSince`
        hard limit themselves (PR-4) — the adapter's job is only to speak
        the protocol, not to police the caller's own request.
        """
        ...

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

    def fetch_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, valuation_date: dt.date, mileage: int
    ) -> ValuationResult: ...

    def fetch_forecast(self, *, fz_key: str, model_year: int) -> ForecastResult: ...

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
