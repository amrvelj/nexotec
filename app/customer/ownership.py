"""Customer's ownership provider for the deactivation step (KAN-200;
FR-A-37, D-A-35). What a user "owns" here is the customers they are the
responsible advisor of (`Customer.advisor_id`, D-24). A hand-over sets the
advisor on each of them — to a colleague, or to nobody ("unassigned": the
customer list's `unassigned=true` filter is that queue) — with the same
audit row per customer an advisor change through PATCH writes, carrying the
manager's reason, and the same `customer.updated` event, so a label
elsewhere refreshes the same way.

Registered with `app.core.ownership` by the composition root
(`app.ownership_registry`); platform never imports this module. `transfer`
runs on a session of its own and commits there (ADR-047).

Customers are group-scoped (ADR-014) while the advisor is a user of one
dealership: the predicate is the advisor id alone — a user id names one
person platform-wide — never a group filter (the no-ambient-group-read test
keeps that out of this file on purpose).

A hand-over to a colleague is refused as a whole when the colleague works in
no Betrieb of a customer's dealer group (KAN-423, Anto 2026-10-23, option A —
Sales' rule for deals): since KAN-410 a sister-Betrieb member advises the
customers they create there, and a membership may span dealer groups, so a
home colleague could otherwise be handed customers they cannot even read.
The refusal uses Sales' code, `colleague_cannot_act_there`, and names a
count, never the other group.
"""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.ownership import OwnedGroup, OwnershipRefusal, OwnershipTransfer
from app.customer.models.customer import Customer, CustomerLifecycleStatus
from app.customer.services.customer import _customer_label_payload
from app.platform.public import list_user_dealer_group_ids

logger = logging.getLogger(__name__)

MODULE = "customer"
GROUP_CUSTOMERS = "customers"
_EVENT_PRODUCER = "customer"


def _open_customers(user_id: uuid.UUID):
    return (Customer.advisor_id == user_id, Customer.lifecycle_status != CustomerLifecycleStatus.MERGED)


class CustomerOwnershipProvider:
    module = MODULE

    def list_open(self, db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> list[OwnedGroup]:
        count = db.scalar(select(func.count(Customer.id)).where(*_open_customers(user_id)))
        if not count:
            return []
        return [OwnedGroup(module=MODULE, group=GROUP_CUSTOMERS, count=int(count))]

    def refuses(self, db: Session, *, from_user_id: uuid.UUID, transfer: OwnershipTransfer) -> OwnershipRefusal | None:
        if transfer.to_user_id is None or transfer.group != GROUP_CUSTOMERS:
            return None
        group_ids = list(db.scalars(select(Customer.group_id).where(*_open_customers(from_user_id))).all())
        return _colleague_refusal(db, to_user_id=transfer.to_user_id, group_ids=group_ids)

    def transfer(
        self,
        session_factory: Callable[[], Session],
        *,
        dealership_id: uuid.UUID,
        from_user_id: uuid.UUID,
        transfer: OwnershipTransfer,
        actor_id: uuid.UUID | None,
        reason: str | None,
    ) -> int:
        if transfer.group != GROUP_CUSTOMERS:
            raise ValueError(f"customer owns no group '{transfer.group}'")
        db = session_factory()
        try:
            moved = _transfer_customers(
                db,
                from_user_id=from_user_id,
                to_user_id=transfer.to_user_id,
                to_label=transfer.to_label,
                actor_id=actor_id,
                reason=reason,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        logger.info(
            "customer.ownership.transferred from=%s to=%s count=%d",
            from_user_id,
            transfer.to_user_id,
            moved,
            extra={"fromUserId": str(from_user_id), "count": moved},
        )
        return moved


COLLEAGUE_CANNOT_ACT_THERE = "colleague_cannot_act_there"


def _colleague_refusal(
    db: Session, *, to_user_id: uuid.UUID | None, group_ids: list[uuid.UUID]
) -> OwnershipRefusal | None:
    """How many of the customers belong to a dealer group the colleague works
    in no Betrieb of — never which group (an id is a fact the caller may not
    hold)."""

    if to_user_id is None:
        return None
    allowed = list_user_dealer_group_ids(db, user_id=to_user_id)
    elsewhere = sum(1 for group in group_ids if group not in allowed)
    if not elsewhere:
        return None
    return OwnershipRefusal(
        code=COLLEAGUE_CANNOT_ACT_THERE,
        message=(
            f"customer/{GROUP_CUSTOMERS}: {elsewhere} of these records belong to another business the colleague "
            "cannot act in — leave the group unassigned (null), and that business hands them over from its own "
            "queue, or choose a colleague who is a member there."
        ),
        count=elsewhere,
    )


def _transfer_customers(
    db: Session,
    *,
    from_user_id: uuid.UUID,
    to_user_id: uuid.UUID | None,
    to_label: str | None,
    actor_id: uuid.UUID | None,
    reason: str | None,
) -> int:
    rows = list(
        db.scalars(select(Customer).where(*_open_customers(from_user_id)).order_by(Customer.id).with_for_update()).all()
    )
    # Judged again under the row locks, before any write (as Sales does): the
    # step's `refuses` read the rows unlocked.
    refusal = _colleague_refusal(db, to_user_id=to_user_id, group_ids=[customer.group_id for customer in rows])
    if refusal is not None:
        raise UnprocessableEntityError(
            refusal.message,
            details={
                "module": MODULE,
                "group": GROUP_CUSTOMERS,
                "toUserId": str(to_user_id),
                "reason": refusal.code,
                "count": refusal.count,
            },
        )
    now = utcnow()
    for customer in rows:
        before = {"advisor_id": str(customer.advisor_id)}
        customer.advisor_id = to_user_id
        customer.advisor_label = to_label if to_user_id is not None else None
        customer.advisor_label_refreshed_at = now if to_user_id is not None else None
        customer.updated_by = actor_id
        customer.version += 1
        record_audit_event(
            db,
            entity_type="customer",
            entity_id=customer.id,
            tenant_id=customer.group_id,
            action="update",
            actor_id=actor_id,
            before=before,
            after={"advisor_id": str(to_user_id) if to_user_id is not None else None},
            reason=reason or "ownership_transferred",
        )
        publish(
            db,
            OutboxEvent(
                event_type="customer.updated",
                tenant_id=customer.group_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="customer",
                aggregate_id=customer.id,
                payload=_customer_label_payload(customer),
            ),
        )
    db.flush()
    return len(rows)
