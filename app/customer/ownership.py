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
"""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.outbox import OutboxEvent, publish
from app.core.ownership import OwnedGroup, OwnershipRefusal, OwnershipTransfer
from app.customer.models.customer import Customer, CustomerLifecycleStatus
from app.customer.services.customer import _customer_label_payload

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
        return None

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
