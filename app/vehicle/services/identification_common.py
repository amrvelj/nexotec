"""What the identification rungs share, split from `identification.py`
(KAN-670, a pure move): the candidate builders, the vehicle-mdm hit, the one
place a rung asks the provider (`_ask_provider`, through the gateway), the
store and merge of seen variants, and the Typenschein rung — which the
registered-vehicle rungs fall back to.

Imports `identification_types.py`; `identification_registered.py` and
`identification.py` import this module. Nothing here writes `vehicle-mdm`
(ADR-070).
"""

import dataclasses
import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integration.public import (
    ProviderGatewayError,
    ProviderNotEntitledError,
    VariantMasterData,
    call_capability,
    get_entitlement,
)
from app.vehicle.models.catalogue import ModelVariant, VariantPrice
from app.vehicle.models.configuration import ConfigurationMatchMethod, VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.catalogue import find_model_variants_by_type_approval
from app.vehicle.services.catalogue_sync import (
    find_enabled_vehicle_data_connection,
    store_seen_variants,
)
from app.vehicle.services.identification_types import (
    _IDENT_ENTITLEMENT,
    _TYPENSCHEIN_CALL_LABEL,
    ExistingVehicle,
    IdentificationNote,
    IdentificationOutcome,
    IdentificationResult,
    IdentifierKind,
    ObservedIdentity,
    PlateRecord,
    VariantCandidate,
)
from app.vehicle.services.plate_lookup_cache import CachedPlateRecord

logger = logging.getLogger(__name__)


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


def _named_candidate(
    db: Session, model_variant_id: uuid.UUID | None, *, first_registration_date: dt.date | None
) -> "VariantCandidate | None":
    """The candidate for the stored variant a record named (KAN-592), or None
    when it named none or the catalogue no longer holds it."""

    variant = db.get(ModelVariant, model_variant_id) if model_variant_id is not None else None
    return _candidate(db, variant, first_registration_date=first_registration_date) if variant is not None else None


def _plate_record(db: Session, cached: CachedPlateRecord) -> PlateRecord:
    record = cached.info
    return PlateRecord(
        vehicle_kind_code=record.vehicle_kind_code,
        brand_name=record.brand_name,
        model_description=record.model_description,
        production_from=record.production_from,
        production_to=record.production_to,
        type_approval_number=record.type_approval_number,
        first_registration_date=record.first_registration_date,
        stammnummer=record.stammnummer,
        variant=_named_candidate(db, cached.model_variant_id, first_registration_date=record.first_registration_date),
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
