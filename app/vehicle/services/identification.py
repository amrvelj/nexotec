"""Identify a vehicle from one input — the FR-C-02 waterfall (C-D / KAN-42;
live on the identAPI since KAN-547, ADR-111, ADR-114).

The advisor types one thing; `classify_identifier` decides from the string
whether it is a VIN, a Kontrollschild, a Stammnummer, a Typenschein or a
Werkscode (the FR-V-06 one-input principle). Each kind then runs its rung
of the waterfall, in the order the counter uses it:

1. **VIN** — our own `vehicle-mdm` first (`matching.match_vehicle`, reused,
   never paralleled). A hit is decisive and offers this tenant's newest
   configuration of that car for reuse. A miss asks the identAPI's Vehicle
   Build by `vin` (KAN-549) — only where the connection's probed `vin`
   licence option is granted (ADR-113: not in the test licence, §8);
   otherwise the note `vin_decode_not_entitled` and no call. A VIN the
   provider does not know falls through to the manual path with the VIN
   kept as observed data.
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

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.integration.public import (
    BuiltVehicleData,
    ProviderGatewayError,
    call_capability,
    vin_decode_granted,
)
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.configuration import ConfigurationMatchMethod
from app.vehicle.services.catalogue_sync import (
    find_enabled_vehicle_data_connection,
    upsert_model_variant,
)
from app.vehicle.services.identification_common import (
    _ask_provider,
    _candidate,
    _existing_vehicle_result,
    _merge_candidates,
    _store_variants,
    _typenschein_result,
)
from app.vehicle.services.identification_registered import (
    _built_vehicles_result,
    _identify_plate,
    _identify_stammnummer,
)
from app.vehicle.services.identification_types import (
    _BEST_MATCH_CALL_LABEL,
    _TYPENSCHEIN_RE,
    _VIN_CALL_LABEL,
    _WERKSCODE_CALL_LABEL,
    BestMatchField,
    BestMatchProposal,
    IdentificationNote,
    IdentificationOutcome,
    IdentificationResult,
    IdentifierKind,
    NewPriceSource,
    ObservedIdentity,
    classify_identifier,
)
from app.vehicle.services.matching import match_vehicle


def _identify_vin(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, vin: str) -> IdentificationResult:
    """Step 1: vehicle-mdm, then — only where the connection's `vin` licence
    option is granted (KAN-549) — one Vehicle Build lookup by VIN, read like
    the plate and Stammnummer answers. A provider that does not know the VIN,
    refuses it or fails leaves the VIN as observed data for the manual path."""

    kind, method = IdentifierKind.VIN, ConfigurationMatchMethod.VIN
    observed = ObservedIdentity(vin=vin)
    found = match_vehicle(db, vin=vin)
    if found.vehicle is not None:
        return _existing_vehicle_result(
            db, tenant_id=tenant_id, vehicle=found.vehicle, kind=kind, match_method=method, observed=observed,
        )

    connection = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if connection is None:
        return IdentificationResult(
            kind=kind, match_method=method, outcome=IdentificationOutcome.NONE, observed=observed,
            notes=[IdentificationNote.NO_PROVIDER_CONNECTION],
        )
    if not vin_decode_granted(db, connection=connection[0]):
        return IdentificationResult(
            kind=kind, match_method=method, outcome=IdentificationOutcome.NONE, observed=observed,
            notes=[IdentificationNote.VIN_DECODE_NOT_ENTITLED],
        )

    answer = _ask_provider(
        db, tenant_id=tenant_id, actor_id=actor_id, label=_VIN_CALL_LABEL, purpose="identify_vin",
        call=lambda adapter: adapter.identify_by_vin(vin),
        not_entitled_note=IdentificationNote.VIN_DECODE_NOT_ENTITLED,
    )
    vehicles: list[BuiltVehicleData] = list(answer.value) if answer.answered else []
    if not vehicles:
        return IdentificationResult(
            kind=kind, match_method=method, outcome=IdentificationOutcome.NONE, observed=observed, notes=answer.notes
        )
    assert answer.provider_code is not None
    # `plate=None`: a VIN answer proves nothing about a plate, so it never
    # fills the plate-lookup cache.
    return _built_vehicles_result(
        db, tenant_id=tenant_id, actor_id=actor_id, kind=kind, match_method=method, observed=observed,
        vehicles=vehicles, provider_code=answer.provider_code, plate=None,
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
