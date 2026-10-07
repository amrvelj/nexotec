"""Identify a vehicle from one input — the FR-C-02 waterfall (C-D / KAN-42).

The advisor types one thing; `classify_identifier` decides from the string
whether it is a VIN, a Kontrollschild, a Stammnummer, a Typenschein or a
Werkscode (the FR-V-06 one-input principle). Each kind then runs its rung
of the waterfall, in the order the counter uses it:

1. **VIN** — our own `vehicle-mdm` first (`matching.match_vehicle`, reused,
   never paralleled). A hit is decisive and offers this tenant's newest
   configuration of that car for reuse. A miss goes to the provider VIN
   decode when the tenant is entitled (`vin_decode`, KAN-36). Not entitled,
   or the decode is unavailable or misses → fall through: the VIN is kept
   as observed data and the advisor continues with a plate, a Typenschein
   or browse — exactly the pre-correction behaviour (exit criterion 6).
   **The decode call itself is not implemented**: the auto-i-dat VIN
   webservice specification does not exist in our document set (KAN-81),
   so `AutoIDatSoapAdapter.decode_vin` raises `NotImplementedError` and
   this module treats that as "unavailable", never as an error.
2. **Kontrollschild** — `KontrollschildInfo`, a live billed call, behind
   the plate-lookup cache (`plate_lookup_cache`, TTL stated there). One
   record continues at the Typenschein rung with that record's Typenschein.
   Several records are returned **for the picker, never chosen here** —
   several distinct Stammnummern are a legitimate Wechselschild (or one
   plate on a car and a motorcycle); one Stammnummer reported with two
   different Typenscheine is a genuine conflict and is raised as a
   `vehicle.plate_lookup.conflicted` data-quality event (ADR-039).
3. **Stammnummer** — our own data only: `vehicle-mdm`, then the
   plate-lookup cache. auto-i-dat accepts no Stammnummer search.
4. **Typenschein** — `find_model_variants_by_type_approval` over the
   catalogue mirror. 1..n variants; more than one is always a picker.
5. **Best match** (`propose_best_match`) — `FahrzeugeMatch` with a
   Typenschein plus a Neupreis. Its answer is a **proposal**: this module
   never creates or changes a configuration from it. The advisor confirms
   in the overlay, and only then is a configuration created with
   `catalogueMatchStatus = best_match_confirmed` (MatchCode 2) or
   `matched` (MatchCode 1).
6. **Werkscode** — the mirror's `werkscode` column.

The Typenschein and Werkscode rungs read the **mirror**, not the provider:
the mirror is synced from `Fahrzeuge`, and a lookup the mirror can answer
is not worth a billed round trip. Only the plate lookup and the best match
are live calls.

Nothing in this module writes `vehicle-mdm` (ADR-070).
"""

import dataclasses
import datetime as dt
import enum
import logging
import re
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.integration.public import (
    PlateInfoData,
    ProviderGatewayError,
    call_capability,
    get_entitlement,
    tenant_has_capability,
)
from app.vehicle.models.catalogue import ModelVariant, VariantPrice
from app.vehicle.models.configuration import ConfigurationMatchMethod, VehicleConfiguration
from app.vehicle.models.provider import ProviderEntityRef
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services import plate_lookup_cache
from app.vehicle.services.catalogue import find_model_variants_by_type_approval
from app.vehicle.services.catalogue_sync import find_enabled_vehicle_data_connection, upsert_model_variant
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

_ENTITY_TYPE_MODEL_VARIANT = "model_variant"
_PLATE_CALL_LABEL = "kontrollschild"
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


@dataclasses.dataclass(frozen=True)
class VariantCandidate:
    catalogue_variant_id: uuid.UUID
    brand_display_name: str | None
    model_group_name: str | None
    variant_name: str
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
    stammnummer: str


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


def _typenschein_result(
    db: Session,
    *,
    kind: IdentifierKind,
    match_method: ConfigurationMatchMethod,
    type_approval_number: str,
    observed: ObservedIdentity,
    notes: list[IdentificationNote],
) -> IdentificationResult:
    variants = [
        _candidate(db, v, first_registration_date=observed.first_registration_date)
        for v in find_model_variants_by_type_approval(db, type_approval_number)
    ]
    return IdentificationResult(
        kind=kind,
        match_method=match_method,
        outcome=IdentificationOutcome.VARIANTS if variants else IdentificationOutcome.NONE,
        observed=dataclasses.replace(observed, type_approval_number=type_approval_number),
        variants=variants,
        best_match_available=len(variants) != 1,
        notes=notes,
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
    if not tenant_has_capability(db, tenant_id=tenant_id, capability_code="vin_decode"):
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
    """The entitled provider decode. Every path out of here falls through.

    **Not called today.** The decode webservice has no specification
    (KAN-81) and `AutoIDatSoapAdapter.decode_vin` raises
    `NotImplementedError`. Calling it through `call_capability` would log an
    error and charge the connection's circuit breaker on every VIN an
    advisor types — and that breaker is per connection, so enough VINs
    would block the plate lookup too. So it is not made. When the
    specification arrives: implement the adapter, call it here through
    `call_capability`, and treat a resolved decode exactly like a
    `Fahrzeuge` resolution. The waterfall around it needs no change."""

    if find_enabled_vehicle_data_connection(db, tenant_id=tenant_id) is None:
        return IdentificationNote.NO_PROVIDER_CONNECTION
    return IdentificationNote.VIN_DECODE_UNAVAILABLE


def _lookup_plate(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, plate: str
) -> tuple[list[PlateInfoData], list[IdentificationNote]]:
    cached = plate_lookup_cache.cached_records_for_plate(db, tenant_id=tenant_id, plate=plate)
    if cached is not None:
        return cached, [IdentificationNote.PLATE_CACHE_HIT]

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return [], [IdentificationNote.NO_PROVIDER_CONNECTION]
    connection, _provider_code = found
    entitlement = get_entitlement(db, connection_id=connection.id, capability_code=_PLATE_CALL_LABEL)
    if entitlement is not None and not entitlement.granted:
        return [], [IdentificationNote.PLATE_LOOKUP_NOT_ENTITLED]

    try:
        with call_capability(
            db, connection=connection, capability=_PLATE_CALL_LABEL, actor_id=actor_id, purpose="identify_plate"
        ) as adapter:
            records = adapter.lookup_plate(plate)
    except (ProviderGatewayError, KeyError):
        logger.warning("Plate lookup failed", exc_info=True)
        return [], [IdentificationNote.PLATE_LOOKUP_FAILED]

    if records:
        plate_lookup_cache.store_records_for_plate(db, tenant_id=tenant_id, plate=plate, records=records)
        db.commit()
    return records, []


def _is_conflict(records: list[PlateInfoData]) -> bool:
    """One Stammnummer reported with two different Typenscheine. Distinct
    Stammnummern are distinct cars legitimately sharing a plate."""

    by_stammnummer: dict[str, set[str]] = {}
    for record in records:
        by_stammnummer.setdefault(record.stammnummer, set()).add(record.type_approval_number)
    return any(len(types) > 1 for types in by_stammnummer.values())


def _publish_plate_conflict(db: Session, *, tenant_id: uuid.UUID, plate: str, records: list[PlateInfoData]) -> None:
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


def _identify_plate(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, plate: str
) -> IdentificationResult:
    observed = ObservedIdentity(licence_plate=plate)
    records, notes = _lookup_plate(db, tenant_id=tenant_id, actor_id=actor_id, plate=plate)
    if not records:
        if not notes:
            notes = [IdentificationNote.PLATE_NOT_FOUND]
        return IdentificationResult(
            kind=IdentifierKind.KONTROLLSCHILD,
            match_method=ConfigurationMatchMethod.KONTROLLSCHILD,
            outcome=IdentificationOutcome.NONE,
            observed=observed,
            notes=notes,
        )

    if len(records) > 1:
        conflict = _is_conflict(records)
        # Raised once per provider answer, not again on every cache hit.
        if conflict and IdentificationNote.PLATE_CACHE_HIT not in notes:
            _publish_plate_conflict(db, tenant_id=tenant_id, plate=plate, records=records)
        return IdentificationResult(
            kind=IdentifierKind.KONTROLLSCHILD,
            match_method=ConfigurationMatchMethod.KONTROLLSCHILD,
            outcome=IdentificationOutcome.PLATE_RECORDS,
            observed=observed,
            plate_records=[_plate_record(r) for r in records],
            plate_records_interchangeable=not conflict,
            plate_records_conflict=conflict,
            notes=notes,
        )

    (record,) = records
    return _typenschein_result(
        db,
        kind=IdentifierKind.KONTROLLSCHILD,
        match_method=ConfigurationMatchMethod.KONTROLLSCHILD,
        type_approval_number=record.type_approval_number,
        observed=dataclasses.replace(
            observed, stammnummer=record.stammnummer, first_registration_date=record.first_registration_date
        ),
        notes=notes,
    )


def _identify_stammnummer(db: Session, *, tenant_id: uuid.UUID, stammnummer: str) -> IdentificationResult:
    observed = ObservedIdentity(stammnummer=stammnummer)
    found = match_vehicle(db, stammnummer=stammnummer)
    if found.vehicle is not None:
        return _existing_vehicle_result(
            db, tenant_id=tenant_id, vehicle=found.vehicle, kind=IdentifierKind.STAMMNUMMER,
            match_method=ConfigurationMatchMethod.STAMMNUMMER, observed=observed,
        )

    cached = plate_lookup_cache.cached_records_for_stammnummer(db, tenant_id=tenant_id, stammnummer=stammnummer)
    if not cached:
        return IdentificationResult(
            kind=IdentifierKind.STAMMNUMMER,
            match_method=ConfigurationMatchMethod.STAMMNUMMER,
            outcome=IdentificationOutcome.NONE,
            observed=observed,
        )
    if len(cached) > 1:
        # Several earlier answers name this Stammnummer: a picker, never a
        # choice (exit criterion 2). Two Typenscheine for one Stammnummer
        # is the same data-quality conflict as on the plate rung.
        conflict = _is_conflict(cached)
        return IdentificationResult(
            kind=IdentifierKind.STAMMNUMMER,
            match_method=ConfigurationMatchMethod.STAMMNUMMER,
            outcome=IdentificationOutcome.PLATE_RECORDS,
            observed=observed,
            plate_records=[_plate_record(r) for r in cached],
            plate_records_interchangeable=not conflict,
            plate_records_conflict=conflict,
            notes=[IdentificationNote.PLATE_CACHE_HIT],
        )
    (record,) = cached
    return _typenschein_result(
        db,
        kind=IdentifierKind.STAMMNUMMER,
        match_method=ConfigurationMatchMethod.STAMMNUMMER,
        type_approval_number=record.type_approval_number,
        observed=dataclasses.replace(observed, first_registration_date=record.first_registration_date),
        notes=[IdentificationNote.PLATE_CACHE_HIT],
    )


def _identify_werkscode(db: Session, *, werkscode: str) -> IdentificationResult:
    variants = list(
        db.scalars(
            select(ModelVariant).where(ModelVariant.werkscode == werkscode).order_by(ModelVariant.name, ModelVariant.id)
        ).all()
    )
    return IdentificationResult(
        kind=IdentifierKind.WERKSCODE,
        match_method=ConfigurationMatchMethod.WERKSCODE,
        outcome=IdentificationOutcome.VARIANTS if variants else IdentificationOutcome.NONE,
        observed=ObservedIdentity(werkscode=werkscode),
        variants=[_candidate(db, v, first_registration_date=None) for v in variants],
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
        return _identify_stammnummer(db, tenant_id=tenant_id, stammnummer=value)
    if kind is IdentifierKind.TYPENSCHEIN:
        return _typenschein_result(
            db, kind=kind, match_method=ConfigurationMatchMethod.TYPENSCHEIN, type_approval_number=value,
            observed=ObservedIdentity(), notes=[],
        )
    return _identify_werkscode(db, werkscode=value)


def _variant_for_fz_key(db: Session, *, provider_code: str, fz_key: str) -> ModelVariant | None:
    ref = db.scalar(
        select(ProviderEntityRef).where(
            ProviderEntityRef.entity_type == _ENTITY_TYPE_MODEL_VARIANT,
            ProviderEntityRef.provider == provider_code,
            ProviderEntityRef.provider_key == fz_key,
        )
    )
    return db.get(ModelVariant, ref.entity_id) if ref is not None else None


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
    it yet (the same upsert the catalogue sync uses), so the confirmed
    configuration can link it."""

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

    variant = _variant_for_fz_key(db, provider_code=provider_code, fz_key=result.vehicle.fz_key)
    if variant is None:
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
