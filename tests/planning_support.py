"""KAN-808 PR-1c test support: a Betrieb with planner resources, the planner's
settings set, and short ways to book through `planning.public`.

Times are local wall-clock times in the session zone (Europe/Zurich) on a
Monday, `at(9)` is 09:00 that day; the instants the planner stores are UTC.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.relative_dates import session_zone
from app.planning import public as planning
from app.planning.services import resources as resource_service
from app.planning.services import working_hours as hours_service
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.services import setting_store
from tests import setup_checklist_support as s
from tests.identity_support import create_dealership

# A Monday in winter time (UTC+1).
MONDAY = dt.date(2027, 3, 1)


def at(hour: int, minute: int = 0, *, day: dt.date = MONDAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=session_zone())


def set_setting(db: Session, dealership_id: uuid.UUID, key: str, minutes: int | None, *, location_id=None) -> None:
    version = setting_store.get_slot(db, dealership_id=dealership_id, location_id=location_id, key=key).version
    setting_store.change_setting(
        db,
        dealership_id=dealership_id,
        location_id=location_id,
        key=key,
        value=setting_store.NOT_SET if minutes is None else Decimal(minutes),
        expected_version=version,
        actor_id=None,
    )
    db.commit()


@dataclass
class Workshop:
    dealership_id: uuid.UUID
    location_id: uuid.UUID
    actor_id: uuid.UUID
    actor_name: str
    lift_1: uuid.UUID
    lift_2: uuid.UUID
    lea: uuid.UUID  # a person with working hours Mo–Fr 07:30–12:00 and 13:00–17:00
    max: uuid.UUID  # a person with no working hours entered

    def keys(self) -> str:
        return uuid.uuid4().hex


def workshop(client: TestClient, db: Session, *, grid: int | None = 15, slot: int | None = 30) -> Workshop:
    dealership_id = uuid.UUID(create_dealership(client))
    location_id = db.scalar(select(Location.id).where(Location.tenant_id == dealership_id))
    boss = s.manager(client, db, dealership_id, isDealerManager=True, firstName="Anna", lastName="Chef")
    lea_user = s.manager(client, db, dealership_id, isDealerManager=False, firstName="Lea", lastName="Meier")
    max_user = s.manager(client, db, dealership_id, isDealerManager=False, firstName="Max", lastName="Muster")
    actor = uuid.UUID(boss["id"])
    lift_1 = resource_service.create_workplace(
        db, dealership_id=dealership_id, actor_id=actor, name="Bühne 1", location_id=location_id
    )
    lift_2 = resource_service.create_workplace(
        db, dealership_id=dealership_id, actor_id=actor, name="Bühne 2", location_id=location_id
    )
    lea = resource_service.create_person(
        db, dealership_id=dealership_id, actor_id=actor, user_id=uuid.UUID(lea_user["id"]), location_id=location_id
    )
    max_ = resource_service.create_person(
        db, dealership_id=dealership_id, actor_id=actor, user_id=uuid.UUID(max_user["id"]), location_id=location_id
    )
    db.commit()
    hours_service.replace_working_hours(
        db,
        dealership_id=dealership_id,
        resource_id=lea.id,
        expected_version=lea.version,
        actor_id=actor,
        days=[
            hours_service.WorkingDay(
                weekday=weekday,
                starts_at=dt.time(7, 30),
                ends_at=dt.time(12, 0),
                second_starts_at=dt.time(13, 0),
                second_ends_at=dt.time(17, 0),
            )
            for weekday in range(1, 6)
        ],
    )
    db.commit()
    if grid is not None:
        set_setting(db, dealership_id, "planning.grid_minutes", grid)
    if slot is not None:
        set_setting(db, dealership_id, "planning.slot_minutes", slot)
    return Workshop(
        dealership_id=dealership_id,
        location_id=location_id,
        actor_id=actor,
        actor_name="Anna Chef",
        lift_1=lift_1.id,
        lift_2=lift_2.id,
        lea=lea.id,
        max=max_.id,
    )


ORDER = planning.Reference(type="aftersales.order", id=uuid.uuid4(), label="Auftrag 1001")


def order(label: str = "Auftrag") -> planning.Reference:
    return planning.Reference(type="aftersales.order", id=uuid.uuid4(), label=label)


def car(label: str = "VW Golf") -> planning.LabelledId:
    return planning.LabelledId(id=uuid.uuid4(), label=label)


def job(label: str = "Service") -> planning.LabelledId:
    return planning.LabelledId(id=uuid.uuid4(), label=label)


def shown(details: dict) -> list[planning.ConfirmedWarning]:
    """The warnings a `planning_warnings_unconfirmed` refusal listed, as the caller confirms them."""

    return [
        planning.ConfirmedWarning(
            code=planning.WarningCode(warning["code"]),
            resource_id=uuid.UUID(warning["resourceId"]) if warning["resourceId"] else None,
            against_booking_id=uuid.UUID(warning["againstBookingId"]) if warning["againstBookingId"] else None,
        )
        for warning in details["warnings"]
    ]


def work(
    db: Session,
    shop: Workshop,
    *,
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    resources: list[uuid.UUID],
    vehicle: planning.LabelledId | None = None,
    for_job: planning.LabelledId | None = None,
    reference: planning.Reference | None = None,
    confirmed=(),
    key: str | None = None,
) -> planning.BookingOutcome:
    return planning.book(
        db,
        dealership_id=shop.dealership_id,
        actor_id=shop.actor_id,
        kind=planning.BookingKind.WORK,
        starts_at=starts_at,
        ends_at=ends_at,
        resource_ids=resources,
        reference=reference or order(),
        job=for_job or job(),
        vehicle=vehicle or car(),
        confirmed_warnings=confirmed,
        idempotency_key=key or uuid.uuid4().hex,
    )


def slot(
    db: Session,
    shop: Workshop,
    kind: planning.BookingKind,
    *,
    starts_at: dt.datetime,
    reference: planning.Reference,
    resources: list[uuid.UUID],
    confirmed=(),
) -> planning.BookingOutcome:
    return planning.book(
        db,
        dealership_id=shop.dealership_id,
        actor_id=shop.actor_id,
        kind=kind,
        starts_at=starts_at,
        ends_at=None,
        resource_ids=resources,
        reference=reference,
        confirmed_warnings=confirmed,
        idempotency_key=uuid.uuid4().hex,
    )


def block(
    db: Session, shop: Workshop, *, starts_at: dt.datetime, ends_at: dt.datetime, resources: list[uuid.UUID], note=None
) -> planning.BookingOutcome:
    return planning.block(
        db,
        dealership_id=shop.dealership_id,
        actor_id=shop.actor_id,
        resource_ids=resources,
        starts_at=starts_at,
        ends_at=ends_at,
        note=note,
        idempotency_key=uuid.uuid4().hex,
    )
