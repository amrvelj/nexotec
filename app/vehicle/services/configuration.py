"""The configuration service (C-C / KAN-41).

Create / read / update / copy a `VehicleConfiguration`. **No delete** —
FR-C-11: a configuration referenced by a host is evidence.

**This module never writes `vehicle-mdm`** (ADR-070). It reads it —
`get_vehicle_mdm_by_vin` — only to set the three-column `vehicle_id` link
when a VIN resolves to an *existing* MDM row.
`tests/architecture/test_configuration_never_writes_vehicle_mdm.py` proves
it (source scan + runtime guard).

Every spec-field edit and every `catalogue_match_status` transition is
audited (`app.core.audit`) — a configuration stays editable after a host
references it, and `overridden_fields` records *that* a field changed but
not who / when / from what. Dates in the audit payload are `.isoformat()`d
before they can reach `json.dumps` (the KAN-29 birth-date-500 shape).
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

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
from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_by_vin

_EVENT_PRODUCER = "vehicle.configuration"
_ENTITY_TYPE = "vehicle_configuration"

_MATCHED_STATUSES = {ConfigurationMatchStatus.MATCHED, ConfigurationMatchStatus.BEST_MATCH_CONFIRMED}

# The five coded spec fields declared directly on the carriers (not in the
# `VehicleSpecBlock` mixin) — see the model. Copied / diffed alongside
# `SPEC_BLOCK_FIELDS`.
_CODED_SPEC_FIELDS = ("vehicle_kind", "fuel_type", "body_style", "drivetrain", "transmission")
_ALL_SPEC_FIELDS = (*SPEC_BLOCK_FIELDS, *_CODED_SPEC_FIELDS)


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
        config.catalogue_match_status = ConfigurationMatchStatus.MATCHED
    else:
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
    spec_before: dict[str, Any] = {}
    spec_after: dict[str, Any] = {}

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

    for field in (
        "mode",
        "match_method",
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
        "notes",
        "brand_display_name",
        "model_group_name",
        "variant_name",
        *_CODED_SPEC_FIELDS,
    ):
        value = getattr(data, field)
        if value is not None:
            setattr(configuration, field, value)

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
