"""Identify a vehicle from one input — the FR-C-02 waterfall (C-D / KAN-42;
live on the identAPI since KAN-547, ADR-111, ADR-114).

The advisor types one thing; `classify_identifier` decides from the string
whether it is a VIN, a Kontrollschild, a Stammnummer, a Typenschein or a
Werkscode (the FR-V-06 one-input principle). Each kind then runs its rung
of the waterfall, in the order the counter uses it:

1. **VIN** — our own `vehicle-mdm` first (`matching.match_vehicle`, reused,
   never paralleled). A hit is decisive and offers this tenant's newest
   configuration of that car for reuse. A miss falls through: the `vin`
   licence option is not in the test licence (ADR-113, §8) and the rung
   itself is KAN-549; the VIN is kept as observed data.
2. **Kontrollschild** — the provider first (ADR-114: the counter wants the
   current registration, never vehicle-mdm's plate register), behind the
   per-tenant plate-lookup cache (`plate_lookup_cache`, 30 days): the
   identAPI's Vehicle Build answer names the registered vehicle(s) **and**
   the catalogue variant each resolves to, so one registered car is one
   candidate without a second call. Several records are **for the picker,
   never chosen here** — distinct Stammnummern are a legitimate
   Wechselschild (an informational notice, ADR-114); one Stammnummer under
   two Typenscheine is a genuine conflict and a
   `vehicle.plate_lookup.conflicted` data-quality event (ADR-039).
3. **Stammnummer** — `vehicle-mdm`, then the identAPI's Vehicle Build by
   `vrn` (live — «auto-i-dat accepts no Stammnummer search» was a SOAP-era
   reading, withdrawn by ADR-111), then the plate-lookup cache as the
   fallback when the provider found nothing or could not be asked.
4. **Typenschein** — the identAPI's Vehicle Catalog by `typeapproval` (one
   provider page of 10), merged with the variants already stored for the
   number (`find_model_variants_by_type_approval`). 1..n variants; more
   than one is always a picker.
5. **Best match** (`propose_best_match`) — Vehicle Match by Typenschein plus
   a Neupreis. Its answer is a **proposal**: this module never creates or
   changes a configuration from it. The advisor confirms in the overlay,
   and only then is a configuration created with
   `catalogueMatchStatus = best_match_confirmed` (match code 2) or
   `matched` (match code 1).
6. **Werkscode** — the identAPI's Vehicle Catalog by `factorycode`, merged
   with the stored variants carrying the code.

Every variant a lookup returns is stored through the catalogue's own
`upsert_model_variant` (master data only, ADR-075; fill-if-NULL on a
revisit, KAN-83): the stored rows are a cache of what was seen, never the
source. A provider's refusal of the licence option reads as a
`provider_not_entitled` note, a failure or timeout as `provider_unavailable`
(the plate rung keeps its KAN-42 note names) — hints on screen, never
errors; the rung then falls through to what we hold ourselves.

Nothing in this module writes `vehicle-mdm` (ADR-070).
"""

import dataclasses
import datetime as dt
import enum
import logging
import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.integration.public import (
    BuiltVehicleData,
    PlateInfoData,
    ProviderGatewayError,
    ProviderNotEntitledError,
    VariantMasterData,
    call_capability,
    get_entitlement,
    vin_decode_granted,
)
from app.vehicle.models.catalogue import ModelVariant, VariantPrice
from app.vehicle.models.configuration import ConfigurationMatchMethod, VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services import plate_lookup_cache
from app.vehicle.services.catalogue import find_model_variants_by_type_approval
from app.vehicle.services.catalogue_sync import (
    find_enabled_vehicle_data_connection,
    store_seen_variants,
    upsert_model_variant,
)
from app.vehicle.services.matching import match_vehicle

logger = logging.getLogger(__name__)

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
    error. `VIN_DECODE_NOT_ENTITLED` is deliberately silent on screen: a
    dealer without the DAT sub-account sees what they saw before it existed."""

    VIN_DECODE_NOT_ENTITLED = "vin_decode_not_entitled"
    VIN_DECODE_UNAVAILABLE = "vin_decode_unavailable"
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


def _candidate(db: Session, variant: ModelVariant, *, first_registration_date: dt.date | None) -> VariantCandidate:
    new_price_for_year = None
    if first_registration_date is not None:
        new_price_for_year = db.scalar(
            select(VariantPrice.new_price).where(
                VariantPrice.model_variant_id == variant.id,
                VariantPrice.model_year == first_registration_date.year,
            )
        )
    return VariantCandidate(
        catalogue_variant_id=variant.id,
        brand_display_name=variant.brand_display_name,
        model_group_name=variant.model_group_name,
        variant_name=variant.name,
        trim_name=variant.trim_name,
        model_year_from=variant.model_year_from,
        model_year_to=variant.model_year_to,
        ps=variant.ps,
        kw=variant.kw,
        base_price=variant.base_price,
        base_price_year=variant.base_price_year,
        new_price_for_year=new_price_for_year,
    )


def _plate_record(record: PlateInfoData) -> PlateRecord:
    return PlateRecord(
        vehicle_kind_code=record.vehicle_kind_code,
        brand_name=record.brand_name,
        model_description=record.model_description,
        production_from=record.production_from,
        production_to=record.production_to,
        type_approval_number=record.type_approval_number,
        first_registration_date=record.first_registration_date,
        stammnummer=record.stammnummer,
    )


def _reusable_configuration_id(db: Session, *, tenant_id: uuid.UUID, vehicle_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(
        select(VehicleConfiguration.id)
        .where(VehicleConfiguration.tenant_id == tenant_id, VehicleConfiguration.vehicle_id == vehicle_id)
        .order_by(VehicleConfiguration.created_at.desc(), VehicleConfiguration.id.desc())
        .limit(1)
    )


def _existing_vehicle_result(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    vehicle: VehicleMdm,
    kind: IdentifierKind,
    match_method: ConfigurationMatchMethod,
    observed: ObservedIdentity,
) -> IdentificationResult:
    variants = []
    if vehicle.catalogue_variant is not None:
        variants = [_candidate(db, vehicle.catalogue_variant, first_registration_date=vehicle.first_registration_date)]
    return IdentificationResult(
        kind=kind,
        match_method=match_method,
        outcome=IdentificationOutcome.EXISTING_VEHICLE,
        observed=dataclasses.replace(
            observed,
            vin=vehicle.vin,
            stammnummer=vehicle.stammnummer or observed.stammnummer,
            type_approval_number=vehicle.type_approval_number or observed.type_approval_number,
            first_registration_date=vehicle.first_registration_date or observed.first_registration_date,
        ),
        existing_vehicle=ExistingVehicle(
            vehicle_id=vehicle.id,
            vehicle_number=vehicle.vehicle_number,
            vin=vehicle.vin,
            catalogue_variant_id=vehicle.catalogue_variant_id,
            reusable_configuration_id=_reusable_configuration_id(db, tenant_id=tenant_id, vehicle_id=vehicle.id),
        ),
        variants=variants,
    )


@dataclasses.dataclass(frozen=True)
class _ProviderAnswer:
    """One rung's provider call: `value` when the call was made and answered,
    else `None` with the note that says why (no connection, not entitled,
    unavailable)."""

    value: Any
    provider_code: str | None
    notes: list[IdentificationNote]

    @property
    def answered(self) -> bool:
        return self.value is not None


def _ask_provider(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    label: str,
    purpose: str,
    call,
    not_entitled_note: IdentificationNote = IdentificationNote.PROVIDER_NOT_ENTITLED,
    failed_note: IdentificationNote = IdentificationNote.PROVIDER_UNAVAILABLE,
) -> _ProviderAnswer:
    """One identAPI operation on the tenant's connection. A rung that cannot
    be asked (no connection, the option not granted) or that fails (a
    transport error, a timeout, a refused call) falls through with a note,
    never an error — the counter keeps working on what we hold."""

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return _ProviderAnswer(None, None, [IdentificationNote.NO_PROVIDER_CONNECTION])
    connection, provider_code = found
    entitlement = get_entitlement(db, connection_id=connection.id, capability_code=_IDENT_ENTITLEMENT)
    if entitlement is not None and not entitlement.granted:
        return _ProviderAnswer(None, provider_code, [not_entitled_note])
    try:
        with call_capability(db, connection=connection, capability=label, actor_id=actor_id, purpose=purpose) as adapter:
            value = call(adapter)
    except ProviderNotEntitledError:
        return _ProviderAnswer(None, provider_code, [not_entitled_note])
    except (ProviderGatewayError, KeyError):
        logger.warning("%s lookup failed", label, exc_info=True)
        return _ProviderAnswer(None, provider_code, [failed_note])
    return _ProviderAnswer(value, provider_code, [])


def _store_variants(db: Session, *, provider_code: str, masters: list[VariantMasterData]) -> list[ModelVariant]:
    """Every variant a rung returned, stored as master data (ADR-075) — the
    catalogue's own `store_seen_variants`, shared with the live browse."""

    return store_seen_variants(db, provider_code=provider_code, masters=masters)


def _merge_candidates(
    db: Session, *, live: list[ModelVariant], stored: list[ModelVariant], first_registration_date: dt.date | None
) -> list[VariantCandidate]:
    seen: set[uuid.UUID] = set()
    candidates: list[VariantCandidate] = []
    for variant in [*live, *stored]:
        if variant.id in seen:
            continue
        seen.add(variant.id)
        candidates.append(_candidate(db, variant, first_registration_date=first_registration_date))
    return candidates


def _typenschein_result(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    kind: IdentifierKind,
    match_method: ConfigurationMatchMethod,
    type_approval_number: str,
    observed: ObservedIdentity,
    notes: list[IdentificationNote],
    prefer_stored: bool = False,
) -> IdentificationResult:
    """Step 4: the live Typenschein lookup (one provider page), merged with
    what is stored for the number. 1..n variants; several are a picker.
    `prefer_stored` (a plate-cache hit): the stored variants answer without
    a call, and the provider is asked only when none are stored — a repeated
    plate within 30 days makes no call (KAN-42 exit criterion 5)."""

    stored = find_model_variants_by_type_approval(db, type_approval_number)
    live: list[ModelVariant] = []
    provider_notes: list[IdentificationNote] = []
    if not (prefer_stored and stored):
        answer = _ask_provider(
            db, tenant_id=tenant_id, actor_id=actor_id, label=_TYPENSCHEIN_CALL_LABEL, purpose="identify_typenschein",
            call=lambda adapter: adapter.find_variants_by_type_approval(type_approval_number),
        )
        provider_notes = answer.notes
        if answer.answered and answer.provider_code is not None:
            live = _store_variants(db, provider_code=answer.provider_code, masters=list(answer.value))
            if live:
                stored = find_model_variants_by_type_approval(db, type_approval_number)
    variants = _merge_candidates(db, live=live, stored=stored, first_registration_date=observed.first_registration_date)
    return IdentificationResult(
        kind=kind,
        match_method=match_method,
        outcome=IdentificationOutcome.VARIANTS if variants else IdentificationOutcome.NONE,
        observed=dataclasses.replace(observed, type_approval_number=type_approval_number),
        variants=variants,
        best_match_available=len(variants) != 1,
        notes=[*notes, *provider_notes],
    )


def _identify_vin(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, vin: str) -> IdentificationResult:
    observed = ObservedIdentity(vin=vin)
    found = match_vehicle(db, vin=vin)
    if found.vehicle is not None:
        return _existing_vehicle_result(
            db, tenant_id=tenant_id, vehicle=found.vehicle, kind=IdentifierKind.VIN,
            match_method=ConfigurationMatchMethod.VIN, observed=observed,
        )

    notes: list[IdentificationNote] = []
    if not vin_decode_granted(db, tenant_id=tenant_id):
        notes.append(IdentificationNote.VIN_DECODE_NOT_ENTITLED)
    else:
        notes.append(_attempt_vin_decode(db, tenant_id=tenant_id))
    return IdentificationResult(
        kind=IdentifierKind.VIN,
        match_method=ConfigurationMatchMethod.VIN,
        outcome=IdentificationOutcome.NONE,
        observed=observed,
        notes=notes,
    )


def _attempt_vin_decode(db: Session, *, tenant_id: uuid.UUID) -> IdentificationNote:
    """The entitled provider decode — KAN-549 builds it on `GET /vinrequest`
    behind the `vin` option, which the test licence does not grant (§8,
    ADR-113). Until then an entitled tenant falls through like an unentitled
    one, and nothing is sent."""

    if find_enabled_vehicle_data_connection(db, tenant_id=tenant_id) is None:
        return IdentificationNote.NO_PROVIDER_CONNECTION
    return IdentificationNote.VIN_DECODE_UNAVAILABLE


# -- registered vehicles (plate, Stammnummer) ----------------------------------------


def _cacheable(vehicle: BuiltVehicleData) -> PlateInfoData | None:
    """What the plate-lookup cache stores of a Vehicle Build record: plate →
    Typenschein + Stammnummer (+ first registration). A record the account
    may not see whole (the spec: `vrn` "depends on user group definition")
    cannot be cached and is left out."""

    if vehicle.type_approval_number is None or vehicle.stammnummer is None:
        return None
    return PlateInfoData(
        vehicle_kind_code=vehicle.vehicle_kind_code,
        brand_name=vehicle.brand_name,
        model_description=vehicle.model_description,
        production_from=vehicle.production_from,
        production_to=vehicle.production_to,
        type_approval_number=vehicle.type_approval_number,
        first_registration_date=vehicle.first_registration_date,
        stammnummer=vehicle.stammnummer,
    )


def _is_conflict(records: list[PlateRecord]) -> bool:
    """One Stammnummer reported with two different Typenscheine. Distinct
    Stammnummern are distinct cars legitimately sharing a plate; a record
    without a Stammnummer can contradict nothing."""

    by_stammnummer: dict[str, set[str]] = {}
    for record in records:
        if record.stammnummer is not None:
            by_stammnummer.setdefault(record.stammnummer, set()).add(record.type_approval_number)
    return any(len(types) > 1 for types in by_stammnummer.values())


def _publish_plate_conflict(db: Session, *, tenant_id: uuid.UUID, plate: str, records: list[PlateRecord]) -> None:
    publish(
        db,
        OutboxEvent(
            event_type="vehicle.plate_lookup.conflicted",
            tenant_id=tenant_id,
            producer="vehicle",
            aggregate_type="plate_lookup",
            aggregate_id=uuid.uuid5(uuid.NAMESPACE_URL, f"plate-lookup:{tenant_id}:{plate}"),
            payload={
                "plate": plate,
                "records": [
                    {"stammnummer": r.stammnummer, "typeApprovalNumber": r.type_approval_number} for r in records
                ],
            },
        ),
    )
    db.commit()


def _picker_result(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    kind: IdentifierKind,
    match_method: ConfigurationMatchMethod,
    observed: ObservedIdentity,
    rows: list[PlateRecord],
    notes: list[IdentificationNote],
    conflict_plate: str | None,
) -> IdentificationResult:
    """Several registered cars are the picker, never a choice: a Wechselschild
    is legitimate, a conflict is said and reported once per provider answer
    (never again from the cache)."""

    conflict = _is_conflict(rows)
    if conflict and conflict_plate is not None and IdentificationNote.PLATE_CACHE_HIT not in notes:
        _publish_plate_conflict(db, tenant_id=tenant_id, plate=conflict_plate, records=rows)
    return IdentificationResult(
        kind=kind,
        match_method=match_method,
        outcome=IdentificationOutcome.PLATE_RECORDS,
        observed=observed,
        plate_records=rows,
        plate_records_interchangeable=not conflict,
        plate_records_conflict=conflict,
        notes=notes,
    )


def _records_result(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    kind: IdentifierKind,
    match_method: ConfigurationMatchMethod,
    observed: ObservedIdentity,
    records: list[PlateInfoData],
    notes: list[IdentificationNote],
    conflict_plate: str | None,
) -> IdentificationResult:
    """A cached answer: several records are the picker; one record continues
    at its Typenschein from the stored variants (no call). The cache keeps the
    Typenschein, not the FzKey the live answer named — where several stored
    variants share the Typenschein the advisor chooses (best match offered)
    rather than the cache guessing which one the provider meant."""

    if len(records) > 1:
        return _picker_result(
            db, tenant_id=tenant_id, kind=kind, match_method=match_method, observed=observed,
            rows=[_plate_record(r) for r in records], notes=notes, conflict_plate=conflict_plate,
        )
    (record,) = records
    return _typenschein_result(
        db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=match_method,
        type_approval_number=record.type_approval_number,
        observed=dataclasses.replace(
            observed, stammnummer=record.stammnummer, first_registration_date=record.first_registration_date
        ),
        notes=notes,
        prefer_stored=IdentificationNote.PLATE_CACHE_HIT in notes,
    )


def _picker_row(vehicle: BuiltVehicleData) -> PlateRecord | None:
    """A registered car the picker can offer: it needs a Typenschein to
    continue at; the Stammnummer may be withheld from this account."""

    if vehicle.type_approval_number is None:
        return None
    return PlateRecord(
        vehicle_kind_code=vehicle.vehicle_kind_code,
        brand_name=vehicle.brand_name,
        model_description=vehicle.model_description,
        production_from=vehicle.production_from,
        production_to=vehicle.production_to,
        type_approval_number=vehicle.type_approval_number,
        first_registration_date=vehicle.first_registration_date,
        stammnummer=vehicle.stammnummer,
    )


def _cache_plate_answer(
    db: Session, *, tenant_id: uuid.UUID, plate: str | None, vehicles: list[BuiltVehicleData]
) -> None:
    """A plate answer fills the 30-day cache — whole or not at all: a record
    the account may not see whole cannot be stored (`_cacheable`), and an
    answer stored in part would later read as the complete truth about the
    plate (one car where there were two). A Stammnummer answer never fills
    it: it proves nothing about what else is registered on that plate, and
    would store a plate the advisor never typed (R-2)."""

    if plate is None:
        return
    records = [_cacheable(v) for v in vehicles]
    if not records or any(r is None for r in records):
        logger.info("plate %s: answer not cached — %d of %d records incomplete", plate, sum(r is None for r in records), len(records))
        return
    plate_lookup_cache.store_records_for_plate(db, tenant_id=tenant_id, plate=plate, records=[r for r in records if r is not None])
    db.commit()


def _built_vehicles_result(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    kind: IdentifierKind,
    match_method: ConfigurationMatchMethod,
    observed: ObservedIdentity,
    vehicles: list[BuiltVehicleData],
    provider_code: str,
    plate: str | None,
) -> IdentificationResult:
    """A Vehicle Build answer: the variants it carries are stored; one
    registered car with its variant is one candidate (no second call); one
    without goes on at its Typenschein; several are the picker — every car
    the answer names a Typenschein for, whether or not its Stammnummer is
    shown, and never one chosen for the advisor. `plate` is the plate the
    advisor typed (a plate lookup), None otherwise."""

    masters = [v.variant for v in vehicles if v.variant is not None]
    variants = _store_variants(db, provider_code=provider_code, masters=masters)  # in `masters` order
    by_fz_key = {master.fz_key: variant for master, variant in zip(masters, variants, strict=True)}
    _cache_plate_answer(db, tenant_id=tenant_id, plate=plate, vehicles=vehicles)

    if len(vehicles) == 1:
        (vehicle,) = vehicles
        # The registered car's own identity, the VIN included: it is the
        # same fact the VIN rung would have started from, so a configuration
        # taken from a plate or Stammnummer carries it.
        observed = dataclasses.replace(
            observed,
            stammnummer=vehicle.stammnummer or observed.stammnummer,
            type_approval_number=vehicle.type_approval_number or observed.type_approval_number,
            first_registration_date=vehicle.first_registration_date or observed.first_registration_date,
            vin=observed.vin or vehicle.vin,
        )
        if vehicle.variant is not None and vehicle.variant.fz_key in by_fz_key:
            return IdentificationResult(
                kind=kind,
                match_method=match_method,
                outcome=IdentificationOutcome.VARIANTS,
                observed=observed,
                variants=[
                    _candidate(db, by_fz_key[vehicle.variant.fz_key], first_registration_date=observed.first_registration_date)
                ],
                best_match_available=False,
            )
        if vehicle.type_approval_number is not None:
            return _typenschein_result(
                db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=match_method,
                type_approval_number=vehicle.type_approval_number, observed=observed, notes=[],
            )
        return IdentificationResult(kind=kind, match_method=match_method, outcome=IdentificationOutcome.NONE, observed=observed)

    rows = [r for r in (_picker_row(v) for v in vehicles) if r is not None]
    if len(rows) < len(vehicles):
        logger.info("%d of %d registered cars carry no Typenschein and cannot be offered", len(vehicles) - len(rows), len(vehicles))
    if not rows:
        return IdentificationResult(kind=kind, match_method=match_method, outcome=IdentificationOutcome.NONE, observed=observed)
    return _picker_result(
        db, tenant_id=tenant_id, kind=kind, match_method=match_method, observed=observed,
        rows=rows, notes=[], conflict_plate=plate,
    )


def _identify_plate(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, plate: str
) -> IdentificationResult:
    """Step 2 (ADR-114): the provider first, behind the 30-day cache; a cached
    answer makes no call and continues at its Typenschein (live)."""

    kind, method = IdentifierKind.KONTROLLSCHILD, ConfigurationMatchMethod.KONTROLLSCHILD
    observed = ObservedIdentity(licence_plate=plate)
    cached = plate_lookup_cache.cached_records_for_plate(db, tenant_id=tenant_id, plate=plate)
    if cached is not None:
        return _records_result(
            db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=method, observed=observed,
            records=cached, notes=[IdentificationNote.PLATE_CACHE_HIT], conflict_plate=plate,
        )

    answer = _ask_provider(
        db, tenant_id=tenant_id, actor_id=actor_id, label=_PLATE_CALL_LABEL, purpose="identify_plate",
        call=lambda adapter: adapter.identify_by_plate(plate),
        not_entitled_note=IdentificationNote.PLATE_LOOKUP_NOT_ENTITLED, failed_note=IdentificationNote.PLATE_LOOKUP_FAILED,
    )
    vehicles: list[BuiltVehicleData] = list(answer.value) if answer.answered else []
    if not vehicles:
        return IdentificationResult(
            kind=kind, match_method=method, outcome=IdentificationOutcome.NONE, observed=observed,
            notes=answer.notes or [IdentificationNote.PLATE_NOT_FOUND],
        )
    assert answer.provider_code is not None
    return _built_vehicles_result(
        db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=method, observed=observed,
        vehicles=vehicles, provider_code=answer.provider_code, plate=plate,
    )


def _identify_stammnummer(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, stammnummer: str
) -> IdentificationResult:
    """Step 3: vehicle-mdm, then the live `vrn` lookup, then the plate-lookup
    cache as the fallback."""

    kind, method = IdentifierKind.STAMMNUMMER, ConfigurationMatchMethod.STAMMNUMMER
    observed = ObservedIdentity(stammnummer=stammnummer)
    found = match_vehicle(db, stammnummer=stammnummer)
    if found.vehicle is not None:
        return _existing_vehicle_result(
            db, tenant_id=tenant_id, vehicle=found.vehicle, kind=kind, match_method=method, observed=observed,
        )

    answer = _ask_provider(
        db, tenant_id=tenant_id, actor_id=actor_id, label=_STAMMNUMMER_CALL_LABEL, purpose="identify_stammnummer",
        call=lambda adapter: adapter.identify_by_vrn(stammnummer),
    )
    vehicles: list[BuiltVehicleData] = list(answer.value) if answer.answered else []
    if vehicles:
        assert answer.provider_code is not None
        # `plate=None`: a `vrn` answer proves nothing about what else is
        # registered on the car's plate, so it never fills the plate cache.
        return _built_vehicles_result(
            db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=method, observed=observed,
            vehicles=vehicles, provider_code=answer.provider_code, plate=None,
        )

    cached = plate_lookup_cache.cached_records_for_stammnummer(db, tenant_id=tenant_id, stammnummer=stammnummer)
    if not cached:
        return IdentificationResult(
            kind=kind, match_method=method, outcome=IdentificationOutcome.NONE, observed=observed, notes=answer.notes
        )
    return _records_result(
        db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=method, observed=observed,
        records=cached, notes=[*answer.notes, IdentificationNote.PLATE_CACHE_HIT], conflict_plate=None,
    )


def _identify_werkscode(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, werkscode: str
) -> IdentificationResult:
    """Step 6: the live Werkscode lookup, merged with the stored variants
    carrying the code."""

    answer = _ask_provider(
        db, tenant_id=tenant_id, actor_id=actor_id, label=_WERKSCODE_CALL_LABEL, purpose="identify_werkscode",
        call=lambda adapter: adapter.find_variants_by_werkscode(werkscode),
    )
    live: list[ModelVariant] = []
    if answer.answered and answer.provider_code is not None:
        live = _store_variants(db, provider_code=answer.provider_code, masters=list(answer.value))
    stored = list(
        db.scalars(
            select(ModelVariant).where(ModelVariant.werkscode == werkscode).order_by(ModelVariant.name, ModelVariant.id)
        ).all()
    )
    variants = _merge_candidates(db, live=live, stored=stored, first_registration_date=None)
    return IdentificationResult(
        kind=IdentifierKind.WERKSCODE,
        match_method=ConfigurationMatchMethod.WERKSCODE,
        outcome=IdentificationOutcome.VARIANTS if variants else IdentificationOutcome.NONE,
        observed=ObservedIdentity(werkscode=werkscode),
        variants=variants,
        notes=answer.notes,
    )


def identify(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, query: str
) -> IdentificationResult:
    classified = classify_identifier(query)
    if classified is None:
        raise UnprocessableEntityError(
            "The input is not a VIN, a licence plate, a Stammnummer, a Typenschein or a Werkscode.",
            details={"reason": "unrecognised_identifier"},
        )
    kind, value = classified
    if kind is IdentifierKind.VIN:
        return _identify_vin(db, tenant_id=tenant_id, actor_id=actor_id, vin=value)
    if kind is IdentifierKind.KONTROLLSCHILD:
        return _identify_plate(db, tenant_id=tenant_id, actor_id=actor_id, plate=value)
    if kind is IdentifierKind.STAMMNUMMER:
        return _identify_stammnummer(db, tenant_id=tenant_id, actor_id=actor_id, stammnummer=value)
    if kind is IdentifierKind.TYPENSCHEIN:
        return _typenschein_result(
            db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=ConfigurationMatchMethod.TYPENSCHEIN,
            type_approval_number=value, observed=ObservedIdentity(), notes=[],
        )
    return _identify_werkscode(db, tenant_id=tenant_id, actor_id=actor_id, werkscode=value)


def propose_best_match(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    type_approval_number: str,
    new_price: Decimal,
    new_price_source: NewPriceSource,
    model_description: str | None = None,
    first_registration_date: dt.date | None = None,
) -> BestMatchProposal:
    """FR-C-02 step 5. Asks `FahrzeugeMatch` and returns a proposal for the
    advisor to confirm. It creates no configuration and changes none. The
    variant the provider names is mirrored if this catalogue does not hold
    it yet, and visited if it does (the same upsert the catalogue sync uses),
    so the confirmed configuration can link it."""

    type_approval_number = type_approval_number.strip().upper()
    if not _TYPENSCHEIN_RE.match(type_approval_number):
        raise UnprocessableEntityError(
            "A best match needs a Typenschein.", details={"reason": "invalid_type_approval_number"}
        )
    if new_price <= 0:
        raise UnprocessableEntityError("A best match needs a Neupreis.", details={"reason": "invalid_new_price"})

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        raise UnprocessableEntityError(
            "No vehicle-data provider is connected.", details={"reason": "no_provider_connection"}
        )
    connection, provider_code = found
    try:
        with call_capability(
            db, connection=connection, capability=_BEST_MATCH_CALL_LABEL, actor_id=actor_id, purpose="best_match"
        ) as adapter:
            result = adapter.find_best_match(
                typ_sch_nr=type_approval_number, neupreis=int(new_price), modell_bez=model_description
            )
    except (ProviderGatewayError, KeyError) as exc:
        raise UnprocessableEntityError(
            "The provider found no match for this Typenschein and Neupreis.", details={"reason": "no_best_match"}
        ) from exc

    # The catalogue's own upsert, also for a variant this catalogue holds: a
    # visit links its Typenscheine and fills only NULL fields (KAN-83), so a
    # variant mirrored before KAN-42 gets its links here (KAN-190) — no
    # backfill ever asks a provider for them (KAN-485).
    variant = upsert_model_variant(db, provider_code=provider_code, master=result.vehicle)
    db.commit()

    candidate = _candidate(db, variant, first_registration_date=first_registration_date)
    reference_price = candidate.new_price_for_year or candidate.base_price
    fields = [
        BestMatchField(
            field="typeApprovalNumber",
            entered=type_approval_number,
            matched=type_approval_number if type_approval_number in result.vehicle.type_approval_numbers else None,
            agrees=type_approval_number in result.vehicle.type_approval_numbers,
        ),
        BestMatchField(
            field="newPrice",
            entered=str(new_price),
            matched=str(reference_price) if reference_price is not None else None,
            agrees=reference_price is not None and Decimal(reference_price) == new_price,
        ),
    ]
    if model_description:
        fields.append(
            BestMatchField(
                field="modelDescription",
                entered=model_description,
                matched=variant.name,
                agrees=model_description.strip().lower() in variant.name.lower(),
            )
        )
    return BestMatchProposal(
        candidate=candidate,
        match_code=result.match_code,
        new_price=new_price,
        new_price_source=new_price_source,
        fields=fields,
    )
