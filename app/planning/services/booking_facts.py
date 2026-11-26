"""A booking's facts for other contexts (KAN-808; ADR-221 (7)): what planning
states, through the outbox, whenever a booking changes — in the write's own
transaction (rules 4–6).

Four event types, one payload shape (ADR-202), aggregate `booking`, tenant
the Betrieb:

- `planning.booking.booked` — a booking was made (a block is `kind: "block"`);
- `planning.booking.moved` — its time or its resources changed;
- `planning.booking.released` — it was released and holds nothing;
- `planning.booking.marked_for_replanning` — a later change left it in the
  wrong place («neu einplanen»; `replan` says why).

Payload: `bookingId`, `dealershipId`, `kind`, `status`, `startsAt`, `endsAt`,
`resources` (`[{resourceId, kind}]`), `reference` (`{type, id, label}` or
null), `jobId`, `vehicleId`, `note`, `replan` (`{cause, since,
causedByBookingId, causedByResourceId}` or null), `overruledWarnings` (the
codes this change was made against; empty for every other change) and the
booking's `version` after the write. `publish` adds and flushes in the
caller's session; the caller's commit makes the row and its fact land
together.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from sqlalchemy.orm import Session

from app.core.outbox import OutboxEvent, publish
from app.planning.models.booking import WarningCode
from app.planning.services.booking_records import BookingRecord

BOOKING_BOOKED_EVENT = "planning.booking.booked"
BOOKING_MOVED_EVENT = "planning.booking.moved"
BOOKING_RELEASED_EVENT = "planning.booking.released"
BOOKING_MARKED_FOR_REPLANNING_EVENT = "planning.booking.marked_for_replanning"
BOOKING_EVENTS = (
    BOOKING_BOOKED_EVENT,
    BOOKING_MOVED_EVENT,
    BOOKING_RELEASED_EVENT,
    BOOKING_MARKED_FOR_REPLANNING_EVENT,
)
_PRODUCER = "planning"
_AGGREGATE = "booking"


def _id(value: Any) -> str | None:
    return str(value) if value is not None else None


def payload_of(record: BookingRecord, *, overruled: Collection[WarningCode] = ()) -> dict[str, Any]:
    return {
        "bookingId": str(record.id),
        "dealershipId": str(record.dealership_id),
        "kind": record.kind.value,
        "status": record.status.value,
        "startsAt": record.starts_at.isoformat(),
        "endsAt": record.ends_at.isoformat(),
        "resources": [{"resourceId": str(r.resource_id), "kind": r.kind.value} for r in record.resources],
        "reference": (
            {"type": record.reference.type, "id": str(record.reference.id), "label": record.reference.label}
            if record.reference is not None
            else None
        ),
        "jobId": _id(record.job.id if record.job else None),
        "vehicleId": _id(record.vehicle.id if record.vehicle else None),
        "note": record.note,
        "replan": (
            {
                "cause": record.replan.cause.value,
                "since": record.replan.since.isoformat(),
                "causedByBookingId": _id(record.replan.caused_by_booking_id),
                "causedByResourceId": _id(record.replan.caused_by_resource_id),
            }
            if record.replan is not None
            else None
        ),
        "overruledWarnings": sorted(code.value for code in overruled),
        "version": record.version,
    }


def publish_booking_fact(
    db: Session, *, event_type: str, record: BookingRecord, overruled: Collection[WarningCode] = ()
) -> None:
    if event_type not in BOOKING_EVENTS:
        raise ValueError(f"{event_type!r} is no booking fact")
    publish(
        db,
        OutboxEvent(
            event_type=event_type,
            tenant_id=record.dealership_id,
            producer=_PRODUCER,
            aggregate_type=_AGGREGATE,
            aggregate_id=record.id,
            payload=payload_of(record, overruled=overruled),
        ),
    )
