"""The Betrieb's planner resources (KAN-808, S7): people and workplaces,
each at one location.

- A person is a user who works in the Betrieb (`platform.public`), one
  person resource per user per Betrieb (Anto, 2026-11-25): 409
  `planning_person_already_a_resource` for a second one, a deactivated one
  included — reactivate it instead. A person has a technician type,
  qualifications and working hours (`working_hours.py`).
- A workplace has a name, unique at its location ignoring case (409
  `planning_workplace_name_taken`), and a workplace type.
- A field of the other kind is refused (422 `planning_person_only`,
  `planning_workplace_only`); a list entry of another Betrieb, or none, is
  422 `planning_list_entry_unknown`.
- The location must be one of the Betrieb's active locations when it is set
  (422 `unknown_location`, `planning_location_inactive`); the user must work
  in the Betrieb when the person is created or reactivated (422
  `planning_person_not_in_dealership`). Each label is copied then (ADR-221 T5).
- Deactivating sets `is_active` false: the resource leaves the planner and
  stays for every booking that names it. It is allowed while the resource
  holds bookings to come (Anto, 2026-11-25): each is marked «neu einplanen»
  with cause `resource_deactivated` and raises
  `planning.booking.marked_for_replanning`, under the Betrieb's booking lock
  (ADR-221 (1); taken after the master-data lock).

The caller checks the role and the Betrieb and commits. Every write is
audited and takes the Betrieb's master-data lock first.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.text_collation import TextSortKey
from app.planning.models.resource import Resource, ResourceKind, ResourceQualification
from app.planning.services import lists
from app.planning.services.bookings import mark_bookings_of_deactivated_resource
from app.planning.services.locks import lock_master_data
from app.planning.services.platform_refs import location_label_for, person_label_for

ENTITY_TYPE = "planning_resource"

# What a PATCH may name; the API passes the fields it was sent.
CHANGEABLE = (
    "name",
    "location_id",
    "workplace_type_id",
    "technician_type_id",
    "qualification_ids",
    "is_active",
    "sort_order",
)
_PERSON_ONLY = ("technician_type_id", "qualification_ids")
_WORKPLACE_ONLY = ("name", "workplace_type_id")


# --- reads ----------------------------------------------------------------------------------


def list_resources(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    location_id: uuid.UUID | None = None,
    kind: ResourceKind | None = None,
    include_inactive: bool = False,
) -> list[Resource]:
    stmt = select(Resource).where(Resource.tenant_id == dealership_id)
    if location_id is not None:
        stmt = stmt.where(Resource.location_id == location_id)
    if kind is not None:
        stmt = stmt.where(Resource.kind == kind)
    if not include_inactive:
        stmt = stmt.where(Resource.is_active.is_(True))
    return list(
        db.scalars(
            stmt.order_by(
                Resource.sort_order, TextSortKey(func.coalesce(Resource.name, Resource.user_label)), Resource.id
            )
        )
    )


def get_resource_or_404(
    db: Session, *, dealership_id: uuid.UUID, resource_id: uuid.UUID, lock: bool = False
) -> Resource:
    stmt = select(Resource).where(Resource.id == resource_id, Resource.tenant_id == dealership_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    resource = db.scalar(stmt)
    if resource is None:
        raise NotFoundError("The resource was not found.")
    return resource


def qualifications_of(db: Session, resource_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    found: dict[uuid.UUID, list[uuid.UUID]] = {resource_id: [] for resource_id in resource_ids}
    if not resource_ids:
        return found
    rows = db.execute(
        select(ResourceQualification.resource_id, ResourceQualification.qualification_id)
        .where(ResourceQualification.resource_id.in_(set(resource_ids)))
        .order_by(ResourceQualification.id)
    )
    for resource_id, qualification_id in rows:
        found[resource_id].append(qualification_id)
    return found


def user_ids_with_a_resource(db: Session, *, dealership_id: uuid.UUID) -> set[uuid.UUID]:
    rows = db.scalars(
        select(Resource.user_id).where(Resource.tenant_id == dealership_id, Resource.user_id.is_not(None))
    )
    return {user_id for user_id in rows if user_id is not None}


# --- checks ---------------------------------------------------------------------------------


def _refuse_unknown_entry() -> UnprocessableEntityError:
    return UnprocessableEntityError(
        "The list entry is not one of this business's.", details={"reason": "planning_list_entry_unknown"}
    )


def _check_entry(db: Session, spec: lists.ListSpec, dealership_id: uuid.UUID, entry_id: uuid.UUID | None) -> None:
    if entry_id is not None and not lists.entries_of_the_betrieb(
        db, spec, dealership_id=dealership_id, entry_ids={entry_id}
    ):
        raise _refuse_unknown_entry()


def _check_qualifications(db: Session, dealership_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    wanted = list(dict.fromkeys(ids))
    known = lists.entries_of_the_betrieb(db, lists.QUALIFICATIONS, dealership_id=dealership_id, entry_ids=set(wanted))
    if len(known) != len(wanted):
        raise _refuse_unknown_entry()
    return wanted


def _refuse_name_taken() -> ConflictError:
    return ConflictError(
        "The location already has a workplace of this name.", details={"reason": "planning_workplace_name_taken"}
    )


def _refuse_taken_name(
    db: Session, dealership_id: uuid.UUID, location_id: uuid.UUID, name: str, *, other_than: uuid.UUID | None = None
) -> None:
    stmt = select(Resource.id).where(
        Resource.tenant_id == dealership_id,
        Resource.location_id == location_id,
        func.lower(Resource.name) == name.lower(),
    )
    if other_than is not None:
        stmt = stmt.where(Resource.id != other_than)
    if db.scalar(stmt.limit(1)) is not None:
        raise _refuse_name_taken()


def _refuse_person_twice() -> ConflictError:
    return ConflictError(
        "This person already is a resource of the business.",
        details={"reason": "planning_person_already_a_resource"},
    )


def _audit(resource: Resource, qualification_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
    return {
        "kind": resource.kind.value,
        "locationId": str(resource.location_id),
        "userId": str(resource.user_id) if resource.user_id else None,
        "name": resource.name,
        "workplaceTypeId": str(resource.workplace_type_id) if resource.workplace_type_id else None,
        "technicianTypeId": str(resource.technician_type_id) if resource.technician_type_id else None,
        "qualificationIds": [str(q) for q in qualification_ids],
        "isActive": resource.is_active,
        "sortOrder": resource.sort_order,
    }


def _flush(db: Session, refusal: ConflictError) -> None:
    """The partial unique indexes back the checks for a writer that took no lock."""

    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise refusal from None


# --- writes ---------------------------------------------------------------------------------


def create_person(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    user_id: uuid.UUID,
    location_id: uuid.UUID,
    technician_type_id: uuid.UUID | None = None,
    qualification_ids: Sequence[uuid.UUID] = (),
    sort_order: int = 0,
) -> Resource:
    lock_master_data(db, dealership_id)
    user_label = person_label_for(db, dealership_id=dealership_id, user_id=user_id)
    location_label = location_label_for(db, dealership_id=dealership_id, location_id=location_id)
    _check_entry(db, lists.TECHNICIAN_TYPES, dealership_id, technician_type_id)
    qualifications = _check_qualifications(db, dealership_id, qualification_ids)
    if user_id in user_ids_with_a_resource(db, dealership_id=dealership_id):
        raise _refuse_person_twice()
    now = utcnow()
    resource = Resource(
        tenant_id=dealership_id,
        kind=ResourceKind.PERSON,
        location_id=location_id,
        location_label=location_label,
        location_label_refreshed_at=now,
        user_id=user_id,
        user_label=user_label,
        user_label_refreshed_at=now,
        technician_type_id=technician_type_id,
        is_active=True,
        sort_order=sort_order,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(resource)
    _flush(db, _refuse_person_twice())
    _write_qualifications(db, resource, qualifications)
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=resource.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_audit(resource, qualifications),
    )
    return resource


def create_workplace(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    name: str,
    location_id: uuid.UUID,
    workplace_type_id: uuid.UUID | None = None,
    sort_order: int = 0,
) -> Resource:
    cleaned = lists.clean_name(name)
    lock_master_data(db, dealership_id)
    location_label = location_label_for(db, dealership_id=dealership_id, location_id=location_id)
    _check_entry(db, lists.WORKPLACE_TYPES, dealership_id, workplace_type_id)
    _refuse_taken_name(db, dealership_id, location_id, cleaned)
    resource = Resource(
        tenant_id=dealership_id,
        kind=ResourceKind.WORKPLACE,
        location_id=location_id,
        location_label=location_label,
        location_label_refreshed_at=utcnow(),
        name=cleaned,
        workplace_type_id=workplace_type_id,
        is_active=True,
        sort_order=sort_order,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(resource)
    _flush(db, _refuse_name_taken())
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=resource.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_audit(resource, []),
    )
    return resource


def _write_qualifications(db: Session, resource: Resource, qualification_ids: Sequence[uuid.UUID]) -> None:
    db.execute(delete(ResourceQualification).where(ResourceQualification.resource_id == resource.id))
    for qualification_id in qualification_ids:
        db.add(
            ResourceQualification(tenant_id=resource.tenant_id, resource_id=resource.id, qualification_id=qualification_id)
        )
    db.flush()


def _refuse_other_kind(resource: Resource, changes: dict[str, Any]) -> None:
    """A field of the other kind, sent with a value, is refused; sent empty it
    says what the resource already is."""

    def sent(field: str) -> bool:
        value = changes.get(field)
        return field in changes and value is not None and value != [] and value != ()

    if resource.kind is ResourceKind.WORKPLACE and any(sent(field) for field in _PERSON_ONLY):
        raise UnprocessableEntityError(
            "A technician type and qualifications are a person's.", details={"reason": "planning_person_only"}
        )
    if resource.kind is ResourceKind.PERSON and any(sent(field) for field in _WORKPLACE_ONLY):
        raise UnprocessableEntityError(
            "A name and a workplace type are a workplace's.", details={"reason": "planning_workplace_only"}
        )


def change_resource(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    resource_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
    changes: dict[str, Any],
) -> Resource:
    """Applies the fields in `changes` (the PATCH's fields set; see
    `CHANGEABLE`). A PATCH that changes nothing writes nothing."""

    unknown = set(changes) - set(CHANGEABLE)
    if unknown:
        raise TypeError(f"a resource has no {sorted(unknown)}")
    lock_master_data(db, dealership_id)
    resource = get_resource_or_404(db, dealership_id=dealership_id, resource_id=resource_id, lock=True)
    check_version(resource.version, expected_version, entity_name="Resource")
    _refuse_other_kind(resource, changes)
    held = qualifications_of(db, [resource.id])[resource.id]
    before = _audit(resource, held)
    now = utcnow()

    location_id = changes.get("location_id")
    if location_id is not None and location_id != resource.location_id:
        resource.location_label = location_label_for(db, dealership_id=dealership_id, location_id=location_id)
        resource.location_label_refreshed_at = now
        resource.location_id = location_id
    if resource.kind is ResourceKind.WORKPLACE:
        if changes.get("name") is not None:
            name = lists.clean_name(changes["name"])
            _refuse_taken_name(db, dealership_id, resource.location_id, name, other_than=resource.id)
            resource.name = name
        elif "location_id" in changes:
            # The same name may already be taken at the new location.
            _refuse_taken_name(db, dealership_id, resource.location_id, resource.name or "", other_than=resource.id)
        if "workplace_type_id" in changes:
            _check_entry(db, lists.WORKPLACE_TYPES, dealership_id, changes["workplace_type_id"])
            resource.workplace_type_id = changes["workplace_type_id"]
    else:
        if "technician_type_id" in changes:
            _check_entry(db, lists.TECHNICIAN_TYPES, dealership_id, changes["technician_type_id"])
            resource.technician_type_id = changes["technician_type_id"]
        if changes.get("qualification_ids") is not None:
            held = _check_qualifications(db, dealership_id, changes["qualification_ids"])
    deactivated = False
    if changes.get("is_active") is not None and changes["is_active"] != resource.is_active:
        deactivated = not changes["is_active"]
        if changes["is_active"]:
            # Back in the planner only at an active location of the Betrieb (422
            # `planning_location_inactive`), and a person only while they still work here.
            resource.location_label = location_label_for(
                db, dealership_id=dealership_id, location_id=resource.location_id
            )
            resource.location_label_refreshed_at = now
            if resource.kind is ResourceKind.PERSON and resource.user_id is not None:
                resource.user_label = person_label_for(db, dealership_id=dealership_id, user_id=resource.user_id)
                resource.user_label_refreshed_at = now
        resource.is_active = changes["is_active"]
    if changes.get("sort_order") is not None:
        resource.sort_order = changes["sort_order"]

    after = _audit(resource, held)
    if after == before:
        return resource
    if after["qualificationIds"] != before["qualificationIds"]:
        _write_qualifications(db, resource, held)
    resource.version += 1
    resource.updated_by = actor_id
    _flush(db, _refuse_name_taken())
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=resource.id,
        tenant_id=dealership_id,
        action="update",
        actor_id=actor_id,
        before=before,
        after=after,
    )
    if deactivated:
        mark_bookings_of_deactivated_resource(
            db, dealership_id=dealership_id, resource_id=resource.id, actor_id=actor_id
        )
    return resource
