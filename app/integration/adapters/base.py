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
