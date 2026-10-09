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
half is atomic and idempotent on its own. Sales confirms through
reserve_for_contract(), idempotent by (item, contract) rather than by key
alone, because a replay may follow a compensating release (KAN-114).

Reservation is allowed while pipeline (a factory order already sold is
the ordinary case, not an edge case) — no lifecycle_status check here at
all, only ONE active reservation per item, checked under a row lock.
There is no time-based expiry in v1 — only release() (called on contract
cancellation, or as a failed confirmation's compensation) or the nightly
orphan sweep (app.inventory.services.reservation_sweep, KAN-122: frees a
reservation whose contract is not signed) clears one.

KAN-158 — a manual configuration's pipeline item is reserved by Stock itself
when its consumer creates the item (reserve_and_flush, in the consumer's
transaction), and released by Stock when it consumes
`sales.contract.cancelled` (record_contract_cancelled), which also records
the cancellation so a confirmation delivered after it never reserves. Sales
never calls reserve() for it.
"""

import datetime as dt
import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.idempotency import find_cached_response, store_response
from app.core.outbox import OutboxEvent, publish
from app.core.uuid7 import uuid7
from app.inventory.models.cancelled_contract import InventoryCancelledContract
from app.inventory.models.stock_item import ReservationState, StockItem

_EVENT_PRODUCER = "inventory"


def reserve(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    stock_item_id: uuid.UUID,
    contract_id: uuid.UUID,
    idempotency_key: str | None = None,
) -> dict:
    """Returns {"reservationId": ..., "stockItemId": ...}. 409 if the item
    already carries an active reservation — a second reserve on an
    already-reserved item is a genuine conflict, not something retried
    away by the caller's own retry/timeout policy.

    A cross-context caller passes its own `idempotency_key`: a replay then
    returns the stored response with no side effect, for as long as the
    record lives (internal records are not purged). The HTTP endpoint
    passes none — its `IdempotentRoute` already holds the client's key
    (KAN-266), and a second record under the same key would collide with
    the route's claim.
    """

    path = f"inventory.reserve:{stock_item_id}"
    body = {"contractId": str(contract_id)}
    if idempotency_key is not None:
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

    reservation_id = reserve_and_flush(db, item=item, contract_id=contract_id)

    response_body = {"reservationId": str(reservation_id), "stockItemId": str(item.id)}
    if idempotency_key is not None:
        store_response(
            db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body, response_status=201,
            response_body=response_body,
        )
    db.commit()
    return response_body


def reserve_for_contract(
    db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID, contract_id: uuid.UUID, idempotency_key: str
) -> dict:
    """Sales' confirmation entry (KAN-114): reserve() made idempotent by
    (stock item, contract), not by key alone. Same response and the same
    409 as reserve().

    Sales retries a confirmation under one key per contract, and between
    two attempts its compensation may have released the reservation the
    first one made (its own commit failed — ADR-047). Replaying the cached
    response would then confirm the contract on a car that is no longer
    reserved. So the item answers, under its row lock: the contract's live
    reservation is returned, a free item is reserved again, and an item
    another contract holds is the ordinary 409. The key still guards
    against reuse with another contract (find_cached_response), but a
    reservation made on a replay is not recorded against it — the record is
    write-once — which is why "held by this contract" is what a later
    replay matches on.

    The HTTP endpoint keeps reserve(), on an IdempotentRoute (KAN-266): a
    replayed Idempotency-Key there returns the stored response with no side
    effect, as the API convention promises, for as long as the HTTP record
    is kept (at least 24 h). Sales only calls this for a PENDING contract.
    """

    path = f"inventory.reserve:{stock_item_id}"
    body = {"contractId": str(contract_id)}

    item = db.scalar(
        select(StockItem).where(StockItem.id == stock_item_id, StockItem.tenant_id == tenant_id).with_for_update()
    )
    if item is None:
        raise NotFoundError(f"Stock item {stock_item_id} was not found.")
    # Read under the row lock, not before it: an attempt that reserved,
    # stored the key and was compensated in between would otherwise leave
    # this one storing the same key a second time (a unique violation).
    cached = find_cached_response(db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body)
    if item.reservation_state == ReservationState.RESERVED and item.reserved_by_contract_id == contract_id:
        db.commit()
        return {"reservationId": str(item.active_reservation_id), "stockItemId": str(item.id)}
    if item.reservation_state == ReservationState.RESERVED:
        raise ConflictError(
            f"Stock item {stock_item_id} already carries an active reservation.",
            details={"stockItemId": str(stock_item_id)},
        )

    reservation_id = reserve_and_flush(db, item=item, contract_id=contract_id)

    response_body = {"reservationId": str(reservation_id), "stockItemId": str(item.id)}
    if cached is None:
        store_response(
            db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body, response_status=201,
            response_body=response_body,
        )
    db.commit()
    return response_body


def release(
    db: Session, *, tenant_id: uuid.UUID, reservation_id: uuid.UUID, idempotency_key: str | None = None
) -> dict:
    """Sales' cancellation and compensation, and the orphan sweep, pass a
    key of their own (replayed with no side effect). The HTTP endpoint
    passes none: its IdempotentRoute holds the client's key (KAN-266)."""

    path = f"inventory.release:{reservation_id}"
    body: dict = {}
    if idempotency_key is not None:
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

    release_and_flush(db, item=item)

    response_body = {"stockItemId": str(item.id)}
    if idempotency_key is not None:
        store_response(
            db, tenant_id=tenant_id, key=idempotency_key, path=path, body=body, response_status=200,
            response_body=response_body,
        )
    db.commit()
    return response_body


def reserve_and_flush(db: Session, *, item: StockItem, contract_id: uuid.UUID) -> uuid.UUID:
    """The commit-free core of reserve(), shared with the pipeline consumer
    (KAN-158), whose write must land in the SAME transaction as the consumer
    harness's ProcessedEvent row. The caller has already established that
    the item carries no active reservation.
    """

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
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"reservationId": str(reservation_id), "contractId": str(contract_id)},
        ),
    )
    return reservation_id


def release_and_flush(db: Session, *, item: StockItem) -> None:
    """The commit-free core of release(), shared with the
    `sales.contract.cancelled` consumer (KAN-158) for the same reason."""

    reservation_id = item.active_reservation_id
    item.reservation_state = ReservationState.NONE
    item.reserved_by_contract_id = None
    item.active_reservation_id = None
    item.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.released",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"reservationId": str(reservation_id)},
        ),
    )


def lock_contract(db: Session, *, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> None:
    """pg_advisory_xact_lock on a 64-bit key derived from (tenant, contract),
    released by the transaction's commit or rollback. Both KAN-158 consumers
    take it first, so a confirmation and a cancellation for one contract,
    handled by two workers at once, run one after the other: the cancellation
    then sees the item the confirmation created, or the confirmation sees the
    recorded cancellation. SQLite (the fast local lane, ADR-011) has no
    advisory locks and serialises writers on its own, so this is a no-op
    there. Same idiom as app.customer.services.customer.
    """

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"inventory.contract:{tenant_id}:{contract_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def record_contract_cancelled(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    contract_label: str,
    cancelled_at: dt.datetime,
) -> None:
    """KAN-158 — Stock's side of a contract's cancellation, commit-free (the
    consumer harness commits). Under the contract's lock, Stock first records
    that the contract is cancelled, so a confirmation that reaches it later (a
    retried delivery) never reserves the ordered car (contract_is_cancelled),
    then releases whatever the contract still holds. A manual configuration's
    pipeline item is the case this exists for; a stock car's reservation was
    already released by cancel_contract's own synchronous call, so nothing
    matches it any more and nothing is emitted twice. Matching on the holder,
    under a row lock, never touches a reservation another contract has taken
    since.
    """

    lock_contract(db, tenant_id=tenant_id, contract_id=contract_id)
    if not contract_is_cancelled(db, tenant_id=tenant_id, contract_id=contract_id):
        db.add(
            InventoryCancelledContract(
                tenant_id=tenant_id,
                contract_id=contract_id,
                contract_label=contract_label,
                contract_denorm_refreshed_at=cancelled_at,
                cancelled_at=cancelled_at,
            )
        )
        db.flush()

    items = db.scalars(
        select(StockItem)
        .where(
            StockItem.tenant_id == tenant_id,
            StockItem.reserved_by_contract_id == contract_id,
            StockItem.reservation_state == ReservationState.RESERVED,
        )
        .with_for_update()
    ).all()
    for item in items:
        release_and_flush(db, item=item)


def contract_is_cancelled(db: Session, *, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(InventoryCancelledContract.id).where(
                InventoryCancelledContract.tenant_id == tenant_id,
                InventoryCancelledContract.contract_id == contract_id,
            )
        )
        is not None
    )
