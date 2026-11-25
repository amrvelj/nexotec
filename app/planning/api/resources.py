"""The planner's resources in Dealer Administration (KAN-808, S7): the lists
(workplace types, technician types, qualifications), the resources (people
and workplaces) and a person's working hours.

Every route sits behind the router-level `aftersales` gate (ADR-221 T3;
D-A-04: 404 for everyone without the module, before any role check), is the
Betrieb's own (tenant match; the platform tenant 404) and depends on the
matrix row `planner_resources`: role aftersales and the manager read, the
manager writes (Anto, 2026-11-25). The session guard counts this router as
Dealer Administration, so a manager sets S7 up before activation.

Mutations of an entry or a resource take If-Match on its version; every
POST honours an Idempotency-Key (the route class), claimed only after the
role check has passed.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.errors import NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, require_entitlement, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.planning.models.resource import Resource, ResourceKind, WorkingHours
from app.planning.schemas.resources import (
    PersonCandidateList,
    PersonCandidateRead,
    PersonCreate,
    QualificationCreate,
    QualificationList,
    QualificationRead,
    QualificationUpdate,
    ResourceCreate,
    ResourceList,
    ResourceRead,
    ResourceUpdate,
    TechnicianTypeCreate,
    TechnicianTypeList,
    TechnicianTypeRead,
    TechnicianTypeUpdate,
    WorkingDay,
    WorkingHoursUpdate,
    WorkplaceTypeCreate,
    WorkplaceTypeList,
    WorkplaceTypeRead,
    WorkplaceTypeUpdate,
)
from app.planning.services import lists
from app.planning.services import resources as service
from app.planning.services import working_hours as hours_service
from app.planning.services.platform_refs import users_acting_in
from app.platform.public import is_platform_dealership

router = APIRouter(
    tags=["planner-resources"],
    route_class=IdempotentRoute,
    dependencies=[Depends(require_entitlement(ModuleCode.AFTERSALES))],
)

_BASE = "/dealerships/{dealership_id}/planning"
_READ = require_read("planner_resources")
_WRITE = require_write("planner_resources")


def guard(db: Session, dealership_id: uuid.UUID, principal: Principal) -> None:
    """The Betrieb's own and a dealer Betrieb — else 404."""

    require_tenant_match(dealership_id, principal)
    if is_platform_dealership(db, dealership_id):
        raise NotFoundError("Not found.")


# --- the lists --------------------------------------------------------------------------------


def _entry_fields(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "name": entry.name,
        "name_translations": entry.name_translations or {},
        "sort_order": entry.sort_order,
        "version": entry.version,
        "created_at": entry.created_at,
    }


def _workplace_type(entry: Any) -> WorkplaceTypeRead:
    return WorkplaceTypeRead(**_entry_fields(entry), is_sample=entry.is_sample, seed_code=entry.seed_code)


def _technician_type(entry: Any) -> TechnicianTypeRead:
    return TechnicianTypeRead(
        **_entry_fields(entry),
        is_sample=entry.is_sample,
        seed_code=entry.seed_code,
        rank=entry.rank,
        counted_time_factor=entry.counted_time_factor,
    )


def _qualification(entry: Any) -> QualificationRead:
    return QualificationRead(**_entry_fields(entry), is_mandatory=entry.is_mandatory)


def _changes(body: Any) -> dict[str, Any]:
    return {field: getattr(body, field) for field in body.model_fields_set}


def _create(spec: lists.ListSpec, db: Session, dealership_id: uuid.UUID, principal: Principal, body: Any) -> Any:
    guard(db, dealership_id, principal)
    fields = body.model_dump()
    entry = lists.create_entry(
        db,
        spec,
        dealership_id=dealership_id,
        actor_id=principal.user_id,
        name=fields.pop("name"),
        name_translations=fields.pop("name_translations"),
        sort_order=fields.pop("sort_order"),
        **fields,
    )
    db.commit()
    db.refresh(entry)
    return entry


def _change(
    spec: lists.ListSpec,
    db: Session,
    dealership_id: uuid.UUID,
    principal: Principal,
    entry_id: uuid.UUID,
    if_match: int,
    body: Any,
) -> Any:
    guard(db, dealership_id, principal)
    entry = lists.change_entry(
        db,
        spec,
        dealership_id=dealership_id,
        entry_id=entry_id,
        expected_version=if_match,
        actor_id=principal.user_id,
        changes=_changes(body),
    )
    db.commit()
    db.refresh(entry)
    return entry


def _confirm(
    spec: lists.ListSpec, db: Session, dealership_id: uuid.UUID, principal: Principal, entry_id: uuid.UUID, if_match: int
) -> Any:
    guard(db, dealership_id, principal)
    entry = lists.confirm_entry(
        db, spec, dealership_id=dealership_id, entry_id=entry_id, expected_version=if_match, actor_id=principal.user_id
    )
    db.commit()
    db.refresh(entry)
    return entry


def _delete(
    spec: lists.ListSpec, db: Session, dealership_id: uuid.UUID, principal: Principal, entry_id: uuid.UUID, if_match: int
) -> Response:
    guard(db, dealership_id, principal)
    lists.delete_entry(
        db, spec, dealership_id=dealership_id, entry_id=entry_id, expected_version=if_match, actor_id=principal.user_id
    )
    db.commit()
    return Response(status_code=204)


@router.get(f"{_BASE}/workplace-types", response_model=WorkplaceTypeList)
def list_workplace_types(
    dealership_id: uuid.UUID, principal: Principal = Depends(_READ), db: Session = Depends(get_db)
):
    guard(db, dealership_id, principal)
    entries = lists.list_entries(db, lists.WORKPLACE_TYPES, dealership_id=dealership_id)
    return WorkplaceTypeList(items=[_workplace_type(entry) for entry in entries])


@router.post(f"{_BASE}/workplace-types", response_model=WorkplaceTypeRead, status_code=201)
def create_workplace_type(
    dealership_id: uuid.UUID,
    body: WorkplaceTypeCreate,
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    return _workplace_type(_create(lists.WORKPLACE_TYPES, db, dealership_id, principal, body))


@router.patch(f"{_BASE}/workplace-types/{{entry_id}}", response_model=WorkplaceTypeRead)
def update_workplace_type(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: WorkplaceTypeUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """Any change ends a sample."""

    return _workplace_type(_change(lists.WORKPLACE_TYPES, db, dealership_id, principal, entry_id, if_match, body))


@router.post(f"{_BASE}/workplace-types/{{entry_id}}/confirmation", response_model=WorkplaceTypeRead)
def confirm_workplace_type(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """«Bestätigen»: a sample becomes the Betrieb's own entry as it is."""

    return _workplace_type(_confirm(lists.WORKPLACE_TYPES, db, dealership_id, principal, entry_id, if_match))


@router.delete(f"{_BASE}/workplace-types/{{entry_id}}", status_code=204)
def delete_workplace_type(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """Refused while a resource uses the entry (409 `planning_list_entry_in_use`)."""

    return _delete(lists.WORKPLACE_TYPES, db, dealership_id, principal, entry_id, if_match)


@router.get(f"{_BASE}/technician-types", response_model=TechnicianTypeList)
def list_technician_types(
    dealership_id: uuid.UUID, principal: Principal = Depends(_READ), db: Session = Depends(get_db)
):
    guard(db, dealership_id, principal)
    entries = lists.list_entries(db, lists.TECHNICIAN_TYPES, dealership_id=dealership_id)
    return TechnicianTypeList(items=[_technician_type(entry) for entry in entries])


@router.post(f"{_BASE}/technician-types", response_model=TechnicianTypeRead, status_code=201)
def create_technician_type(
    dealership_id: uuid.UUID,
    body: TechnicianTypeCreate,
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    return _technician_type(_create(lists.TECHNICIAN_TYPES, db, dealership_id, principal, body))


@router.patch(f"{_BASE}/technician-types/{{entry_id}}", response_model=TechnicianTypeRead)
def update_technician_type(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: TechnicianTypeUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """Any change ends a sample; `rank` and `countedTimeFactor` clear with null."""

    return _technician_type(_change(lists.TECHNICIAN_TYPES, db, dealership_id, principal, entry_id, if_match, body))


@router.post(f"{_BASE}/technician-types/{{entry_id}}/confirmation", response_model=TechnicianTypeRead)
def confirm_technician_type(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    return _technician_type(_confirm(lists.TECHNICIAN_TYPES, db, dealership_id, principal, entry_id, if_match))


@router.delete(f"{_BASE}/technician-types/{{entry_id}}", status_code=204)
def delete_technician_type(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    return _delete(lists.TECHNICIAN_TYPES, db, dealership_id, principal, entry_id, if_match)


@router.get(f"{_BASE}/qualifications", response_model=QualificationList)
def list_qualifications(
    dealership_id: uuid.UUID, principal: Principal = Depends(_READ), db: Session = Depends(get_db)
):
    guard(db, dealership_id, principal)
    entries = lists.list_entries(db, lists.QUALIFICATIONS, dealership_id=dealership_id)
    return QualificationList(items=[_qualification(entry) for entry in entries])


@router.post(f"{_BASE}/qualifications", response_model=QualificationRead, status_code=201)
def create_qualification(
    dealership_id: uuid.UUID,
    body: QualificationCreate,
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    return _qualification(_create(lists.QUALIFICATIONS, db, dealership_id, principal, body))


@router.patch(f"{_BASE}/qualifications/{{entry_id}}", response_model=QualificationRead)
def update_qualification(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: QualificationUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    return _qualification(_change(lists.QUALIFICATIONS, db, dealership_id, principal, entry_id, if_match, body))


@router.delete(f"{_BASE}/qualifications/{{entry_id}}", status_code=204)
def delete_qualification(
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    return _delete(lists.QUALIFICATIONS, db, dealership_id, principal, entry_id, if_match)


# --- resources --------------------------------------------------------------------------------


def _day(row: WorkingHours) -> WorkingDay:
    return WorkingDay(
        weekday=row.weekday,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        second_starts_at=row.second_starts_at,
        second_ends_at=row.second_ends_at,
    )


def _resource_reads(db: Session, dealership_id: uuid.UUID, resources: list[Resource]) -> list[ResourceRead]:
    ids = [resource.id for resource in resources]
    qualifications = service.qualifications_of(db, ids)
    hours = hours_service.working_hours_of(db, ids)
    # One hop for the screen's «deactivated» mark (ADR-221 T5).
    acting = users_acting_in(db, dealership_id=dealership_id) if any(r.user_id for r in resources) else {}
    return [
        ResourceRead(
            id=resource.id,
            kind=resource.kind,
            location_id=resource.location_id,
            location_label=resource.location_label,
            user_id=resource.user_id,
            user_label=resource.user_label,
            person_acts_here=(resource.user_id in acting) if resource.kind is ResourceKind.PERSON else None,
            name=resource.name,
            workplace_type_id=resource.workplace_type_id,
            technician_type_id=resource.technician_type_id,
            qualification_ids=qualifications[resource.id],
            working_hours=[_day(row) for row in hours[resource.id]],
            is_active=resource.is_active,
            sort_order=resource.sort_order,
            version=resource.version,
            created_at=resource.created_at,
        )
        for resource in resources
    ]


def _one(db: Session, dealership_id: uuid.UUID, resource_id: uuid.UUID) -> ResourceRead:
    resource = service.get_resource_or_404(db, dealership_id=dealership_id, resource_id=resource_id)
    return _resource_reads(db, dealership_id, [resource])[0]


@router.get(f"{_BASE}/resources", response_model=ResourceList)
def list_resources(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID | None = Query(default=None, alias="locationId"),
    kind: ResourceKind | None = Query(default=None),
    include_inactive: bool = Query(default=False, alias="includeInactive"),
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    """The Betrieb's resources in their order. A Betrieb has a few dozen: one page."""

    guard(db, dealership_id, principal)
    resources = service.list_resources(
        db, dealership_id=dealership_id, location_id=location_id, kind=kind, include_inactive=include_inactive
    )
    return ResourceList(items=_resource_reads(db, dealership_id, resources))


@router.post(f"{_BASE}/resources", response_model=ResourceRead, status_code=201)
def create_resource(
    dealership_id: uuid.UUID,
    body: ResourceCreate,
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """A person (a user who works in the business, once) or a workplace."""

    guard(db, dealership_id, principal)
    if isinstance(body, PersonCreate):
        resource = service.create_person(
            db,
            dealership_id=dealership_id,
            actor_id=principal.user_id,
            user_id=body.user_id,
            location_id=body.location_id,
            technician_type_id=body.technician_type_id,
            qualification_ids=body.qualification_ids,
            sort_order=body.sort_order,
        )
    else:
        resource = service.create_workplace(
            db,
            dealership_id=dealership_id,
            actor_id=principal.user_id,
            name=body.name,
            location_id=body.location_id,
            workplace_type_id=body.workplace_type_id,
            sort_order=body.sort_order,
        )
    db.commit()
    return _one(db, dealership_id, resource.id)


@router.get(f"{_BASE}/resources/{{resource_id}}", response_model=ResourceRead)
def get_resource(
    dealership_id: uuid.UUID,
    resource_id: uuid.UUID,
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    guard(db, dealership_id, principal)
    return _one(db, dealership_id, resource_id)


@router.patch(f"{_BASE}/resources/{{resource_id}}", response_model=ResourceRead)
def update_resource(
    dealership_id: uuid.UUID,
    resource_id: uuid.UUID,
    body: ResourceUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """Changes, deactivates (`isActive: false`: out of the planner, kept for
    its bookings) or reactivates a resource."""

    guard(db, dealership_id, principal)
    service.change_resource(
        db,
        dealership_id=dealership_id,
        resource_id=resource_id,
        expected_version=if_match,
        actor_id=principal.user_id,
        changes=_changes(body),
    )
    db.commit()
    return _one(db, dealership_id, resource_id)


@router.put(f"{_BASE}/resources/{{resource_id}}/working-hours", response_model=ResourceRead)
def replace_working_hours(
    dealership_id: uuid.UUID,
    resource_id: uuid.UUID,
    body: WorkingHoursUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """A person's whole week, under the resource's version."""

    guard(db, dealership_id, principal)
    hours_service.replace_working_hours(
        db,
        dealership_id=dealership_id,
        resource_id=resource_id,
        expected_version=if_match,
        actor_id=principal.user_id,
        days=[
            hours_service.WorkingDay(
                weekday=day.weekday,
                starts_at=day.starts_at,
                ends_at=day.ends_at,
                second_starts_at=day.second_starts_at,
                second_ends_at=day.second_ends_at,
            )
            for day in body.days
        ],
    )
    db.commit()
    return _one(db, dealership_id, resource_id)


@router.get(f"{_BASE}/person-candidates", response_model=PersonCandidateList)
def list_person_candidates(
    dealership_id: uuid.UUID, principal: Principal = Depends(_WRITE), db: Session = Depends(get_db)
):
    """Who may become a person resource: the users who work in the business
    and are none yet, by name. Read under the write row: only the one who
    creates resources needs the list."""

    guard(db, dealership_id, principal)
    taken = service.user_ids_with_a_resource(db, dealership_id=dealership_id)
    return PersonCandidateList(
        items=[
            PersonCandidateRead(user_id=user_id, display_name=label)
            for user_id, label in users_acting_in(db, dealership_id=dealership_id).items()
            if user_id not in taken
        ]
    )
