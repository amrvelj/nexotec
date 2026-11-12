"""The identification waterfall's vocabulary, split from `identification.py`
(KAN-670, a pure move): the identifier patterns and the call labels, the
kinds, outcomes and notes, the result and proposal shapes, and
`classify_identifier`, which decides from the string alone what was typed.
No database, no provider.
"""

import dataclasses
import datetime as dt
import enum
import re
import uuid
from decimal import Decimal

from app.vehicle.models.configuration import ConfigurationMatchMethod

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_STAMMNUMMER_RE = re.compile(r"^\d{9}$")
_TYPENSCHEIN_RE = re.compile(r"^\d[A-Z0-9]{5}$")
_PLATE_RE = re.compile(r"^([A-Z]{2})(\d{1,6})$")
_WERKSCODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9./-]{1,63}$")

# The 26 cantonal codes. A two-letter prefix that is not one of them is not
# a Swiss Kontrollschild, which keeps "AB1234" from costing a billed call.
_CANTONS = frozenset(
    {
        "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE",
        "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH",
    }
)

_VIN_CALL_LABEL = "vin"
_PLATE_CALL_LABEL = "kontrollschild"
_STAMMNUMMER_CALL_LABEL = "stammnummer"
_TYPENSCHEIN_CALL_LABEL = "typenschein"
_WERKSCODE_CALL_LABEL = "werkscode"
# Every identification rung is the identAPI's basis option (KAN-488): the
# entitlement row a refusal is read from is `ident_basis`.
_IDENT_ENTITLEMENT = "ident_basis"
_BEST_MATCH_CALL_LABEL = "vehicle_data"


class IdentifierKind(str, enum.Enum):
    VIN = "vin"
    KONTROLLSCHILD = "kontrollschild"
    STAMMNUMMER = "stammnummer"
    TYPENSCHEIN = "typenschein"
    WERKSCODE = "werkscode"


class IdentificationOutcome(str, enum.Enum):
    """What the overlay renders next. `variants` with exactly one entry is
    decisive; with more it is a picker. `plate_records` is always a picker."""

    EXISTING_VEHICLE = "existing_vehicle"
    VARIANTS = "variants"
    PLATE_RECORDS = "plate_records"
    NONE = "none"


class NewPriceSource(str, enum.Enum):
    """Where a best-match Neupreis came from (FR-C-02 step 5) — shown with
    the proposal, because a match run against the wrong year's price
    returns a plausible wrong car rather than an error."""

    CATALOGUE = "catalogue"
    DOCUMENT = "document"
    CUSTOMER = "customer"


class IdentificationNote(str, enum.Enum):
    """Why the waterfall did what it did — rendered as a hint, never as an
    error. `VIN_DECODE_NOT_ENTITLED` (KAN-549): the connection's `vin`
    licence option is not granted, so no VIN query was sent — the advisor
    is told to identify by plate, Stammnummer or Typenschein."""

    VIN_DECODE_NOT_ENTITLED = "vin_decode_not_entitled"
    NO_PROVIDER_CONNECTION = "no_provider_connection"
    PLATE_LOOKUP_NOT_ENTITLED = "plate_lookup_not_entitled"
    PLATE_LOOKUP_FAILED = "plate_lookup_failed"
    PLATE_CACHE_HIT = "plate_cache_hit"
    PLATE_NOT_FOUND = "plate_not_found"
    # KAN-547: the Stammnummer, Typenschein and Werkscode rungs are live; the
    # provider's refusal of the option, and a failure or timeout, are said.
    PROVIDER_NOT_ENTITLED = "provider_not_entitled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


@dataclasses.dataclass(frozen=True)
class VariantCandidate:
    catalogue_variant_id: uuid.UUID
    brand_display_name: str | None
    model_group_name: str | None
    variant_name: str
    trim_name: str | None
    model_year_from: int
    model_year_to: int | None
    ps: int | None
    kw: int | None
    base_price: Decimal | None
    base_price_year: int | None
    # The mirrored FahrzeugePreise row for the car's first-registration
    # year, when both are known — the first of the three Neupreis sources.
    new_price_for_year: Decimal | None


@dataclasses.dataclass(frozen=True)
class PlateRecord:
    vehicle_kind_code: str
    brand_name: str
    model_description: str
    production_from: int | None
    production_to: int | None
    type_approval_number: str
    first_registration_date: dt.date | None
    # None when the account may not see it (the identAPI's `vrn` "depends on
    # user group definition"): the car is still offered, by its Typenschein.
    stammnummer: str | None
    # KAN-592: the stored catalogue variant the provider named for this car,
    # as the one candidate picking the record offers — no second, billed
    # `vehiclecatalog` call at its Typenschein. None when the provider named
    # none, or the variant is no longer stored: the pick then continues at the
    # Typenschein as before.
    variant: "VariantCandidate | None" = None


@dataclasses.dataclass(frozen=True)
class ExistingVehicle:
    vehicle_id: uuid.UUID
    vehicle_number: str
    vin: str
    catalogue_variant_id: uuid.UUID | None
    # This tenant's newest configuration of the car, offered for reuse.
    reusable_configuration_id: uuid.UUID | None


@dataclasses.dataclass(frozen=True)
class ObservedIdentity:
    """What the input established about the car, carried into the
    configuration as observed data whatever the advisor picks next."""

    vin: str | None = None
    licence_plate: str | None = None
    stammnummer: str | None = None
    type_approval_number: str | None = None
    first_registration_date: dt.date | None = None
    werkscode: str | None = None


@dataclasses.dataclass(frozen=True)
class IdentificationResult:
    kind: IdentifierKind
    match_method: ConfigurationMatchMethod
    outcome: IdentificationOutcome
    observed: ObservedIdentity
    existing_vehicle: ExistingVehicle | None = None
    variants: list[VariantCandidate] = dataclasses.field(default_factory=list)
    plate_records: list[PlateRecord] = dataclasses.field(default_factory=list)
    # Several plate records describing distinct cars — a Wechselschild or a
    # plate shared by a car and a motorcycle. Legitimate, never an error.
    plate_records_interchangeable: bool = False
    plate_records_conflict: bool = False
    # A Typenschein is known and did not resolve to exactly one variant:
    # the advisor may ask FahrzeugeMatch with a Neupreis.
    best_match_available: bool = False
    notes: list[IdentificationNote] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class BestMatchField:
    field: str
    entered: str | None
    matched: str | None
    agrees: bool


@dataclasses.dataclass(frozen=True)
class BestMatchProposal:
    """Always requires a human confirmation — `match_code` 1 included
    (KAN-42 exit criterion 4). Nothing is written by producing one."""

    candidate: VariantCandidate
    match_code: int
    new_price: Decimal
    new_price_source: NewPriceSource
    fields: list[BestMatchField]
    requires_confirmation: bool = True


def normalise_plate(raw: str) -> str:
    return re.sub(r"[\s.\-]", "", raw.upper())


def classify_identifier(raw: str) -> tuple[IdentifierKind, str] | None:
    """Decides what the string is. Order matters only where two shapes
    could overlap; the patterns are chosen so they do not: a VIN is 17
    characters, a Stammnummer 9 digits, a Typenschein 6 characters starting
    with a digit, a plate a cantonal code plus digits."""

    compact = re.sub(r"[\s.]", "", raw.strip().upper())
    if not compact:
        return None
    if _VIN_RE.match(compact):
        return IdentifierKind.VIN, compact
    plate = normalise_plate(raw)
    plate_match = _PLATE_RE.match(plate)
    if plate_match and plate_match.group(1) in _CANTONS:
        return IdentifierKind.KONTROLLSCHILD, plate
    if _STAMMNUMMER_RE.match(compact):
        return IdentifierKind.STAMMNUMMER, compact
    if _TYPENSCHEIN_RE.match(compact) and re.search(r"[A-Z]", compact):
        return IdentifierKind.TYPENSCHEIN, compact
    if _WERKSCODE_RE.match(compact):
        return IdentifierKind.WERKSCODE, compact
    return None
