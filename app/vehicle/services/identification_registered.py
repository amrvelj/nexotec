"""The registered-vehicle rungs of the identification waterfall, split from
`identification.py` (KAN-670, a pure move): Kontrollschild and Stammnummer —
the provider's Vehicle Build answer, the plate-lookup cache, the picker and
the `vehicle.plate_lookup.conflicted` event. `_built_vehicles_result` also
serves the VIN rung in `identification.py`.

Imports `identification_common.py` and `identification_types.py`. Nothing
here writes `vehicle-mdm` (ADR-070).
"""

import dataclasses
import logging
import uuid

from sqlalchemy.orm import Session

from app.core.outbox import OutboxEvent, publish
from app.integration.public import (
    BuiltVehicleData,
    PlateInfoData,
)
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.configuration import ConfigurationMatchMethod
from app.vehicle.services import plate_lookup_cache
from app.vehicle.services.identification_common import (
    _ask_provider,
    _candidate,
    _existing_vehicle_result,
    _named_candidate,
    _plate_record,
    _store_variants,
    _typenschein_result,
)
from app.vehicle.services.identification_types import (
    _PLATE_CALL_LABEL,
    _STAMMNUMMER_CALL_LABEL,
    IdentificationNote,
    IdentificationOutcome,
    IdentificationResult,
    IdentifierKind,
    ObservedIdentity,
    PlateRecord,
)
from app.vehicle.services.matching import match_vehicle
from app.vehicle.services.plate_lookup_cache import CachedPlateRecord

logger = logging.getLogger(__name__)


# -- registered vehicles (plate, Stammnummer) ----------------------------------------


def _cacheable(vehicle: BuiltVehicleData, *, model_variant_id: uuid.UUID | None) -> CachedPlateRecord | None:
    """What the plate-lookup cache stores of a Vehicle Build record: plate →
    Typenschein + Stammnummer (+ first registration), and the stored variant
    the record named (KAN-592). A record the account may not see whole (the
    spec: `vrn` "depends on user group definition") cannot be cached and is
    left out."""

    if vehicle.type_approval_number is None or vehicle.stammnummer is None:
        return None
    info = PlateInfoData(
        vehicle_kind_code=vehicle.vehicle_kind_code,
        brand_name=vehicle.brand_name,
        model_description=vehicle.model_description,
        production_from=vehicle.production_from,
        production_to=vehicle.production_to,
        type_approval_number=vehicle.type_approval_number,
        first_registration_date=vehicle.first_registration_date,
        stammnummer=vehicle.stammnummer,
    )
    return CachedPlateRecord(info=info, model_variant_id=model_variant_id)


def _named_variant_id(vehicle: BuiltVehicleData, by_fz_key: dict[str, ModelVariant]) -> uuid.UUID | None:
    """The stored variant a Vehicle Build record named, if it named one."""

    if vehicle.variant is None:
        return None
    variant = by_fz_key.get(vehicle.variant.fz_key)
    return variant.id if variant is not None else None


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
    """`vehicle.plate_lookup.conflicted` — one Stammnummer reported under two
    Typenscheine (ADR-039, a data-quality fact). Payload: `plate` (normalised)
    and `records[]`, each `{stammnummer, typeApprovalNumber}`, **both always
    set** (KAN-593): a record the account may not see the Stammnummer of can
    contradict nothing (`_is_conflict`), so it is left out rather than sent as
    a null. The aggregate id is derived from (tenant, plate), so a consumer
    groups repeats of one plate by it.

    Published once per provider answer, never from the cache. An answer the
    cache cannot hold (a record without its Stammnummer or Typenschein, whole
    or not at all) is asked again on the next lookup and publishes again: each
    is a fact of its own — the provider said it at that moment — and a
    consumer is idempotent by `eventId` and groups by plate; no per-day
    dedupe is kept here (KAN-593, decided: it would need a read of the outbox
    or state of its own for a rare, consumer-less signal)."""

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
                    {"stammnummer": r.stammnummer, "typeApprovalNumber": r.type_approval_number}
                    for r in records
                    if r.stammnummer is not None
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
    records: list[CachedPlateRecord],
    notes: list[IdentificationNote],
    conflict_plate: str | None,
) -> IdentificationResult:
    """A cached answer: several records are the picker (each carrying the
    variant it named); one record that named a variant still stored is that
    one candidate — the same answer the live lookup gave (KAN-592); one record
    without continues at its Typenschein from the stored variants (no call),
    where several stored variants sharing the Typenschein are the advisor's
    choice (best match offered), never the cache's guess."""

    if len(records) > 1:
        return _picker_result(
            db, tenant_id=tenant_id, kind=kind, match_method=match_method, observed=observed,
            rows=[_plate_record(db, r) for r in records], notes=notes, conflict_plate=conflict_plate,
        )
    (cached,) = records
    record = cached.info
    named = _named_candidate(db, cached.model_variant_id, first_registration_date=record.first_registration_date)
    if named is not None:
        return IdentificationResult(
            kind=kind,
            match_method=match_method,
            outcome=IdentificationOutcome.VARIANTS,
            observed=dataclasses.replace(
                observed,
                stammnummer=record.stammnummer,
                type_approval_number=record.type_approval_number,
                first_registration_date=record.first_registration_date,
            ),
            variants=[named],
            best_match_available=False,
            notes=notes,
        )
    return _typenschein_result(
        db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=match_method,
        type_approval_number=record.type_approval_number,
        observed=dataclasses.replace(
            observed, stammnummer=record.stammnummer, first_registration_date=record.first_registration_date
        ),
        notes=notes,
        prefer_stored=IdentificationNote.PLATE_CACHE_HIT in notes,
    )


def _picker_row(
    db: Session, vehicle: BuiltVehicleData, *, model_variant_id: uuid.UUID | None = None
) -> PlateRecord | None:
    """A registered car the picker can offer: it needs a Typenschein to
    continue at; the Stammnummer may be withheld from this account. It
    carries the stored variant the record named (KAN-592)."""

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
        variant=_named_candidate(db, model_variant_id, first_registration_date=vehicle.first_registration_date),
    )


def _cache_plate_answer(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    plate: str | None,
    vehicles: list[BuiltVehicleData],
    by_fz_key: dict[str, ModelVariant],
) -> None:
    """A plate answer fills the 30-day cache — whole or not at all: a record
    the account may not see whole cannot be stored (`_cacheable`), and an
    answer stored in part would later read as the complete truth about the
    plate (one car where there were two). A Stammnummer answer never fills
    it: it proves nothing about what else is registered on that plate, and
    would store a plate the advisor never typed (R-2)."""

    if plate is None:
        return
    records = [_cacheable(v, model_variant_id=_named_variant_id(v, by_fz_key)) for v in vehicles]
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
    _cache_plate_answer(db, tenant_id=tenant_id, plate=plate, vehicles=vehicles, by_fz_key=by_fz_key)

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

    rows = [
        r for r in (_picker_row(db, v, model_variant_id=_named_variant_id(v, by_fz_key)) for v in vehicles) if r is not None
    ]
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
