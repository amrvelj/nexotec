"""The configuration service (C-C / KAN-41).

Create / read / update / copy a `VehicleConfiguration`. **No delete** —
FR-C-11: a configuration referenced by a host is evidence.

**This module never writes `vehicle-mdm`** (ADR-070). It reads it —
`get_vehicle_mdm_by_vin` — only to set the three-column `vehicle_id` link
when a VIN resolves to an *existing* MDM row.
`tests/architecture/test_configuration_never_writes_vehicle_mdm.py` proves
it (source scan + runtime guard).

Every field edit — spec block, coded fields and the top-level observed
fields (VIN, mileage, colours, …; KAN-319) — and every
`catalogue_match_status` transition is audited (`app.core.audit`) — a
configuration stays editable after a host references it, and
`overridden_fields` records *that* a field changed but not who / when /
from what. Dates in the audit payload are `.isoformat()`d
before they can reach `json.dumps` (the KAN-29 birth-date-500 shape).
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.configuration import (
    ConfigurationMatchStatus,
    ConfigurationSource,
    VehicleConfiguration,
    VehicleConfigurationOption,
    VehicleConfigurationOptionFeature,
)
from app.vehicle.models.spec_block import SPEC_BLOCK_FIELDS
from app.vehicle.schemas.configuration import (
    ConfigurationCreate,
    ConfigurationOptionInput,
    ConfigurationUpdate,
)
from app.vehicle.services.reference_codes import validate_reference_codes
from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_by_vin

_EVENT_PRODUCER = "vehicle.configuration"
_ENTITY_TYPE = "vehicle_configuration"

_MATCHED_STATUSES = {ConfigurationMatchStatus.MATCHED, ConfigurationMatchStatus.BEST_MATCH_CONFIRMED}

# The five coded spec fields declared directly on the carriers (not in the
# `VehicleSpecBlock` mixin) — see the model. Copied / diffed alongside
# `SPEC_BLOCK_FIELDS`. Each holds a value of the reference list of the same
# name; a value a client sends is checked against it (KAN-96).
_CODED_SPEC_FIELDS = ("vehicle_kind", "fuel_type", "body_style", "drivetrain", "transmission")
_ALL_SPEC_FIELDS = (*SPEC_BLOCK_FIELDS, *_CODED_SPEC_FIELDS)
# KAN-245 — the spec block's own reference fields. Each holds a value of the
# reference list of the same name (migrations a3d9c1e58f27: engine_cycle,
# valuation_classification; 6ba0a99ed5c4: vehicle_class, emission_standard,
# consumption_norm) and is checked exactly like a coded field.
_SPEC_REFERENCE_FIELDS = ("engine_cycle", "vehicle_class", "valuation_classification", "emission_standard", "consumption_norm")

# The top-level fields a PATCH writes (R2, Anto 2026-10-12): absent leaves a
# field alone, an explicit null clears it. The coded fields are handled on
# their own (locked on a provider configuration, required on a manual one).
_PATCHABLE_NULLABLE_FIELDS = (
    "vin",
    "stammnummer",
    "type_approval_number",
    "first_registration_date",
    "licence_plate",
    "mileage_km",
    "exterior_colour",
    "interior_colour",
    "exterior_colour_surcharge",
    "interior_colour_surcharge",
    "wheels",
    "wheels_surcharge",
    "notes",
    "brand_display_name",
    "model_group_name",
    "variant_name",
)


def _audit_value(value: Any) -> Any:
    """Make a value safe for the audit payload's ``json.dumps``:
    ``dt.date``/``dt.datetime`` → ISO string (the KAN-29 birth-date-500
    shape), ``Decimal`` → string (spec fields like consumption / base price),
    enum → its value; everything else unchanged."""

    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value.value if hasattr(value, "value") else value


def get_configuration_or_404(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID
) -> VehicleConfiguration:
    config = db.scalar(
        select(VehicleConfiguration)
        .options(
            selectinload(VehicleConfiguration.options).selectinload(
                VehicleConfigurationOption.equipment_feature_links
            )
        )
        .where(
            VehicleConfiguration.id == configuration_id,
            VehicleConfiguration.tenant_id == tenant_id,
        )
    )
    if config is None:
        # 404, never 403 — a 403 would confirm the record exists (rule 7).
        raise NotFoundError(f"Configuration {configuration_id} was not found.")
    return config


def _copy_spec_block_from_variant(config: VehicleConfiguration, variant: ModelVariant) -> None:
    for field in _ALL_SPEC_FIELDS:
        setattr(config, field, getattr(variant, field))
    config.brand_display_name = variant.brand_display_name
    config.model_group_name = variant.model_group_name
    config.variant_name = variant.variant_name


def _apply_spec_payload(config: VehicleConfiguration, spec: Any) -> None:
    data = spec.model_dump()
    for field in SPEC_BLOCK_FIELDS:
        if field in data:
            setattr(config, field, data[field])


def _validate_coded_spec_fields(db: Session, data: ConfigurationCreate | ConfigurationUpdate) -> None:
    """Every coded value the client sent — the five coded fields (KAN-96)
    and the spec block's five reference fields (KAN-245) — must be an active
    value of its reference list: otherwise one 422 naming every offending
    field; 500 if a list is missing (`validate_reference_codes`, the rule
    KAN-57 set for `/v1/vehicles`).

    A PATCH checks every value it carries, one sent back unchanged included:
    a value retired from its list refuses the save (Anto, 2026-10-07; for
    the spec block, 2026-10-12). Codes a provider configuration copies from
    its variant at capture are not checked here — their integrity belongs
    to catalogue sync (KAN-75 / KAN-77).
    """

    values: dict[str, Any] = {field: getattr(data, field) for field in _CODED_SPEC_FIELDS}
    if data.spec is not None:
        spec = data.spec.model_dump(exclude_unset=True)
        values.update({field: spec[field] for field in _SPEC_REFERENCE_FIELDS if field in spec})
    validate_reference_codes(db, values, (*_CODED_SPEC_FIELDS, *_SPEC_REFERENCE_FIELDS))


def _require_coded_fields_on_create(data: ConfigurationCreate) -> None:
    """R1 (Anto, 2026-10-12) — a manual configuration names all five coded
    fields: there is no catalogue to supply them. One 422 naming each
    missing field."""

    missing = [to_camel(field) for field in _CODED_SPEC_FIELDS if getattr(data, field) is None]
    if missing:
        raise UnprocessableEntityError(
            f"A manual configuration needs all five coded fields; missing: {', '.join(missing)}.",
            details={"reason": "coded_fields_required", "missing": missing},
        )


def _check_coded_field_patch(configuration: VehicleConfiguration, data: ConfigurationUpdate) -> dict[str, Any]:
    """R1 (Anto, 2026-10-12) — which coded fields this PATCH may write.

    On a provider configuration (`source = provider`, built from Auto-i-DAT's
    catalogue — the same test the configurator overlay applies, and the one
    KAN-240's fill uses) the five coded fields are the catalogue's: a
    PATCH that changes one is a 422 `coded_field_locked` naming each field —
    the same 422-with-a-reason shape as `configuration_mode_fixed`, the
    other state-based refusal of this endpoint. Sending the value it already
    has is a no-op. FR-C-16's re-sync (`configuration_host.apply_resync`)
    still copies them from the catalogue; it does not come through here.

    On a manual configuration none of the five can be cleared: an explicit
    null is a 422 `coded_field_required`.

    Returns the coded fields that actually change, new value by field.
    """

    sent = {field: getattr(data, field) for field in _CODED_SPEC_FIELDS if field in data.model_fields_set}
    changed = {field: value for field, value in sent.items() if value != getattr(configuration, field)}
    if configuration.source == ConfigurationSource.PROVIDER:
        if changed:
            fields = [to_camel(field) for field in changed]
            raise UnprocessableEntityError(
                "A provider configuration's coded fields come from the catalogue and cannot be changed: "
                f"{', '.join(fields)}.",
                details={"reason": "coded_field_locked", "fields": fields},
            )
        return {}
    cleared = [to_camel(field) for field, value in sent.items() if value is None]
    if cleared:
        raise UnprocessableEntityError(
            f"A manual configuration's coded fields cannot be cleared: {', '.join(cleared)}.",
            details={"reason": "coded_field_required", "fields": cleared},
        )
    return changed


def _link_vehicle_if_vin_resolves(db: Session, config: VehicleConfiguration) -> None:
    """A VIN that resolves to an existing `vehicle_mdm` row links the
    configuration to it (three-column pattern). **A miss does nothing** —
    it never creates an MDM record (ADR-070)."""

    if not config.vin:
        return
    vehicle = get_vehicle_mdm_by_vin(db, config.vin)
    if vehicle is None:
        return
    config.vehicle_id = vehicle.id
    config.vehicle_label = vehicle.vehicle_number
    config.vehicle_label_refreshed_at = utcnow()


def create_configuration(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, data: ConfigurationCreate
) -> VehicleConfiguration:
    config = VehicleConfiguration(
        tenant_id=tenant_id,
        source=data.source,
        mode=data.mode,
        match_method=data.match_method,
        overridden_fields=[],
        created_by=actor_id,
        updated_by=actor_id,
        vin=data.vin,
        stammnummer=data.stammnummer,
        type_approval_number=data.type_approval_number,
        first_registration_date=data.first_registration_date,
        licence_plate=data.licence_plate,
        mileage_km=data.mileage_km,
        exterior_colour=data.exterior_colour,
        interior_colour=data.interior_colour,
        exterior_colour_surcharge=data.exterior_colour_surcharge,
        interior_colour_surcharge=data.interior_colour_surcharge,
        wheels=data.wheels,
        wheels_surcharge=data.wheels_surcharge,
        notes=data.notes,
    )

    if data.source == ConfigurationSource.PROVIDER:
        if data.catalogue_variant_id is None:
            raise UnprocessableEntityError("A provider configuration needs a catalogueVariantId.")
        variant = db.get(ModelVariant, data.catalogue_variant_id)
        if variant is None:
            raise NotFoundError(f"Catalogue variant {data.catalogue_variant_id} was not found.")
        config.catalogue_variant_id = variant.id
        config.catalogue_variant_label = _variant_label(variant)
        config.catalogue_variant_label_refreshed_at = utcnow()
        _copy_spec_block_from_variant(config, variant)
        config.catalogue_match_status = (
            ConfigurationMatchStatus.BEST_MATCH_CONFIRMED
            if data.confirmed_best_match_code == 2
            else ConfigurationMatchStatus.MATCHED
        )
    else:
        if data.confirmed_best_match_code is not None:
            raise UnprocessableEntityError("A confirmed best match needs a provider configuration.")
        _require_coded_fields_on_create(data)
        _validate_coded_spec_fields(db, data)
        config.catalogue_match_status = ConfigurationMatchStatus.UNVERIFIED
        if data.spec is not None:
            _apply_spec_payload(config, data.spec)
        for field in ("brand_display_name", "model_group_name", "variant_name", *_CODED_SPEC_FIELDS):
            value = getattr(data, field)
            if value is not None:
                setattr(config, field, value)

    _link_vehicle_if_vin_resolves(db, config)

    db.add(config)
    db.flush()

    record_audit_event(
        db,
        entity_type=_ENTITY_TYPE,
        entity_id=config.id,
        tenant_id=tenant_id,
        action="create",
        actor_id=actor_id,
        after={
            "source": config.source.value,
            "mode": config.mode.value,
            "catalogueMatchStatus": config.catalogue_match_status.value,
            "matchMethod": config.match_method.value,
            "catalogueVariantId": str(config.catalogue_variant_id) if config.catalogue_variant_id else None,
            "vehicleId": str(config.vehicle_id) if config.vehicle_id else None,
            "firstRegistrationDate": _audit_value(config.first_registration_date),
        },
    )
    _publish(db, config, "configuration.created")
    db.commit()
    db.refresh(config)
    return get_configuration_or_404(db, tenant_id=tenant_id, configuration_id=config.id)


def update_configuration(
    db: Session,
    *,
    configuration: VehicleConfiguration,
    actor_id: uuid.UUID,
    data: ConfigurationUpdate,
) -> VehicleConfiguration:
    # C-F (KAN-10): a configuration's mode is fixed once saved. Hosts check
    # the PRD v1.4 mode matrix when they attach one (offer Path B build only,
    # valuation record only); changing it afterwards would put a record
    # configuration under an offer, or a build one under a valuation,
    # without any host ever seeing the change.
    if data.mode is not None and data.mode != configuration.mode:
        raise UnprocessableEntityError(
            "A saved configuration's mode cannot be changed; copy it into a new configuration instead.",
            details={"reason": "configuration_mode_fixed"},
        )
    # Before anything on the row changes: a refused PATCH leaves it as it was.
    _validate_coded_spec_fields(db, data)
    coded_changes = _check_coded_field_patch(configuration, data)

    spec_before: dict[str, Any] = {}
    spec_after: dict[str, Any] = {}

    # R3 — a coded-field change is audited exactly like a spec-block field;
    # an unchanged value records nothing. (A provider configuration's coded
    # fields cannot change here, so none is ever marked overridden.)
    for field, new in coded_changes.items():
        spec_before[field] = _audit_value(getattr(configuration, field))
        spec_after[field] = _audit_value(new)
        setattr(configuration, field, new)

    if data.spec is not None:
        # exclude_unset — a PATCH sends only the fields it wants changed;
        # a missing field is "leave alone", never "set to null".
        payload = data.spec.model_dump(exclude_unset=True)
        for field in SPEC_BLOCK_FIELDS:
            if field not in payload:
                continue
            current = getattr(configuration, field)
            new = payload[field]
            if current == new:
                continue
            spec_before[field] = _audit_value(current)
            spec_after[field] = _audit_value(new)
            setattr(configuration, field, new)
            # A field edited after a catalogue copy is now overridden.
            if configuration.catalogue_variant_id is not None and field not in configuration.overridden_fields:
                configuration.overridden_fields = [*configuration.overridden_fields, field]

    status_before = configuration.catalogue_match_status
    status_transition = False
    if data.catalogue_match_status is not None and data.catalogue_match_status != status_before:
        configuration.catalogue_match_status = data.catalogue_match_status
        status_transition = True
    if data.catalogue_variant_id is not None and data.catalogue_variant_id != configuration.catalogue_variant_id:
        variant = db.get(ModelVariant, data.catalogue_variant_id)
        if variant is None:
            raise NotFoundError(f"Catalogue variant {data.catalogue_variant_id} was not found.")
        configuration.catalogue_variant_id = variant.id
        configuration.catalogue_variant_label = _variant_label(variant)
        configuration.catalogue_variant_label_refreshed_at = utcnow()
        status_transition = True

    # `mode` and `match_method` are never null on the row: a null leaves them.
    # (`mode` cannot change — checked above — so only the match method can
    # reach the audit here; it is kept permanently, a dispute turns on it.)
    for field in ("mode", "match_method"):
        value = getattr(data, field)
        if value is not None and value != getattr(configuration, field):
            spec_before[field] = _audit_value(getattr(configuration, field))
            spec_after[field] = _audit_value(value)
            setattr(configuration, field, value)
    # R2 (KAN-247) — absent is "leave alone", an explicit null clears, the
    # way the spec block already reads `exclude_unset`. KAN-319: a change
    # or a clearing is in the audit before/after exactly like a spec field
    # (FR-C-11: a referenced configuration is evidence); an unchanged value
    # records nothing. These fields are not catalogue copies, so none is
    # marked overridden.
    for field in _PATCHABLE_NULLABLE_FIELDS:
        if field not in data.model_fields_set:
            continue
        current = getattr(configuration, field)
        new = getattr(data, field)
        if current == new:
            continue
        spec_before[field] = _audit_value(current)
        spec_after[field] = _audit_value(new)
        setattr(configuration, field, new)

    _link_vehicle_if_vin_resolves(db, configuration)

    configuration.updated_by = actor_id
    configuration.version += 1
    db.flush()

    if spec_before or spec_after:
        record_audit_event(
            db,
            entity_type=_ENTITY_TYPE,
            entity_id=configuration.id,
            tenant_id=configuration.tenant_id,
            action="update",
            actor_id=actor_id,
            before=spec_before or None,
            after=spec_after or None,
        )
    if status_transition:
        record_audit_event(
            db,
            entity_type=_ENTITY_TYPE,
            entity_id=configuration.id,
            tenant_id=configuration.tenant_id,
            action="match_status_change",
            actor_id=actor_id,
            before={"catalogueMatchStatus": status_before.value},
            after={
                "catalogueMatchStatus": configuration.catalogue_match_status.value,
                "catalogueVariantId": str(configuration.catalogue_variant_id)
                if configuration.catalogue_variant_id
                else None,
            },
        )

    _publish(db, configuration, "configuration.updated")
    if status_transition and configuration.catalogue_match_status in _MATCHED_STATUSES:
        _publish(db, configuration, "configuration.matched")

    db.commit()
    db.refresh(configuration)
    return get_configuration_or_404(
        db, tenant_id=configuration.tenant_id, configuration_id=configuration.id
    )


def copy_configuration(
    db: Session, *, source: VehicleConfiguration, actor_id: uuid.UUID
) -> VehicleConfiguration:
    """FR-C-11 — a new configuration with the same content and a new id
    (what an advisor does when the customer asks about the same car in a
    different trim)."""

    copy = VehicleConfiguration(
        tenant_id=source.tenant_id,
        source=source.source,
        mode=source.mode,
        match_method=source.match_method,
        catalogue_match_status=source.catalogue_match_status,
        catalogue_variant_id=source.catalogue_variant_id,
        catalogue_variant_label=source.catalogue_variant_label,
        catalogue_variant_label_refreshed_at=source.catalogue_variant_label_refreshed_at,
        vin=source.vin,
        stammnummer=source.stammnummer,
        type_approval_number=source.type_approval_number,
        first_registration_date=source.first_registration_date,
        licence_plate=source.licence_plate,
        mileage_km=source.mileage_km,
        vehicle_id=source.vehicle_id,
        vehicle_label=source.vehicle_label,
        vehicle_label_refreshed_at=source.vehicle_label_refreshed_at,
        brand_display_name=source.brand_display_name,
        model_group_name=source.model_group_name,
        variant_name=source.variant_name,
        exterior_colour=source.exterior_colour,
        interior_colour=source.interior_colour,
        exterior_colour_surcharge=source.exterior_colour_surcharge,
        interior_colour_surcharge=source.interior_colour_surcharge,
        wheels=source.wheels,
        wheels_surcharge=source.wheels_surcharge,
        overridden_fields=list(source.overridden_fields),
        notes=source.notes,
        created_by=actor_id,
        updated_by=actor_id,
    )
    for field in _ALL_SPEC_FIELDS:
        setattr(copy, field, getattr(source, field))
    db.add(copy)
    db.flush()

    for opt in source.options:
        new_opt = VehicleConfigurationOption(
            tenant_id=copy.tenant_id,
            configuration_id=copy.id,
            sequence=opt.sequence,
            variant_option_id=opt.variant_option_id,
            option_code=opt.option_code,
            description=opt.description,
            option_group=opt.option_group,
            price=opt.price,
            is_included=opt.is_included,
            is_package=opt.is_package,
            selected=opt.selected,
        )
        db.add(new_opt)
        db.flush()
        for link in opt.equipment_feature_links:
            db.add(
                VehicleConfigurationOptionFeature(
                    tenant_id=copy.tenant_id,
                    configuration_option_id=new_opt.id,
                    feature_value_code=link.feature_value_code,
                )
            )

    record_audit_event(
        db,
        entity_type=_ENTITY_TYPE,
        entity_id=copy.id,
        tenant_id=copy.tenant_id,
        action="create",
        actor_id=actor_id,
        after={"copiedFrom": str(source.id)},
    )
    _publish(db, copy, "configuration.created")
    db.commit()
    db.refresh(copy)
    return get_configuration_or_404(db, tenant_id=copy.tenant_id, configuration_id=copy.id)


def replace_options(
    db: Session,
    *,
    configuration: VehicleConfiguration,
    actor_id: uuid.UUID,
    options: list[ConfigurationOptionInput],
) -> VehicleConfiguration:
    """Full replace of the option rows, sequence = list order. C-C's
    hand-typed path; priced catalogue options / packages / colours are C-E."""

    configuration.options.clear()
    db.flush()
    for position, item in enumerate(options):
        opt = VehicleConfigurationOption(
            tenant_id=configuration.tenant_id,
            configuration_id=configuration.id,
            sequence=position,
            variant_option_id=item.variant_option_id,
            option_code=item.option_code,
            description=item.description,
            option_group=item.option_group,
            price=item.price,
            is_included=item.is_included,
            is_package=item.is_package,
            selected=item.selected,
        )
        db.add(opt)
        db.flush()
        for code in item.equipment_features:
            db.add(
                VehicleConfigurationOptionFeature(
                    tenant_id=configuration.tenant_id,
                    configuration_option_id=opt.id,
                    feature_value_code=code,
                )
            )

    configuration.updated_by = actor_id
    configuration.version += 1
    db.flush()
    _publish(db, configuration, "configuration.updated")
    db.commit()
    db.refresh(configuration)
    return get_configuration_or_404(
        db, tenant_id=configuration.tenant_id, configuration_id=configuration.id
    )


def _variant_label(variant: ModelVariant) -> str:
    parts = [variant.brand_display_name, variant.model_group_name, variant.name]
    return " ".join(p for p in parts if p)[:200]


def _publish(db: Session, config: VehicleConfiguration, event_type: str) -> None:
    publish(
        db,
        OutboxEvent(
            event_type=event_type,
            tenant_id=config.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type=_ENTITY_TYPE,
            aggregate_id=config.id,
            payload={
                "source": config.source.value,
                "mode": config.mode.value,
                "catalogueMatchStatus": config.catalogue_match_status.value,
                "catalogueVariantId": str(config.catalogue_variant_id) if config.catalogue_variant_id else None,
            },
        ),
    )


def fill_null_spec_fields_from_variants(db: Session, *, variant_ids: list[uuid.UUID], reason: str) -> int:
    """KAN-240 (Anto, 2026-10-12): when resolving a mapping gap has filled
    catalogue variants (`catalogue_sync.fill_variants_for_resolved_code`),
    every provider configuration built from one of them gets that variant's
    value in each spec-block or coded field it still holds as `NULL` — the
    `NULL`s `_copy_spec_block_from_variant` copied before the gap was
    resolved. Returns how many configurations changed.

    - **Which configurations:** every `source = provider` configuration whose
      `catalogue_variant_id` is one of `variant_ids`, in every tenant (each
      row stays in its own tenant; the variant is global, ADR-075). A
      configuration has no lifecycle state of its own and "stays editable
      after a host references it" (module docstring); what a host has
      committed lives in the host's frozen `vehicle_snapshot` (ADR-041), which
      this never touches. So "open" is every such configuration.
    - **Which fields:** `_ALL_SPEC_FIELDS` that are `NULL` here and set on the
      variant. A value already set — the advisor's or the catalogue's — is
      never overwritten, and a field in `overridden_fields` is skipped even
      when `NULL`: the advisor cleared it on purpose (FR-C-03).
    - Each changed configuration's `version` moves once, an audit record is
      written with no actor (the system) and `reason` — `updated_by` is
      cleared to match — and
      `configuration.updated` is published, like every other configuration
      write. Nothing is committed here (the caller's transaction), and
      vehicle-mdm is never written (ADR-070).
    """

    if not variant_ids:
        return 0
    variants = {
        variant.id: variant
        for variant in db.scalars(
            select(ModelVariant)
            .where(ModelVariant.id.in_(variant_ids))
            .execution_options(populate_existing=True)
        )
    }
    configurations = db.scalars(
        select(VehicleConfiguration)
        .where(
            VehicleConfiguration.catalogue_variant_id.in_(variant_ids),
            VehicleConfiguration.source == ConfigurationSource.PROVIDER,
        )
        .order_by(VehicleConfiguration.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()

    changed = 0
    for configuration in configurations:
        variant = variants[configuration.catalogue_variant_id]  # type: ignore[index]
        overridden = set(configuration.overridden_fields or [])
        after: dict[str, Any] = {}
        for field in _ALL_SPEC_FIELDS:
            value = getattr(variant, field)
            if field in overridden or value is None or getattr(configuration, field) is not None:
                continue
            setattr(configuration, field, value)
            after[field] = _audit_value(value)
        if not after:
            continue
        configuration.version += 1
        configuration.updated_by = None  # a system write (KAN-243: null actor), like its audit record
        db.flush()
        record_audit_event(
            db,
            entity_type=_ENTITY_TYPE,
            entity_id=configuration.id,
            tenant_id=configuration.tenant_id,
            action="catalogue_fill",
            actor_id=None,
            before=dict.fromkeys(after),
            after=after,
            reason=reason,
        )
        _publish(db, configuration, "configuration.updated")
        changed += 1
    return changed
