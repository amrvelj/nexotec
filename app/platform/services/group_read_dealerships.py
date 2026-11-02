"""KAN-121 (ADR-055, rule 2): the Betriebe of a dealer group whose group
read is enabled, as plain data — for a context that reads its own rows of a
whole group (Stock's group-readable listing, ADR-055; the customer 360's
«in group stock» lookup) and must not join platform's tables to find them.

Its own module so the ambient-group-read allowlist
(`tests/architecture/test_no_ambient_group_read.py`) names this one read:
the `dealer_group_id` predicate moved here from
`app/inventory/services/group_listing.py`, which no longer filters on a
platform column at all."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models.dealership import DealerGroup, Dealership


@dataclass(frozen=True)
class DealershipRef:
    """A Betrieb as another context may hold it: its id and its label (the
    legal name, which the group stock listing has always shown)."""

    id: uuid.UUID
    label: str


def list_group_read_dealerships(db: Session, *, group_id: uuid.UUID) -> list[DealershipRef] | None:
    """Every Betrieb of `group_id`, sorted by label, while the group's read
    is enabled (`DealerGroup.group_read_enabled`, ADR-030's joint-controller
    flag). `None` when the group does not exist or its read is off — the
    caller turns that into its own 404 or empty answer, so nothing here
    confirms that a group exists. An enabled group without a Betrieb is an
    empty list, not `None`.

    The caller decides first whether the principal may ask about this group
    at all (its own group only, ADR-055); this function only answers for the
    group it is given."""

    # One statement (platform's own two tables): the callers count their
    # queries (the customer 360's Vehicles tab is batched, KAN-49). The
    # outer join keeps the group's row when it has no Betrieb.
    rows = db.execute(
        select(DealerGroup.group_read_enabled, Dealership.id, Dealership.legal_name)
        .select_from(DealerGroup)
        .outerjoin(Dealership, Dealership.dealer_group_id == DealerGroup.id)
        .where(DealerGroup.id == group_id)
        .order_by(Dealership.legal_name, Dealership.id)
    ).all()
    if not rows or not rows[0].group_read_enabled:
        return None
    return [
        DealershipRef(id=dealership_id, label=legal_name)
        for _enabled, dealership_id, legal_name in rows
        if dealership_id is not None
    ]
