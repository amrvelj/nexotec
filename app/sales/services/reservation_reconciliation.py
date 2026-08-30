"""Orphan-reservation sweep (WP-8 PR-6) — the follow-up work
app.inventory.services.reservation's own docstring flags as "PR-7/WP-8
follow-up work": a reservation whose contract never confirmed (or was
cancelled through some path that didn't release it) stays RESERVED
forever without this. Nothing schedules this — no scheduler exists
anywhere in the codebase (same honest gap as every other reconciliation
job here) — flagged, not hidden.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.public import ReservationState, StockItem, release
from app.sales.models.contract import ContractStatus, SalesContract


def release_orphaned_reservations(db: Session, *, tenant_id: uuid.UUID) -> list[uuid.UUID]:
    """Returns the stock_item ids whose orphaned reservation was released.
    A reservation is orphaned when its owning contract is not CONFIRMED —
    cancelled, or (defensively) simply missing.
    """

    reserved_items = list(
        db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id,
                StockItem.reservation_state == ReservationState.RESERVED,
                StockItem.active_reservation_id.is_not(None),
            )
        ).all()
    )

    released: list[uuid.UUID] = []
    for item in reserved_items:
        contract = None
        if item.reserved_by_contract_id is not None:
            contract = db.scalar(
                select(SalesContract).where(SalesContract.id == item.reserved_by_contract_id)
            )
        if contract is not None and contract.status == ContractStatus.CONFIRMED:
            continue  # a real, still-active reservation — not an orphan

        reservation_id = item.active_reservation_id
        assert reservation_id is not None, "query filtered on active_reservation_id IS NOT NULL"
        release(
            db,
            tenant_id=tenant_id,
            reservation_id=reservation_id,
            idempotency_key=f"sales.reservation_reconciliation:{reservation_id}",
        )
        released.append(item.id)

    return released
