"""VehicleMdm service layer (WP-5 PR-3)."""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleMdm, VehicleNumberSequence
from app.vehicle.schemas.vehicle_mdm import VehicleMdmUpdate

# KAN-186 — the fact another context's copy of a vehicle's label follows
# (customer's VehicleParty, KAN-84). Published in the same transaction as
# every write that changes a field `app.vehicle.public.VehicleSummary`
# carries, and only then.
SUMMARY_CHANGED_EVENT = "vehicle.vehicle_mdm.summary_changed"
_EVENT_PRODUCER = "vehicle"
_ENTITY_TYPE = "vehicle_mdm"


def allocate_vehicle_number(db: Session) -> str:
    """Allocate the next `F-000001`-style number. Global — unlike the
    per-group CustomerNumberSequence (app.customer.models.customer), a
    vehicle is a global fact (ADR-022), so there is exactly one counter,
    not one per group. Same row-lock-then-increment idiom: a failed
    transaction rolls back its increment, so its number is re-issued to
    the next caller (G-71); a committed number is never handed out twice.
    """

    row = db.get(VehicleNumberSequence, "GLOBAL", with_for_update=True)
    if row is None:
        # First use of this key. A concurrent first caller can insert the same
        # row: its commit turns our INSERT into a UniqueViolation (KAN-70). The
        # savepoint keeps the caller's transaction alive through that, and the
        # locked re-read below then waits for and takes the winner's row.
        # Flush the caller's own pending rows first, so that the except below
        # can only ever see this counter row's INSERT.
        db.flush()
        try:
            with db.begin_nested():
                db.add(VehicleNumberSequence(singleton_key="GLOBAL", next_value=1))
        except IntegrityError:
            pass  # the concurrent caller's row now exists; re-read it below
        row = db.get(VehicleNumberSequence, "GLOBAL", with_for_update=True)
        assert row is not None, "VehicleNumberSequence row missing after its first-use INSERT or the concurrent winner's"

    value = row.next_value
    row.next_value += 1
    db.flush()
    return f"F-{value:06d}"


def get_vehicle_mdm_or_404(db: Session, vehicle_id: uuid.UUID) -> VehicleMdm:
    vehicle = db.get(VehicleMdm, vehicle_id)
    if vehicle is None:
        raise NotFoundError(f"Vehicle {vehicle_id} was not found.")
    return vehicle


def get_vehicle_mdm_by_vin(db: Session, vin: str) -> VehicleMdm | None:
    return db.scalar(select(VehicleMdm).where(VehicleMdm.vin == vin))


def create_vehicle_mdm(
    db: Session,
    *,
    vin: str,
    catalogue_variant_id: uuid.UUID | None,
    stammnummer: str | None = None,
    type_approval_number: str | None = None,
    first_registration_date: dt.date | None = None,
    actor_id: uuid.UUID | None = None,
) -> VehicleMdm:
    """FR-V-15's "a VIN that already exists is not a validation error"
    rule is enforced by the CALLER (the API layer resolves the existing
    record and offers to open it, per the brief) — this function's own
    IntegrityError handling is the last-resort guard against a race
    between that check and this insert, not the primary mechanism.
    """

    vehicle = VehicleMdm(
        vin=vin,
        vehicle_number=allocate_vehicle_number(db),
        stammnummer=stammnummer,
        type_approval_number=type_approval_number,
        first_registration_date=first_registration_date,
        catalogue_variant_id=catalogue_variant_id,
        catalogue_match_status=(
            CatalogueMatchStatus.MATCHED if catalogue_variant_id is not None else CatalogueMatchStatus.UNVERIFIED
        ),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(vehicle)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"A vehicle with VIN '{vin}' already exists.", details={"vin": vin}) from exc

    db.commit()
    db.refresh(vehicle)
    return vehicle


def create_or_get_vehicle_mdm(
    db: Session,
    *,
    vin: str,
    catalogue_variant_id: uuid.UUID | None,
    stammnummer: str | None = None,
    type_approval_number: str | None = None,
    first_registration_date: dt.date | None = None,
    actor_id: uuid.UUID | None = None,
) -> tuple[VehicleMdm, bool]:
    """WP-5 PR-9, FR-V-15: "a VIN that already exists is not a validation
    error — it is the same car." Returns (vehicle, created) — created=False
    means the VIN already resolved to an existing record, returned as-is
    (never re-created, never silently merged with the new payload's other
    fields) so the caller can offer to open it. This is the actual
    enforcement point create_vehicle_mdm's own docstring describes.
    """

    existing = get_vehicle_mdm_by_vin(db, vin)
    if existing is not None:
        return existing, False

    vehicle = create_vehicle_mdm(
        db, vin=vin, catalogue_variant_id=catalogue_variant_id, stammnummer=stammnummer,
        type_approval_number=type_approval_number, first_registration_date=first_registration_date,
        actor_id=actor_id,
    )
    return vehicle, True


def update_vehicle_mdm(
    db: Session, *, vehicle: VehicleMdm, data: VehicleMdmUpdate, actor_id: uuid.UUID | None
) -> VehicleMdm:
    """The only PATCH path for vin/stammnummer/first_registration_date —
    nothing else in the codebase may write them (ADR-045). A mistyped VIN
    correction on an unverified record is permitted and audit-logged here
    like any other field (KAN-561: an `update` audit row with the actor and
    the changed fields before and after; a PATCH that changes nothing writes
    none, and `tenant_id` is None — vehicle-mdm is global, as the merge's
    row); re-running the matching waterfall against the
    corrected value is the caller's job (PR-6's match_vehicle), not this
    function's — keeping this a plain field update keeps the two concerns
    (persisting a correction, deciding what it implies) separable.

    A write that changes a summary field (a VIN correction; a first
    registration in another year, the summary's model year) publishes
    `vehicle.vehicle_mdm.summary_changed` in this same transaction
    (KAN-186); a refused write publishes nothing.
    """

    summary_before = summary_fields(vehicle)
    changes = data.model_dump(exclude_unset=True)
    changed = {field: value for field, value in changes.items() if getattr(vehicle, field) != value}
    before = {_AUDIT_NAMES[field]: _audit_value(getattr(vehicle, field)) for field in changed}
    for field, value in changes.items():
        setattr(vehicle, field, value)
    vehicle.updated_by = actor_id
    vehicle.version += 1

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        duplicate = _UNIQUE_FIELDS.get(_violated_constraint(exc))
        if duplicate is None:
            raise
        field, label = duplicate
        raise ConflictError(
            f"A vehicle with {label} '{changes.get(field)}' already exists.",
            details={_AUDIT_NAMES[field]: changes.get(field)},
        ) from exc

    if changed:
        record_audit_event(
            db,
            entity_type=_ENTITY_TYPE,
            entity_id=vehicle.id,
            tenant_id=None,
            action="update",
            actor_id=actor_id,
            before=before,
            after={_AUDIT_NAMES[field]: _audit_value(getattr(vehicle, field)) for field in changed},
        )
    publish_summary_changed_if_changed(db, vehicle=vehicle, before=summary_before)
    db.commit()
    db.refresh(vehicle)
    return vehicle


# The PATCH's fields under their camelCase API names — the audit row's keys
# and a duplicate refusal's `details` key (the configuration's audit rule).
_AUDIT_NAMES = {
    "vin": "vin",
    "stammnummer": "stammnummer",
    "type_approval_number": "typeApprovalNumber",
    "first_registration_date": "firstRegistrationDate",
    "vehicle_status": "vehicleStatus",
}

# A unique constraint the PATCH can hit -> the field it guards and its name in
# the refusal. Any other integrity error is not a duplicate and is raised as is.
_UNIQUE_FIELDS = {
    "vehicle_mdm_vin_key": ("vin", "VIN"),
    "vehicle_mdm_stammnummer_key": ("stammnummer", "Stammnummer"),
}


def _violated_constraint(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _audit_value(value: object) -> object:
    if isinstance(value, dt.date):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def summary_fields(vehicle: VehicleMdm) -> dict[str, object]:
    """The fields of `app.vehicle.public.VehicleSummary` (its id aside),
    under their camelCase event names — the one list the comparison and the
    payload both use, so a field added to the summary is added here."""

    return {
        "vin": vehicle.vin,
        "vehicleNumber": vehicle.vehicle_number,
        "make": vehicle.make,
        "model": vehicle.model,
        "modelYear": vehicle.model_year,
        "trim": vehicle.trim,
    }


def publish_summary_changed_if_changed(db: Session, *, vehicle: VehicleMdm, before: dict[str, object]) -> bool:
    """KAN-186 — adds `vehicle.vehicle_mdm.summary_changed` to the caller's
    transaction (flushed, never committed here) when a summary field
    differs from `before`; a write that changes none publishes nothing.
    Returns whether it published. A writer that changes
    `catalogue_variant_id` must expire `vehicle.catalogue_variant` first, or
    make/model/trim are read from the variant the session already holds.

    The payload has one shape: `vehicleId`, the record's `version` after
    the write, `changedFields` (the camelCase names that differ) and the
    whole `summary` as it now stands. `tenant_id` is None: vehicle-mdm is a
    global fact no Betrieb owns (ADR-022); a consumer applies its own scope
    (customer relabels every dealer group's party rows for the vehicle).
    """

    after = summary_fields(vehicle)
    changed = [field for field, value in after.items() if before.get(field) != value]
    if not changed:
        return False
    publish(
        db,
        OutboxEvent(
            event_type=SUMMARY_CHANGED_EVENT,
            tenant_id=None,
            producer=_EVENT_PRODUCER,
            aggregate_type=_ENTITY_TYPE,
            aggregate_id=vehicle.id,
            payload={
                "vehicleId": str(vehicle.id),
                "version": vehicle.version,
                "changedFields": changed,
                "summary": after,
            },
        ),
    )
    return True


def get_vehicle_equipment(db: Session, vehicle_id: uuid.UUID) -> dict:
    """WP-7 PR-8 (ADR-062) — the three separate concepts, read-only from
    here (inventory's publishing tab reads, never writes them; equipment
    is a fact about the car, edited wherever the vehicle's own identity
    fields are).
    """

    vehicle = get_vehicle_mdm_or_404(db, vehicle_id)
    return {
        "ausstattungCodes": vehicle.ausstattung_codes or [],
        "extras": vehicle.extras or [],
        "eigenschaften": vehicle.eigenschaften or [],
        "providerAusstattung": vehicle.provider_ausstattung or {},
    }


def has_current_energy_rating(db: Session, vehicle_id: uuid.UUID) -> bool:
    """WP-7 PR-8's "missing Energieetikette on a new car <2000km" blocking
    condition. Simplified to "any BFE rating year exists for this
    variant" — the full "current fleet year" resolution is
    ModelVariantEnergyRating's own domain (WP-5 PR-8), not something this
    single yes/no check needs to reimplement.
    """

    vehicle = get_vehicle_mdm_or_404(db, vehicle_id)
    if vehicle.catalogue_variant_id is None:
        return False
    return (
        db.scalar(
            select(ModelVariantEnergyRating.id).where(
                ModelVariantEnergyRating.model_variant_id == vehicle.catalogue_variant_id
            )
        )
        is not None
    )
