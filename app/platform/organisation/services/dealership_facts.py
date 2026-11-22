"""A Betrieb's own published facts for other contexts (KAN-785; Dealer
Administration FR-A-35, D-A-32): what Platform states, through the outbox,
when a field of the dealership row that a channel mirrors changes — the
sibling of `location_facts`, for the one Betrieb-level field a location's
channel facts carry.

A channel that mirrors a location (Google Business Profile) reads the
Betrieb's website as part of the location's facts
(`platform.public.location_channel_facts`). Without a fact of its own, a new
website reached Google only when one of that Betrieb's location facts fired
next (KAN-785).

One event type:

- `platform.dealership.updated` — a published field of the Betrieb changed.
  Today that is `website` only (`PUBLISHED_FIELDS`); a field joins the list
  when a channel publishes it.

Payload: `dealershipId`, the dealership's `version` after the write,
`changedFields` (the camelCase names that changed) and `dealership`, the
published facts as they now stand. A consumer re-reads the current state
through `platform.public` (delivery is unordered and at-least-once).

A write that changes no published field publishes nothing. `publish` adds and
flushes in the caller's session; the caller's commit makes the row and its
fact land together (rule 4).
"""

from collections.abc import Iterable
from typing import Any

from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app.core.outbox import OutboxEvent, publish
from app.platform.organisation.models.dealership import Dealership

DEALERSHIP_UPDATED_EVENT = "platform.dealership.updated"
_PRODUCER = "platform"
_AGGREGATE = "dealership"

# The dealership fields a channel may publish, snake_case as on the model.
PUBLISHED_FIELDS = ("website",)


def published_facts(dealership: Dealership) -> dict[str, Any]:
    """The Betrieb's published facts as they now stand, camelCase."""

    return {to_camel(field): getattr(dealership, field) for field in PUBLISHED_FIELDS}


def publish_dealership_changes(db: Session, *, dealership: Dealership, changed_fields: Iterable[str]) -> bool:
    """Given the snake_case fields a write changed, adds
    `platform.dealership.updated` when a published one is among them.
    True when the fact was added."""

    changed = [to_camel(field) for field in PUBLISHED_FIELDS if field in set(changed_fields)]
    if not changed:
        return False
    publish(
        db,
        OutboxEvent(
            event_type=DEALERSHIP_UPDATED_EVENT,
            tenant_id=dealership.id,
            producer=_PRODUCER,
            aggregate_type=_AGGREGATE,
            aggregate_id=dealership.id,
            payload={
                "dealershipId": str(dealership.id),
                "version": dealership.version,
                "changedFields": changed,
                "dealership": published_facts(dealership),
            },
        ),
    )
    return True
