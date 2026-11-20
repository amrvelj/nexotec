"""Dealer Betriebe only (KAN-221 PR-1): the one door through which platform
lists Betriebe for a dealer purpose.

The platform tenant (`DealershipKind.PLATFORM`, the Betrieb «Nexotec» that
homes Nexotec's staff) is a `dealership` row so that staff are users like
any other — but it is no dealer. It is never listed, never a membership,
support-session or entitlement target, and never in a status run, a
checklist run, a group read, an export or a configuration copy.

Every query in `app/` and `scripts/` that selects or joins `Dealership` or
`DealerGroup` either applies one of these clauses or is named, with its
reason, in `tests/architecture/test_dealer_dealership_reads.py`'s allow-list
(a read of one Betrieb by id, a uniqueness check, a reference check).
"""

import uuid

from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableEntityError
from app.platform.organisation.models.dealership import (
    PLATFORM_DEALER_GROUP_ID,
    PLATFORM_DEALERSHIP_ID,
    DealerGroup,
    Dealership,
    DealershipKind,
)


def dealer_dealerships_only() -> ColumnElement[bool]:
    """The WHERE clause that keeps dealer Betriebe and drops the platform tenant."""

    return Dealership.kind == DealershipKind.DEALER


def dealer_groups_only() -> ColumnElement[bool]:
    """The WHERE clause that drops the platform tenant's group «Nexotec»."""

    return DealerGroup.id != PLATFORM_DEALER_GROUP_ID


def is_platform_dealership(db: Session, dealership_id: uuid.UUID) -> bool:
    """Whether `dealership_id` names the platform tenant."""

    if dealership_id == PLATFORM_DEALERSHIP_ID:
        return True
    dealership = db.get(Dealership, dealership_id)
    return dealership is not None and dealership.kind == DealershipKind.PLATFORM


def assert_dealer_dealership(db: Session, dealership_id: uuid.UUID) -> None:
    """404 for the platform tenant where only a dealer Betrieb can be meant —
    the same answer as a Betrieb that does not exist: to a dealer route the
    platform tenant is not a Betrieb."""

    if is_platform_dealership(db, dealership_id):
        raise NotFoundError(f"Dealership {dealership_id} was not found.")


def refuse_platform_dealer_group(chosen_group: uuid.UUID | None) -> None:
    """422: no Betrieb is onboarded into the platform tenant's group. (A check
    of the one id the request names, not a read: nothing is filtered by it.)"""

    if chosen_group == PLATFORM_DEALER_GROUP_ID:
        raise UnprocessableEntityError(
            "The Nexotec platform group holds no dealer business.",
            details={"reason": "platform_group_holds_no_dealer"},
        )
