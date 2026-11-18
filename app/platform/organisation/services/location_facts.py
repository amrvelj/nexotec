"""A location's facts for other contexts (KAN-226 PR-1; Dealer Administration
PRD FR-A-35, D-A-32): what Platform states, through the outbox, when a site's
public master data or its schedule changes — so a channel that mirrors a
location (Google Business Profile first) learns it from Platform's own fact,
never by polling and never inside Platform's transaction (rules 4–6).

Two event types, one payload shape (one entity's events share it):

- `platform.location.updated` — a published master-data field changed: name,
  code, types, the address block, phone, e-mail, geo position, active flag or
  validity.
- `platform.location.schedule_changed` — the site's opening hours, one of its
  closures, or its holiday calendar changed.

Payload: `dealershipId`, `locationId`, the location's `version` after the
write, `changedFields` (the camelCase names that changed — `openingHours` and
`closures` for the schedule's lists) and `location`, the published facts as
they now stand. The hours and closures themselves are not copied: a consumer
reads the current schedule through `platform.public` when it handles the
event (delivery is unordered and at-least-once, so a list in the payload
could be stale on arrival anyway).

Never in a payload: the contact person, the lead e-mail, the letterhead
override and the bank-account override — they are internal, and the bank
override is readable by the banking roles only (ADR-100).

A write that changes no published field publishes nothing. A location's
creation publishes nothing either: a new site is bound to no channel yet, and
binding it reads its facts then. `publish` adds and flushes in the caller's
session; the caller's commit makes the row and its fact land together.
"""

from collections.abc import Iterable
from typing import Any

from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app.core.outbox import OutboxEvent, publish
from app.platform.organisation.models.dealership import Location

LOCATION_UPDATED_EVENT = "platform.location.updated"
LOCATION_SCHEDULE_CHANGED_EVENT = "platform.location.schedule_changed"
_PRODUCER = "platform"
_AGGREGATE = "location"

# The master-data fields a channel may publish, snake_case as on the model.
PUBLISHED_FIELDS = (
    "code",
    "name",
    "location_types",
    "address_street",
    "address_house_number",
    "address_postal_code",
    "address_locality",
    "address_canton",
    "address_country",
    "phone",
    "email",
    "geo_lat",
    "geo_lng",
    "is_active",
    "valid_from",
    "valid_to",
)
# The schedule's scalar field; the hours and closures are named by list.
SCHEDULE_FIELDS = ("holiday_calendar_id",)
OPENING_HOURS = "openingHours"
CLOSURES = "closures"


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def published_facts(location: Location) -> dict[str, Any]:
    """The location's published facts as they now stand, camelCase."""

    return {to_camel(field): _plain(getattr(location, field)) for field in (*PUBLISHED_FIELDS, *SCHEDULE_FIELDS)}


def _publish(db: Session, *, event_type: str, location: Location, changed: list[str]) -> None:
    publish(
        db,
        OutboxEvent(
            event_type=event_type,
            tenant_id=location.tenant_id,
            producer=_PRODUCER,
            aggregate_type=_AGGREGATE,
            aggregate_id=location.id,
            payload={
                "dealershipId": str(location.tenant_id),
                "locationId": str(location.id),
                "version": location.version,
                "changedFields": changed,
                "location": published_facts(location),
            },
        ),
    )


def publish_location_changes(db: Session, *, location: Location, changed_fields: Iterable[str]) -> list[str]:
    """Given the snake_case fields a master-data write changed, adds
    `platform.location.updated` for the published ones and
    `platform.location.schedule_changed` for the holiday calendar — each
    only when one of its fields is among them. Returns the event types
    published."""

    changed = set(changed_fields)
    published: list[str] = []
    master = [to_camel(f) for f in PUBLISHED_FIELDS if f in changed]
    if master:
        _publish(db, event_type=LOCATION_UPDATED_EVENT, location=location, changed=master)
        published.append(LOCATION_UPDATED_EVENT)
    schedule = [to_camel(f) for f in SCHEDULE_FIELDS if f in changed]
    if schedule:
        _publish(db, event_type=LOCATION_SCHEDULE_CHANGED_EVENT, location=location, changed=schedule)
        published.append(LOCATION_SCHEDULE_CHANGED_EVENT)
    return published


def publish_schedule_changed(db: Session, *, location: Location, changed: str) -> None:
    """`platform.location.schedule_changed` for a change to the site's
    opening hours (`OPENING_HOURS`) or one of its closures (`CLOSURES`)."""

    _publish(db, event_type=LOCATION_SCHEDULE_CHANGED_EVENT, location=location, changed=[changed])
