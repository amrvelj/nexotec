"""What `planning.public` hands out about a booking (KAN-808; ADR-162):
frozen dataclasses of plain values and enums, never a mapped class."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.planning.models.booking import (
    Booking,
    BookingChange,
    BookingKind,
    BookingResource,
    BookingStatus,
    OverruledWarning,
    ReplanCause,
    WarningCode,
)
from app.planning.models.resource import Resource, ResourceKind


@dataclass(frozen=True)
class Reference:
    """The caller's reference R: `type` names the owning context and entity
    (`aftersales.order`), `label` how the caller names it today."""

    type: str
    id: uuid.UUID
    label: str


@dataclass(frozen=True)
class LabelledId:
    """Another context's id with the label the caller passes (a job, a car)."""

    id: uuid.UUID
    label: str


@dataclass(frozen=True)
class ConfirmedWarning:
    """One warning the caller was shown and books against, exactly as the
    refusal listed it: its code, the resource and the booking it was raised
    against. Confirmation is per warning, never per code (ADR-221 (2)): a
    warning of a confirmed code that appears between the two calls — another
    resource, another block, another slot — refuses again."""

    code: WarningCode
    resource_id: uuid.UUID | None = None
    against_booking_id: uuid.UUID | None = None


@dataclass(frozen=True)
class BookedResource:
    resource_id: uuid.UUID
    kind: ResourceKind
    # A workplace's name, a person's user name.
    label: str


@dataclass(frozen=True)
class ReplanMark:
    """«Neu einplanen»: why, since when, and what caused it — the block or the
    slot (`caused_by_booking_id`), or the deactivated resource."""

    cause: ReplanCause
    since: dt.datetime
    caused_by_booking_id: uuid.UUID | None
    caused_by_resource_id: uuid.UUID | None


@dataclass(frozen=True)
class OverruledWarningRecord:
    code: WarningCode
    resource_id: uuid.UUID | None
    against_booking_id: uuid.UUID | None
    change: BookingChange
    booked_starts_at: dt.datetime
    booked_ends_at: dt.datetime
    overruled_by: uuid.UUID
    overruled_by_name: str
    overruled_at: dt.datetime


@dataclass(frozen=True)
class BookingRecord:
    id: uuid.UUID
    dealership_id: uuid.UUID
    kind: BookingKind
    status: BookingStatus
    starts_at: dt.datetime
    ends_at: dt.datetime
    resources: tuple[BookedResource, ...]
    reference: Reference | None
    job: LabelledId | None
    vehicle: LabelledId | None
    note: str | None
    replan: ReplanMark | None
    overruled_warnings: tuple[OverruledWarningRecord, ...]
    released_at: dt.datetime | None
    version: int


@dataclass(frozen=True)
class BookingOutcome:
    """A booking written, and the bookings the write marked «neu einplanen»
    (a block over them; work left outside a slot that was booked or moved)."""

    booking: BookingRecord
    marked_for_replanning: tuple[BookingRecord, ...]


def _labelled(entity_id: uuid.UUID | None, label: str | None) -> LabelledId | None:
    return LabelledId(id=entity_id, label=label or "") if entity_id is not None else None


def records_of(db: Session, bookings: Sequence[Booking]) -> list[BookingRecord]:
    """The records of these bookings, in their order; three reads for any number."""

    ids = [booking.id for booking in bookings]
    if not ids:
        return []
    resources: dict[uuid.UUID, list[BookedResource]] = {booking_id: [] for booking_id in ids}
    for booking_id, resource_id, kind, name, user_label in db.execute(
        select(BookingResource.booking_id, Resource.id, Resource.kind, Resource.name, Resource.user_label)
        .join(Resource, Resource.id == BookingResource.resource_id)
        .where(BookingResource.booking_id.in_(ids))
        .order_by(BookingResource.id)
    ):
        resources[booking_id].append(BookedResource(resource_id=resource_id, kind=kind, label=name or user_label or ""))
    warnings: dict[uuid.UUID, list[OverruledWarningRecord]] = {booking_id: [] for booking_id in ids}
    for row in db.scalars(
        select(OverruledWarning).where(OverruledWarning.booking_id.in_(ids)).order_by(OverruledWarning.id)
    ):
        warnings[row.booking_id].append(
            OverruledWarningRecord(
                code=row.code,
                resource_id=row.resource_id,
                against_booking_id=row.against_booking_id,
                change=row.change,
                booked_starts_at=row.booked_starts_at,
                booked_ends_at=row.booked_ends_at,
                overruled_by=row.overruled_by,
                overruled_by_name=row.overruled_by_name,
                overruled_at=row.overruled_at,
            )
        )
    return [
        BookingRecord(
            id=booking.id,
            dealership_id=booking.tenant_id,
            kind=booking.kind,
            status=booking.status,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            resources=tuple(resources[booking.id]),
            reference=(
                Reference(type=booking.reference_type or "", id=booking.reference_id, label=booking.reference_label or "")
                if booking.reference_id is not None
                else None
            ),
            job=_labelled(booking.job_id, booking.job_label),
            vehicle=_labelled(booking.vehicle_id, booking.vehicle_label),
            note=booking.note,
            replan=(
                ReplanMark(
                    cause=booking.replan_cause,
                    since=booking.replan_since,
                    caused_by_booking_id=booking.replan_caused_by_booking_id,
                    caused_by_resource_id=booking.replan_caused_by_resource_id,
                )
                if booking.replan_cause is not None and booking.replan_since is not None
                else None
            ),
            overruled_warnings=tuple(warnings[booking.id]),
            released_at=booking.released_at,
            version=booking.version,
        )
        for booking in bookings
    ]


def record_of(db: Session, booking: Booking) -> BookingRecord:
    return records_of(db, [booking])[0]


def bookings_by_id(db: Session, *, dealership_id: uuid.UUID, ids: Iterable[uuid.UUID]) -> list[Booking]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = {
        booking.id: booking
        for booking in db.scalars(
            select(Booking)
            .where(Booking.tenant_id == dealership_id, Booking.id.in_(wanted))
            .execution_options(populate_existing=True)
        )
    }
    return [found[booking_id] for booking_id in wanted if booking_id in found]
