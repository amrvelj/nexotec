"""VehicleMdm service layer (WP-5 PR-3)."""

import datetime as dt
import re
import unicodedata
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.validators import PRE_ISO_VIN_CUTOFF, is_chassis_number
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.vehicle_mdm import (
    CatalogueMatchStatus,
    VehicleIdentifierKind,
    VehicleMdm,
    VehicleNumberSequence,
)
from app.vehicle.schemas.vehicle_mdm import VehicleMdmUpdate
from app.vehicle.services.stock_holding import VIN_CHANGE_NEEDS_VIN_CORRECTION, held_stock_item

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
    """The VIN record for `vin`. A chassis-number record is never found by
    its number alone (KAN-535: it is that number together with its make)."""

    return db.scalar(
        select(VehicleMdm).where(VehicleMdm.vin == vin, VehicleMdm.identifier_kind == VehicleIdentifierKind.VIN)
    )


# KAN-535 (Anto, 2026-11-13, option A): a make compared tolerantly — case,
# spaces, hyphens and accents ignored, nothing else — so «Mercedes-Benz» and
# «mercedes benz» are one make and «Mercedes» another.
_MAKE_KEY_IGNORED = re.compile(r"[\s\-\u2010-\u2015]+")
_MAKE_KEY_LENGTH = 255  # VehicleMdm.chassis_make_key


def chassis_make_key(make: str | None) -> str | None:
    """The key a chassis-number record's make is compared by; None for no
    make (nothing but spaces and hyphens included)."""

    if make is None:
        return None
    decomposed = unicodedata.normalize("NFKD", make)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    key = _MAKE_KEY_IGNORED.sub("", unaccented).casefold()
    return key[:_MAKE_KEY_LENGTH] or None


def get_vehicle_mdm_by_chassis_number(db: Session, *, chassis_number: str, make_key: str) -> VehicleMdm | None:
    """The chassis-number record for this number and make key (KAN-535)."""

    return db.scalar(
        select(VehicleMdm).where(
            VehicleMdm.vin == chassis_number,
            VehicleMdm.identifier_kind == VehicleIdentifierKind.CHASSIS_NUMBER,
            VehicleMdm.chassis_make_key == make_key,
        )
    )


def require_chassis_number_identity(
    *, chassis_number: str, make: str | None, first_registration_date: dt.date | None
) -> str:
    """The make key of a pre-1981 car's chassis-number record, after the
    rules a record needs (KAN-535): a chassis number (never a VIN: that is
    a VIN record), a first registration before 1981-01-01 (KAN-353's rule)
    and a make. 422 with a `reason` otherwise; returns the make key."""

    if not is_chassis_number(chassis_number):
        raise UnprocessableEntityError(
            f"'{chassis_number}' is not a chassis number.", details={"field": "vin", "reason": "invalid_vin"}
        )
    if first_registration_date is None or first_registration_date >= PRE_ISO_VIN_CUTOFF:
        raise UnprocessableEntityError(
            "A chassis number that is not a 17-character VIN is accepted only for a car first registered "
            "before 1981.",
            details={"field": "vin", "reason": "chassis_number_requires_pre_1981_registration"},
        )
    make_key = chassis_make_key(make)
    if make_key is None:
        raise UnprocessableEntityError(
            "A car with a chassis number is told apart by its make: enter the make on its configuration.",
            details={"field": "make", "reason": "chassis_number_requires_make"},
        )
    return make_key


def create_vehicle_mdm(
    db: Session,
    *,
    vin: str,
    catalogue_variant_id: uuid.UUID | None,
    stammnummer: str | None = None,
    type_approval_number: str | None = None,
    first_registration_date: dt.date | None = None,
    actor_id: uuid.UUID | None = None,
    chassis_make_key: str | None = None,
) -> VehicleMdm:
    """FR-V-15's "a VIN that already exists is not a validation error"
    rule is enforced by the CALLER (the API layer resolves the existing
    record and offers to open it, per the brief) — this function's own
    IntegrityError handling is the last-resort guard against a race
    between that check and this insert, not the primary mechanism.

    With `chassis_make_key` (KAN-535) the record is a pre-1981 car's
    chassis-number record, its identity the number and that key; the caller
    has checked both (`require_chassis_number_identity`).
    """

    vehicle = VehicleMdm(
        vin=vin,
        identifier_kind=(
            VehicleIdentifierKind.VIN if chassis_make_key is None else VehicleIdentifierKind.CHASSIS_NUMBER
        ),
        chassis_make_key=chassis_make_key,
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


def create_or_get_vehicle_mdm_by_chassis_number(
    db: Session,
    *,
    chassis_number: str,
    make: str | None,
    first_registration_date: dt.date | None,
    actor_id: uuid.UUID | None = None,
) -> tuple[VehicleMdm, bool]:
    """KAN-535 (ADR-103 as amended): a pre-1981 car's chassis-number record
    — found by its number and make key, or created with its first
    registration — FR-V-15's "the same car" for a car without a VIN.
    Refused (422) without the rules `require_chassis_number_identity` names.
    The insert race is the caller's, as for a VIN (`vin_resolution.py`)."""

    make_key = require_chassis_number_identity(
        chassis_number=chassis_number, make=make, first_registration_date=first_registration_date
    )
    existing = get_vehicle_mdm_by_chassis_number(db, chassis_number=chassis_number, make_key=make_key)
    if existing is not None:
        return existing, False
    vehicle = create_vehicle_mdm(
        db, vin=chassis_number, catalogue_variant_id=None, first_registration_date=first_registration_date,
        actor_id=actor_id, chassis_make_key=make_key,
    )
    return vehicle, True


def update_vehicle_mdm(
    db: Session,
    *,
    vehicle: VehicleMdm,
    data: VehicleMdmUpdate,
    actor_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
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

    **A car the caller's Betrieb holds in stock keeps its VIN here** (KAN-667;
    Anto, 2026-11-13, option A): its VIN is corrected on the stock car with
    «VIN korrigieren» (KAN-557), which rewrites the stock item, this record and
    the configuration together — a change here alone would leave the stock
    item on the old VIN. `tenant_id` is the caller's Betrieb, from the token;
    only its own stock counts (`services/stock_holding.py`, rule 7), so
    another Betrieb's stock never refuses the change nor shows in the
    refusal. Refused 409 `vin_change_needs_vin_correction` with the stock
    item's `stockItemId` and `stockNumber`, before anything is written; a
    PATCH that leaves the VIN as it is is not refused.
    """

    summary_before = summary_fields(vehicle)
    changes = data.model_dump(exclude_unset=True)
    changed = {field: value for field, value in changes.items() if getattr(vehicle, field) != value}
    if vehicle.identifier_kind == VehicleIdentifierKind.CHASSIS_NUMBER:
        _refuse_chassis_number_identity_change(changed)
    if "vin" in changed:
        held = held_stock_item(db, tenant_id=tenant_id, vehicle_id=vehicle.id)
        if held is not None:
            raise ConflictError(
                "This car is in your stock: correct its VIN on the stock car («VIN korrigieren»).",
                details={
                    "reason": VIN_CHANGE_NEEDS_VIN_CORRECTION,
                    "field": "vin",
                    "stockItemId": str(held.stock_item_id),
                    "stockNumber": held.stock_number,
                },
            )
    before = {_AUDIT_NAMES[field]: _audit_value(getattr(vehicle, field)) for field in changed}
    for field, value in changes.items():
        setattr(vehicle, field, value)
    vehicle.updated_by = actor_id
    vehicle.version += 1

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        constraint = _violated_constraint(exc)
        duplicate = _UNIQUE_FIELDS.get(constraint) if constraint is not None else None
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


def _refuse_chassis_number_identity_change(changed: dict[str, object]) -> None:
    """KAN-535: a chassis-number record's identity is its number, its make
    and its pre-1981 first registration. Its number is not changed here
    (`chassis_number_identity_fixed`): Anto (2026-11-14, option B) lets a
    bought oldtimer's chassis number be corrected only through Stock's «VIN
    korrigieren» (`vin_correction.py`), which carries the stock item and the
    configuration along — refused here whether or not the caller's Betrieb
    holds the car. Its first registration stays before 1981
    (`chassis_number_requires_pre_1981_registration`)."""

    if "vin" in changed:
        raise UnprocessableEntityError(
            "This car is identified by its chassis number, which is corrected only on a stock car («VIN korrigieren»).",
            details={"field": "vin", "reason": "chassis_number_identity_fixed"},
        )
    if "first_registration_date" in changed:
        registered = changed["first_registration_date"]
        if not isinstance(registered, dt.date) or registered >= PRE_ISO_VIN_CUTOFF:
            raise UnprocessableEntityError(
                "A car identified by its chassis number was first registered before 1981.",
                details={"field": "firstRegistrationDate", "reason": "chassis_number_requires_pre_1981_registration"},
            )


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
    "uq_vehicle_mdm_vin": ("vin", "VIN"),
    "vehicle_mdm_vin_key": ("vin", "VIN"),  # SQLite's message, mapped below
    "vehicle_mdm_stammnummer_key": ("stammnummer", "Stammnummer"),
}


def _violated_constraint(exc: IntegrityError) -> str | None:
    """Postgres names the constraint; SQLite (the fast lane only, ADR-011)
    names the column — «UNIQUE constraint failed: vehicle_mdm.vin»."""

    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if name is not None:
        return str(name)
    failed = re.search(r"UNIQUE constraint failed: vehicle_mdm\.(\w+)", str(exc.orig))
    return f"vehicle_mdm_{failed.group(1)}_key" if failed else None


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
