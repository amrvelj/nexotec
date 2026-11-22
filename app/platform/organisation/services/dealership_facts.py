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

The master-data fact:

- `platform.dealership.updated` — a published field of the Betrieb changed.
  Today that is `website` only (`PUBLISHED_FIELDS`); a field joins the list
  when a channel publishes it.

**One payload shape for every `platform.dealership.*` fact** (events.md: one
entity's events share it; KAN-790), built by `dealership_envelope`:
`dealershipId`, the dealership's `version` after the write, `changedFields`
(the camelCase names of the dealership's fields the write set that a consumer
may act on: the published fields among them, and `status` and its stamps for
a lifecycle step — a creation names `status` and every published field,
whether or not it holds a value, never the internal ones such as the legal
name or the UID) and
`dealership`, the published facts as they now stand. A lifecycle fact adds
its own fields beside them, never inside: `platform.dealership.onboarded`
(identity's onboarding) its group, legal name, entitlements, manager and
actor; `.activated` and `.offboarded` (administration) when and, for an
activation, by whom. A consumer re-reads the current state through
`platform.public` (delivery is unordered and at-least-once).

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


def dealership_envelope(dealership: Dealership, *, changed_fields: Iterable[str]) -> dict[str, Any]:
    """The payload every `platform.dealership.*` fact carries (KAN-790),
    given the snake_case fields the write set. The caller has written the
    row's new `version`."""

    if dealership.version is None:
        raise ValueError("A dealership fact needs the row's version after the write.")
    return {
        "dealershipId": str(dealership.id),
        "version": dealership.version,
        "changedFields": [to_camel(field) for field in changed_fields],
        "dealership": published_facts(dealership),
    }


def publish_dealership_changes(db: Session, *, dealership: Dealership, changed_fields: Iterable[str]) -> bool:
    """Given the snake_case fields a write changed, adds
    `platform.dealership.updated` when a published one is among them.
    True when the fact was added."""

    if not any(field in set(changed_fields) for field in PUBLISHED_FIELDS):
        return False
    publish(
        db,
        OutboxEvent(
            event_type=DEALERSHIP_UPDATED_EVENT,
            tenant_id=dealership.id,
            producer=_PRODUCER,
            aggregate_type=_AGGREGATE,
            aggregate_id=dealership.id,
            payload=dealership_envelope(
                dealership, changed_fields=[field for field in PUBLISHED_FIELDS if field in set(changed_fields)]
            ),
        ),
    )
    return True
