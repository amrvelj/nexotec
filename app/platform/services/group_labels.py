"""KAN-371 (ADR-093): the display labels of ids another context recorded —
a Betrieb's name and a user's name — resolved on read for the History tab of
a group-owned customer. Its own module so the ambient-group-read allowlist
(`tests/architecture/test_no_ambient_group_read.py`) names this one read and
nothing beside it."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models.dealership import DealerGroup, Dealership
from app.platform.models.user import User


def get_group_display_labels(
    db: Session,
    *,
    group_id: uuid.UUID,
    dealership_id: uuid.UUID,
    dealership_ids: set[uuid.UUID],
    user_ids: set[uuid.UUID],
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    """A Betrieb's trade name (else legal name) and a user's `First Last`.

    ADR-093: the caller's own Betrieb is always named; a sister Betrieb of
    `group_id` and its users only while the group's read is enabled (ADR-030:
    the group-read path waits for a recorded joint-controller agreement,
    `DealerGroup.group_read_enabled`). An id outside that scope, or one that
    names nothing, is absent from the result — the caller renders it as
    unknown, so nothing here confirms another group's or a closed group's
    record exists."""

    group_read = bool(db.scalar(select(DealerGroup.group_read_enabled).where(DealerGroup.id == group_id)))
    in_scope = Dealership.dealer_group_id == group_id if group_read else Dealership.id == dealership_id

    dealerships: dict[uuid.UUID, str] = {}
    if dealership_ids:
        for found_id, trade_name, legal_name in db.execute(
            select(Dealership.id, Dealership.trade_name, Dealership.legal_name).where(
                Dealership.id.in_(dealership_ids), in_scope
            )
        ):
            dealerships[found_id] = trade_name or legal_name
    users: dict[uuid.UUID, str] = {}
    if user_ids:
        for user_id, first_name, last_name in db.execute(
            select(User.id, User.first_name, User.last_name)
            .join(Dealership, Dealership.id == User.tenant_id)
            .where(User.id.in_(user_ids), in_scope)
        ):
            users[user_id] = f"{first_name} {last_name}".strip()
    return dealerships, users
