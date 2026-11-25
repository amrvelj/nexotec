"""A person's weekly working hours (KAN-808, S7; check 18 reads them in PR-1c).

One row per working weekday (ISO: 1 = Monday … 7 = Sunday) with one
interval and an optional second one, e.g. 07:30–12:00 and 13:00–17:00 — the
shape of a location's opening hours. A weekday that is not listed is not
worked. Times are local wall-clock times (Europe/Zurich), to the minute.

The week is replaced as a whole under the resource's version (If-Match): the
rows carry no version of their own. Only a person has working hours (422
`planning_person_only`); a week that does not hold together is 422
`planning_working_hours_invalid`, naming the weekday.

The caller checks the role and the Betrieb and commits. Audited.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.concurrency import check_version
from app.core.errors import UnprocessableEntityError
from app.planning.models.resource import Resource, ResourceKind, WorkingHours
from app.planning.services.locks import lock_master_data
from app.planning.services.resources import get_resource_or_404

# Audited on the resource itself, so its history shows the week changing with
# its version (KAN-808 review).
ENTITY_TYPE = "planning_resource"


@dataclass(frozen=True)
class WorkingDay:
    weekday: int
    starts_at: dt.time
    ends_at: dt.time
    second_starts_at: dt.time | None = None
    second_ends_at: dt.time | None = None


def working_hours_of(db: Session, resource_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, list[WorkingHours]]:
    found: dict[uuid.UUID, list[WorkingHours]] = {resource_id: [] for resource_id in resource_ids}
    if not resource_ids:
        return found
    rows = db.scalars(
        select(WorkingHours)
        .where(WorkingHours.resource_id.in_(set(resource_ids)))
        .order_by(WorkingHours.resource_id, WorkingHours.weekday)
    )
    for row in rows:
        found[row.resource_id].append(row)
    return found


def _invalid(message: str, weekday: Any = None) -> UnprocessableEntityError:
    details: dict[str, Any] = {"reason": "planning_working_hours_invalid"}
    if isinstance(weekday, int):
        details["weekday"] = weekday
    return UnprocessableEntityError(message, details=details)


def _on_the_minute(value: dt.time | None) -> bool:
    return value is None or (value.tzinfo is None and value.second == 0 and value.microsecond == 0)


def check_week(days: Sequence[WorkingDay]) -> list[WorkingDay]:
    """The week in weekday order, or 422 `planning_working_hours_invalid`."""

    seen: set[int] = set()
    for day in days:
        if not isinstance(day.weekday, int) or not 1 <= day.weekday <= 7:
            raise _invalid("A weekday is 1 (Monday) to 7 (Sunday).")
        if day.weekday in seen:
            raise _invalid("A weekday is listed twice.", day.weekday)
        seen.add(day.weekday)
        times = (day.starts_at, day.ends_at, day.second_starts_at, day.second_ends_at)
        if not all(_on_the_minute(value) for value in times):
            raise _invalid("Working hours are local times to the minute.", day.weekday)
        if day.starts_at >= day.ends_at:
            raise _invalid("The working time ends after it starts.", day.weekday)
        if (day.second_starts_at is None) != (day.second_ends_at is None):
            raise _invalid("The second interval needs its start and its end.", day.weekday)
        if day.second_starts_at is not None and day.second_ends_at is not None:
            if day.second_starts_at <= day.ends_at:
                raise _invalid("The second interval starts after the first one ends.", day.weekday)
            if day.second_starts_at >= day.second_ends_at:
                raise _invalid("The second interval ends after it starts.", day.weekday)
    return sorted(days, key=lambda day: day.weekday)


def _plain(value: dt.time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _snapshot(rows: Sequence[WorkingHours | WorkingDay]) -> list[dict[str, Any]]:
    return [
        {
            "weekday": row.weekday,
            "startsAt": _plain(row.starts_at),
            "endsAt": _plain(row.ends_at),
            "secondStartsAt": _plain(row.second_starts_at),
            "secondEndsAt": _plain(row.second_ends_at),
        }
        for row in sorted(rows, key=lambda row: row.weekday)
    ]


def replace_working_hours(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    resource_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
    days: Sequence[WorkingDay],
) -> Resource:
    week = check_week(days)
    lock_master_data(db, dealership_id)
    resource = get_resource_or_404(db, dealership_id=dealership_id, resource_id=resource_id, lock=True)
    check_version(resource.version, expected_version, entity_name="Resource")
    if resource.kind is not ResourceKind.PERSON:
        raise UnprocessableEntityError(
            "Only a person has working hours.", details={"reason": "planning_person_only"}
        )
    before = _snapshot(working_hours_of(db, [resource.id])[resource.id])
    after = _snapshot(week)
    if before == after:
        return resource
    db.execute(delete(WorkingHours).where(WorkingHours.resource_id == resource.id))
    for day in week:
        db.add(
            WorkingHours(
                tenant_id=dealership_id,
                resource_id=resource.id,
                weekday=day.weekday,
                starts_at=day.starts_at,
                ends_at=day.ends_at,
                second_starts_at=day.second_starts_at,
                second_ends_at=day.second_ends_at,
            )
        )
    resource.version += 1
    resource.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=resource.id,
        tenant_id=dealership_id,
        action="working_hours_replaced",
        actor_id=actor_id,
        before={"workingHours": before},
        after={"workingHours": after},
    )
    return resource
