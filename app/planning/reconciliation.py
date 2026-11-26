"""Planning's outbound references (KAN-808; rule 10, ADR-107): the platform
ids its rows hold. Read-only — see app.core.reconciliation for the
mechanism; a finding is reported, never repaired.

- every row names a Betrieb that exists (`tenant_id`, ADR-221 T7);
- a resource names a location and, for a person, a user that exist;
- an active person resource whose user is deactivated: the planner still
  offers someone who has left (the manager deactivates the resource);
- a booking's release and an overruled warning name a user that exists;
- the actor columns of every table that has them (KAN-243).

A booking's caller references (an order, a job, a car) belong to contexts
above planning, which planning cannot read: the caller's own reconciliation
checks them through a planning.public builder (ADR-221 T6).
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.planning.models.booking import Booking, BookingResource, OverruledWarning
from app.planning.models.lists import Qualification, TechnicianType, WorkplaceType
from app.planning.models.resource import Resource, ResourceKind, ResourceQualification, WorkingHours
from app.planning.models.starting_set import StartingSet
from app.platform.public import (
    actor_reference_checks,
    dealership_reference_check,
    location_reference_check,
    user_is_deactivated,
    user_reference_check,
)

CONTEXT = "planning"

# Every planning table with created_by / updated_by (KAN-243).
ACTOR_TABLES = (WorkplaceType, TechnicianType, Qualification, Resource, Booking)


def _tenant_checks() -> list[ReferenceCheck | StateCheck]:
    return [
        dealership_reference_check(
            label=f"{model.__tablename__}.tenant_id -> dealership.id",
            source_model=model,
            source_row_id_column=model.id,
            source_fk_column=model.tenant_id,
        )
        for model in (
            WorkplaceType,
            TechnicianType,
            Qualification,
            Resource,
            ResourceQualification,
            WorkingHours,
            Booking,
            BookingResource,
            OverruledWarning,
            StartingSet,
        )
    ]


CHECKS: list[ReferenceCheck | StateCheck] = [
    *_tenant_checks(),
    location_reference_check(
        label="planning_resource.location_id -> location.id",
        source_model=Resource,
        source_row_id_column=Resource.id,
        source_fk_column=Resource.location_id,
    ),
    user_reference_check(
        label="planning_resource.user_id -> user.id",
        source_model=Resource,
        source_row_id_column=Resource.id,
        source_fk_column=Resource.user_id,
        nullable=True,  # a workplace names no user
    ),
    user_reference_check(
        label="planning_booking.released_by -> user.id",
        source_model=Booking,
        source_row_id_column=Booking.id,
        source_fk_column=Booking.released_by,
        nullable=True,  # a booked booking, or one released by the system
    ),
    user_reference_check(
        label="planning_overruled_warning.overruled_by -> user.id",
        source_model=OverruledWarning,
        source_row_id_column=OverruledWarning.id,
        source_fk_column=OverruledWarning.overruled_by,
    ),
    StateCheck(
        label="active person resource whose user is deactivated",
        source_model=Resource,
        source_row_id_column=Resource.id,
        where=lambda: (Resource.kind == ResourceKind.PERSON)
        & Resource.is_active.is_(True)
        & user_is_deactivated(Resource.user_id, correlate=Resource),
    ),
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
