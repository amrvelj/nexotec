"""Vehicle service layer: global (no tenant_id) CRUD, custody-event
recording, and reference-data value_code validation for the spec-fields
issue #3 seeded (vehicle_type, fuel_type, body_style, drivetrain,
transmission, exterior_color, interior_color).

Custody/status authorization (who may relinquish or claim custody, who may
change `status`) is decided by the API router, not here — same separation
the rest of the codebase uses (services stay auth-agnostic; routers own
Principal-based checks). See app/api/v1/vehicles.py.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams, build_page, paginate_query
from app.platform.public import get_reference_list_or_404, get_reference_value_or_404
from app.vehicle.models.vehicle import CustodyEventType, Vehicle, VehicleCustodyEvent, VehicleStatus
from app.vehicle.schemas.vehicle import VehicleCreate, VehicleUpdate

_REFERENCE_FIELDS = (
    "vehicle_type",
    "fuel_type",
    "body_style",
    "drivetrain",
    "transmission",
    "exterior_color",
    "interior_color",
)
_AUDITED_FIELDS = {"registration_status", "registration_canton", "odometer", "status"}
_TERMINAL_STATUSES = {VehicleStatus.TOTALED, VehicleStatus.SCRAPPED}


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _validate_reference_fields(db: Session, values: dict[str, Any]) -> None:
    for field in _REFERENCE_FIELDS:
        value_code = values.get(field)
        if value_code is None:
            continue
        ref_list = get_reference_list_or_404(db, field)
        get_reference_value_or_404(db, list_id=ref_list.id, value_code=value_code)


def get_vehicle_or_404(db: Session, vehicle_id: uuid.UUID) -> Vehicle:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise NotFoundError(f"Vehicle {vehicle_id} was not found.")
    return vehicle


def get_vehicle_by_vin_or_404(db: Session, vin: str) -> Vehicle:
    vehicle = db.scalar(select(Vehicle).where(Vehicle.vin == vin))
    if vehicle is None:
        raise NotFoundError(f"Vehicle with VIN '{vin}' was not found.")
    return vehicle


def list_vehicles(
    db: Session,
    *,
    status: VehicleStatus | None,
    custodian_partner_id: uuid.UUID | None,
    updated_since: dt.datetime | None,
    params: PageParams,
) -> tuple[list[Vehicle], str | None]:
    stmt = select(Vehicle)
    if status is not None:
        stmt = stmt.where(Vehicle.status == status)
    if custodian_partner_id is not None:
        stmt = stmt.where(Vehicle.current_custodian_partner_id == custodian_partner_id)
    if updated_since is not None:
        stmt = stmt.where(Vehicle.updated_at >= updated_since)
    stmt = paginate_query(stmt, model=Vehicle, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def create_vehicle(
    db: Session, *, data: VehicleCreate, custodian_partner_id: uuid.UUID, actor_id: uuid.UUID
) -> Vehicle:
    """Creating a Vehicle always establishes the creating dealer as the
    first custodian via an implicit `acquired` event — see VehicleCreate's
    docstring for why (fills the original spec's open question 8).
    """

    field_values = data.model_dump(exclude={"vin"})
    _validate_reference_fields(db, field_values)

    vehicle = Vehicle(
        vin=data.vin,
        current_custodian_partner_id=custodian_partner_id,
        created_by=actor_id,
        updated_by=actor_id,
        **field_values,
    )
    db.add(vehicle)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"A vehicle with VIN '{data.vin}' already exists.", details={"vin": data.vin}) from exc

    db.add(
        VehicleCustodyEvent(
            vehicle_id=vehicle.id,
            partner_id=custodian_partner_id,
            event_type=CustodyEventType.ACQUIRED,
            event_date=utcnow(),
            created_by=actor_id,
        )
    )

    record_audit_event(
        db,
        entity_type="vehicle",
        entity_id=vehicle.id,
        # tenant_id = the creating (and now custodian) dealer, not None —
        # this is what GET /v1/vehicles/{id}/audit-log row-filters on,
        # same partner_id-based scoping as .../custody-events (see that
        # endpoint's docstring for the visibility rule this implements).
        tenant_id=custodian_partner_id,
        action="create",
        actor_id=actor_id,
        after={
            "vin": vehicle.vin,
            "status": _plain(vehicle.status),
            "registrationStatus": _plain(vehicle.registration_status),
            "odometer": vehicle.odometer,
            "currentCustodianPartnerId": str(custodian_partner_id),
        },
    )
    db.commit()
    db.refresh(vehicle)
    return vehicle


def update_vehicle(
    db: Session, *, vehicle: Vehicle, data: VehicleUpdate, actor_id: uuid.UUID, actor_tenant_id: uuid.UUID
) -> Vehicle:
    if vehicle.status in _TERMINAL_STATUSES and data.status is not None and data.status != vehicle.status:
        raise ConflictError(
            f"Vehicle status '{vehicle.status.value}' is terminal and cannot be changed.",
            details={"currentStatus": vehicle.status.value},
        )

    changes = data.model_dump(exclude_unset=True)
    _validate_reference_fields(db, changes)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        current = getattr(vehicle, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _plain(current)
            after[field] = _plain(value)
        setattr(vehicle, field, value)

    vehicle.updated_by = actor_id
    vehicle.version += 1

    if before or after:
        record_audit_event(
            db,
            entity_type="vehicle",
            entity_id=vehicle.id,
            # tenant_id = the editing dealer's own tenant, not the current
            # custodian — PATCH on static spec fields is open to any
            # dealer_admin/inventory user, not custodian-gated, so "who did
            # this" is the only stable scoping concept for row-filtering.
            tenant_id=actor_tenant_id,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )

    db.commit()
    db.refresh(vehicle)
    return vehicle


def list_custody_events(
    db: Session, *, vehicle_id: uuid.UUID, tenant_filter: uuid.UUID | None, params: PageParams
) -> tuple[list[VehicleCustodyEvent], str | None]:
    """tenant_filter=None means "any tenant" (platform_admin sees the full
    chain); otherwise row-filtered to that tenant's own events (Swiss
    addendum Round 3 visibility rule).
    """

    stmt = select(VehicleCustodyEvent).where(VehicleCustodyEvent.vehicle_id == vehicle_id)
    if tenant_filter is not None:
        stmt = stmt.where(VehicleCustodyEvent.partner_id == tenant_filter)
    stmt = paginate_query(stmt, model=VehicleCustodyEvent, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def has_custody_event_for_tenant(db: Session, *, vehicle_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Backs the `status` visibility rule's R1 refinement (PM/CTO,
    2026-08-06): a dealer who has ever appeared as `partner_id` in this
    vehicle's custody history can see `status`, even after it's no longer
    their custody (e.g. the selling dealer, right after their own sale
    clears current_custodian_partner_id). Cheap indexed EXISTS — see
    ix_vehicle_custody_event_vehicle_id_partner_id — not a full fetch, since
    this runs on every Vehicle serialization.
    """

    stmt = (
        select(VehicleCustodyEvent.id)
        .where(VehicleCustodyEvent.vehicle_id == vehicle_id, VehicleCustodyEvent.partner_id == tenant_id)
        .limit(1)
    )
    return db.scalar(stmt) is not None


def create_custody_event(
    db: Session,
    *,
    vehicle: Vehicle,
    event_type: CustodyEventType,
    partner_id: uuid.UUID,
    event_date: dt.datetime | None,
    transaction_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    commit: bool = True,
) -> VehicleCustodyEvent:
    """commit=False lets a caller (complete_transaction) fold this
    function's Vehicle/custody-event/audit mutations into its own single
    db.commit(), instead of this committing on its own — see that
    function's docstring for why (CTO review, 2026-08-06). Direct endpoint
    callers keep the default commit=True, unchanged.
    """
    event = VehicleCustodyEvent(
        vehicle_id=vehicle.id,
        partner_id=partner_id,
        event_type=event_type,
        event_date=event_date or utcnow(),
        transaction_id=transaction_id,
        created_by=actor_id,
    )
    db.add(event)

    before_custodian = vehicle.current_custodian_partner_id
    new_custodian = None if event_type == CustodyEventType.SOLD else partner_id
    vehicle.current_custodian_partner_id = new_custodian
    vehicle.updated_by = actor_id
    vehicle.version += 1

    record_audit_event(
        db,
        entity_type="vehicle",
        entity_id=vehicle.id,
        # Same partner_id used on the custody event row itself — a dealer
        # sees this audit entry iff they'd also see the custody event via
        # .../custody-events (same row-filtering rule, same asymmetry: the
        # dealer relinquishing custody in a transfer doesn't see it either,
        # consistent with that endpoint).
        tenant_id=partner_id,
        action="custody_event",
        actor_id=actor_id,
        before={"currentCustodianPartnerId": str(before_custodian) if before_custodian else None},
        after={
            "currentCustodianPartnerId": str(new_custodian) if new_custodian else None,
            "eventType": _plain(event_type),
        },
    )
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return event
