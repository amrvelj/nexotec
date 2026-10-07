"""What a host reads from a configuration (C-F / KAN-10, FR-C-12 … FR-C-16).

Four hosts store a `configurationId` — an offer, a stock item, a valuation,
and (by the configuration's own `vehicle_id`) a vehicle. They never import
this context's models: they read `HostConfiguration` through
`app.vehicle.public`, a plain value with the identity, the price lines and
the ADR-071 specification block, which is what the offer freezes into its
`vehicleSnapshot` (the block's third carrier).

**Which modes a host may use is decided by the host** (PRD v1.4 mode
matrix), so each host checks `HostConfiguration.mode` itself:
offer Path B `build` only · valuation `record` only · pipeline both.

Re-sync (FR-C-16) lives here too, because it is a host-triggered action on
a configuration that may already sit under a live offer: it is never
automatic, lists every field the catalogue now disagrees on, marks the
ones the advisor overrode, and applies only the fields chosen.
"""

import dataclasses
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.configuration import ConfigurationMode, VehicleConfiguration
from app.vehicle.models.spec_block import spec_block_as_dict
from app.vehicle.services.configuration import (
    _ALL_SPEC_FIELDS,
    _ENTITY_TYPE,
    _audit_value,
    _publish,
    get_configuration_or_404,
)
from app.vehicle.services.host_configuration import (
    ConfigurationPriceLine,
    HostConfiguration,
)


def configuration_label(config: VehicleConfiguration) -> str:
    parts = [config.brand_display_name, config.model_group_name, config.variant_name]
    label = " ".join(p for p in parts if p)
    return (label or config.catalogue_variant_label or "—")[:200]


def _price_lines(config: VehicleConfiguration) -> list[ConfigurationPriceLine]:
    if config.mode != ConfigurationMode.BUILD:
        return []
    lines = [
        ConfigurationPriceLine(kind="option", code=o.option_code, label=o.description, price=o.price)
        for o in sorted(config.options, key=lambda o: (o.sequence, o.id))
        if o.selected and o.price is not None and not o.is_included
    ]
    for kind, label, price in (
        ("exterior_colour", config.exterior_colour, config.exterior_colour_surcharge),
        ("interior_colour", config.interior_colour, config.interior_colour_surcharge),
        ("wheels", config.wheels, config.wheels_surcharge),
    ):
        if price is not None and price != 0 and label:
            lines.append(ConfigurationPriceLine(kind=kind, code=None, label=label, price=price))
    return lines


def get_configuration_for_host(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID
) -> HostConfiguration:
    """404 for another tenant's configuration (rule 7)."""

    config = get_configuration_or_404(db, tenant_id=tenant_id, configuration_id=configuration_id)
    return HostConfiguration(
        id=config.id,
        version=config.version,
        mode=config.mode.value,
        source=config.source.value,
        catalogue_match_status=config.catalogue_match_status.value,
        label=configuration_label(config),
        brand_display_name=config.brand_display_name,
        model_group_name=config.model_group_name,
        variant_name=config.variant_name,
        vin=config.vin,
        licence_plate=config.licence_plate,
        first_registration_date=config.first_registration_date,
        mileage_km=config.mileage_km,
        vehicle_id=config.vehicle_id,
        base_price=config.base_price,
        base_price_year=config.base_price_year,
        price_lines=_price_lines(config),
        spec={
            **{k: _audit_value(v) for k, v in spec_block_as_dict(config).items()},
            "vehicleKind": config.vehicle_kind,
            "fuelType": config.fuel_type,
            "bodyStyle": config.body_style,
            "drivetrain": config.drivetrain,
            "transmission": config.transmission,
        },
    )


def find_configuration_for_vehicle(
    db: Session, *, tenant_id: uuid.UUID, vehicle_id: uuid.UUID
) -> VehicleConfiguration:
    """FR-C-15 — the configuration Vehicle 360's Specification tab renders:
    this tenant's newest configuration linked to the vehicle. 404 when the
    tenant holds none (a configuration is tenant-private, ADR-013)."""

    config_id = db.scalar(
        select(VehicleConfiguration.id)
        .where(VehicleConfiguration.tenant_id == tenant_id, VehicleConfiguration.vehicle_id == vehicle_id)
        .order_by(VehicleConfiguration.created_at.desc(), VehicleConfiguration.id.desc())
        .limit(1)
    )
    if config_id is None:
        raise NotFoundError(f"No configuration is linked to vehicle {vehicle_id}.")
    return get_configuration_or_404(db, tenant_id=tenant_id, configuration_id=config_id)


# --- FR-C-16 re-sync ---------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ResyncField:
    field: str
    current: Any
    catalogue: Any
    overridden: bool


def _catalogue_variant_or_409(db: Session, config: VehicleConfiguration) -> ModelVariant:
    if config.catalogue_variant_id is None:
        raise ConflictError(
            "A configuration with no catalogue variant cannot be re-synced.",
            details={"reason": "no_catalogue_variant"},
        )
    variant = db.get(ModelVariant, config.catalogue_variant_id)
    if variant is None:
        raise ConflictError(
            "The configuration's catalogue variant no longer exists.", details={"reason": "catalogue_variant_missing"}
        )
    return variant


def preview_resync(db: Session, *, configuration: VehicleConfiguration) -> list[ResyncField]:
    """Every spec field on which the catalogue now disagrees. Reads only."""

    variant = _catalogue_variant_or_409(db, configuration)
    overridden = set(configuration.overridden_fields or [])
    return [
        ResyncField(
            field=field,
            current=_audit_value(getattr(configuration, field)),
            catalogue=_audit_value(getattr(variant, field)),
            overridden=field in overridden,
        )
        for field in _ALL_SPEC_FIELDS
        if getattr(configuration, field) != getattr(variant, field)
    ]


def apply_resync(
    db: Session, *, configuration: VehicleConfiguration, actor_id: uuid.UUID, fields: list[str]
) -> VehicleConfiguration:
    """Copies exactly the chosen fields from the catalogue variant. A field
    not chosen keeps its value — overridden or not. A chosen field is no
    longer overridden: it now equals the catalogue again. Never called by
    anything but an advisor's explicit request."""

    if not fields:
        raise UnprocessableEntityError("Choose at least one field to re-sync.", details={"reason": "no_fields"})
    unknown = sorted(set(fields) - set(_ALL_SPEC_FIELDS))
    if unknown:
        raise UnprocessableEntityError(
            "Unknown specification fields.", details={"reason": "unknown_fields", "fields": unknown}
        )
    variant = _catalogue_variant_or_409(db, configuration)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field in dict.fromkeys(fields):
        current, catalogue = getattr(configuration, field), getattr(variant, field)
        if current == catalogue:
            continue
        before[field] = _audit_value(current)
        after[field] = _audit_value(catalogue)
        setattr(configuration, field, catalogue)
    chosen = set(fields)
    configuration.overridden_fields = [f for f in (configuration.overridden_fields or []) if f not in chosen]
    configuration.updated_by = actor_id
    configuration.version += 1
    db.flush()

    record_audit_event(
        db,
        entity_type=_ENTITY_TYPE,
        entity_id=configuration.id,
        tenant_id=configuration.tenant_id,
        action="resync",
        actor_id=actor_id,
        before=before or None,
        after=after or None,
    )
    _publish(db, configuration, "configuration.updated")
    db.commit()
    return get_configuration_or_404(db, tenant_id=configuration.tenant_id, configuration_id=configuration.id)
