"""The reservation service (WP-7 PR-4, ADR-047).

"A write spanning two contexts is a call with a compensating action,
never a shared transaction. It would work today because everything
shares one database. That is exactly why it is forbidden." reserve()/
release() each own their own commit — Pattern B (app.customer.services.
customer.repoint_vehicle_party), not Pattern A (app.sales.services.
transaction.repoint_customer_transactions's "join the caller's
transaction"). A future Sales caller (WP-8) calls this from OUTSIDE its
own contract-write transaction, with its own Idempotency-Key, a timeout
and one retry on its side — this module's only obligation is that ITS
half is atomic and idempotent on its own.

Reservation is allowed while pipeline (a factory order already sold is
the ordinary case, not an edge case) — no lifecycle_status check here at
all, only ONE active reservation per item, checked under a row lock.
There is no time-based expiry in v1 — only release() (called on contract
cancellation) or the nightly reconciliation job (compares every active
reservation against a confirmed contract and releases orphans — not
built in this PR; flagged as PR-7/WP-8 follow-up work, same as the
sales-side producer this whole module waits on) clears one.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.idempotency import find_cached_response, store_response
from app.core.outbox import OutboxEvent, publish
from app.core.uuid7 import uuid7
from app.inventory.models.stock_item import ReservationState, StockItem

_EVENT_PRODUCER = "inventory"


def reserve(
    db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID, contract_id: uuid.UUID, idempotency_key: str
) -> dict:
    """Returns {"reservationId": ..., "stockItemId": ...}. 409 if the item
    already carries an active reservation — a second reserve on an
    already-reserved item is a genuine conflict, not something retried
    away by the caller's own retry/timeout policy.
    """

    path = f"inventory.reserve:{stock_item_id}"
    body = {"contractId": str(contract_id)}
    cached = find_cached_response(db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body)
    if cached is not None:
        return cached.response_body

    item = db.scalar(
        select(StockItem).where(StockItem.id == stock_item_id, StockItem.tenant_id == tenant_id).with_for_update()
    )
    if item is None:
        raise NotFoundError(f"Stock item {stock_item_id} was not found.")
    if item.reservation_state == ReservationState.RESERVED:
        raise ConflictError(
            f"Stock item {stock_item_id} already carries an active reservation.",
            details={"stockItemId": str(stock_item_id)},
        )

    reservation_id = uuid7()
    item.reservation_state = ReservationState.RESERVED
    item.reserved_by_contract_id = contract_id
    item.active_reservation_id = reservation_id
    item.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.reserved",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"reservationId": str(reservation_id), "contractId": str(contract_id)},
        ),
    )

    response_body = {"reservationId": str(reservation_id), "stockItemId": str(item.id)}
    store_response(
        db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body, response_status=201,
        response_body=response_body,
    )
    db.commit()
    return response_body


def release(db: Session, *, tenant_id: uuid.UUID, reservation_id: uuid.UUID, idempotency_key: str) -> dict:
    path = f"inventory.release:{reservation_id}"
    body: dict = {}
    cached = find_cached_response(db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body)
    if cached is not None:
        return cached.response_body

    item = db.scalar(
        select(StockItem)
        .where(StockItem.tenant_id == tenant_id, StockItem.active_reservation_id == reservation_id)
        .with_for_update()
    )
    if item is None:
        raise NotFoundError(f"Reservation {reservation_id} was not found.")

    item.reservation_state = ReservationState.NONE
    item.reserved_by_contract_id = None
    item.active_reservation_id = None
    item.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.released",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"reservationId": str(reservation_id)},
        ),
    )

    response_body = {"stockItemId": str(item.id)}
    store_response(
        db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body, response_status=200,
        response_body=response_body,
    )
    db.commit()
    return response_body
