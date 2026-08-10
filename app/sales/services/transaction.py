"""Transaction service layer: tenant-scoped CRUD + the /complete and
/cancel state transitions. "Full field-level audit on every state
transition" (spec §4) — every create/update/complete/cancel is logged.

/complete is the only path that mutates Vehicle custody/status, reusing
app.vehicle.public.create_custody_event (via context-seams PR-1) so the
custody chain has one writer, not two competing implementations
(Transaction and Vehicle's own custody-events endpoint). See
complete_transaction's docstring for the cross-context commit-ordering
note — flagged as the same "synchronous in MDM" open question the
original spec called out (§4 open question 9), not silently resolved,
and now also tracked as residual cross-context write coupling for PR-4/5
to replace with events.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BadRequestError, ConflictError
from app.core.pagination import PageParams, build_page, paginate_query
from app.core.tenancy import get_or_404
from app.core.base import utcnow
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.sales.schemas.transaction import TransactionCreate, TransactionUpdate
from app.customer.public import get_customer_or_404
from app.platform.public import get_user_or_404
from app.vehicle.public import CustodyEventType, VehicleStatus, create_custody_event, get_vehicle_or_404
from app.core.audit import record_audit_event

_AUDITED_FIELDS = {
    "transaction_type",
    "customer_id",
    "vehicle_id",
    "primary_user_id",
    "amount",
    "external_ref",
    "notes",
}
_VEHICLE_BLOCKED_SALE_STATUSES = {VehicleStatus.SOLD, VehicleStatus.TOTALED, VehicleStatus.SCRAPPED}


def _plain(value: Any) -> Any:
    """Audit before/after payloads are stored as JSON — Decimal and UUID
    aren't natively JSON-serializable, so both get stringified explicitly
    (Decimal as str to keep full precision, not float).
    """

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value.value if hasattr(value, "value") else value


def get_transaction_or_404(db: Session, tenant_id: uuid.UUID, transaction_id: uuid.UUID) -> Transaction:
    return get_or_404(db, Transaction, transaction_id, tenant_id)


def list_transactions(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID | None,
    vehicle_id: uuid.UUID | None,
    transaction_type: TransactionType | None,
    status: TransactionStatus | None,
    updated_since: dt.datetime | None,
    params: PageParams,
) -> tuple[list[Transaction], str | None]:
    stmt = select(Transaction).where(Transaction.tenant_id == tenant_id)
    if customer_id is not None:
        stmt = stmt.where(Transaction.customer_id == customer_id)
    if vehicle_id is not None:
        stmt = stmt.where(Transaction.vehicle_id == vehicle_id)
    if transaction_type is not None:
        stmt = stmt.where(Transaction.transaction_type == transaction_type)
    if status is not None:
        stmt = stmt.where(Transaction.status == status)
    if updated_since is not None:
        stmt = stmt.where(Transaction.updated_at >= updated_since)
    stmt = paginate_query(stmt, model=Transaction, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def _validate_references(
    db: Session, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, vehicle_id: uuid.UUID, primary_user_id: uuid.UUID
) -> None:
    get_customer_or_404(db, tenant_id, customer_id)
    get_vehicle_or_404(db, vehicle_id)
    get_user_or_404(db, tenant_id, primary_user_id)


def create_transaction(
    db: Session, *, tenant_id: uuid.UUID, data: TransactionCreate, actor_id: uuid.UUID
) -> Transaction:
    _validate_references(
        db,
        tenant_id=tenant_id,
        customer_id=data.customer_id,
        vehicle_id=data.vehicle_id,
        primary_user_id=data.primary_user_id,
    )

    transaction = Transaction(
        tenant_id=tenant_id,
        transaction_type=data.transaction_type,
        status=TransactionStatus.DRAFT,
        customer_id=data.customer_id,
        vehicle_id=data.vehicle_id,
        primary_user_id=data.primary_user_id,
        amount=data.amount,
        external_ref=data.external_ref,
        notes=data.notes,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(transaction)
    db.flush()

    record_audit_event(
        db,
        entity_type="transaction",
        entity_id=transaction.id,
        tenant_id=tenant_id,
        action="create",
        actor_id=actor_id,
        after={field: _plain(getattr(transaction, field)) for field in _AUDITED_FIELDS}
        | {"status": _plain(transaction.status)},
    )
    db.commit()
    db.refresh(transaction)
    return transaction


def update_transaction(
    db: Session, *, transaction: Transaction, data: TransactionUpdate, actor_id: uuid.UUID
) -> Transaction:
    if transaction.status != TransactionStatus.DRAFT:
        raise ConflictError(
            f"Transaction status '{transaction.status.value}' cannot be edited — only draft transactions"
            " may be updated.",
            details={"currentStatus": transaction.status.value},
        )

    changes = data.model_dump(exclude_unset=True)
    _validate_references(
        db,
        tenant_id=transaction.tenant_id,
        customer_id=changes.get("customer_id", transaction.customer_id),
        vehicle_id=changes.get("vehicle_id", transaction.vehicle_id),
        primary_user_id=changes.get("primary_user_id", transaction.primary_user_id),
    )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        current = getattr(transaction, field)
        if current == value:
            continue
        before[field] = _plain(current)
        after[field] = _plain(value)
        setattr(transaction, field, value)

    transaction.updated_by = actor_id
    transaction.version += 1

    if before or after:
        record_audit_event(
            db,
            entity_type="transaction",
            entity_id=transaction.id,
            tenant_id=transaction.tenant_id,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )

    db.commit()
    db.refresh(transaction)
    return transaction


def complete_transaction(db: Session, *, transaction: Transaction, actor_id: uuid.UUID) -> Transaction:
    """The only path that mutates Vehicle custody/status. Transaction and
    Vehicle live in the same database, same session, same process — no real
    cross-service boundary forces two commits, so this issues exactly one
    (`vehicle_service.create_custody_event(..., commit=False)`, then a
    single `db.commit()` here covering the Transaction's own status change
    together with the Vehicle/custody-event mutation). Originally this
    called create_custody_event with its default commit=True, splitting the
    write into two commits with the *dangerous* ordering (Transaction
    committed COMPLETED first, Vehicle mutation second) — a failure in
    between would have left a `completed` Transaction with a Vehicle that
    was never actually mutated, and `status != DRAFT` blocks the normal API
    from ever retrying it. Fixed per CTO review, 2026-08-06.
    """

    if transaction.status != TransactionStatus.DRAFT:
        raise ConflictError(
            f"Transaction status '{transaction.status.value}' cannot be completed — only draft"
            " transactions may be completed.",
            details={"currentStatus": transaction.status.value},
        )
    if transaction.amount is None:
        raise BadRequestError("amount is required before a transaction can be completed.")

    vehicle = get_vehicle_or_404(db, transaction.vehicle_id)

    if transaction.transaction_type == TransactionType.SALE:
        if vehicle.current_custodian_partner_id != transaction.tenant_id:
            raise ConflictError(
                "Cannot complete a sale — this dealer does not currently hold custody of the vehicle.",
                details={"vehicleId": str(vehicle.id)},
            )
        if vehicle.status in _VEHICLE_BLOCKED_SALE_STATUSES:
            raise ConflictError(
                f"Cannot complete a sale — vehicle status '{vehicle.status.value}' does not allow it.",
                details={"vehicleStatus": vehicle.status.value},
            )
        vehicle.status = VehicleStatus.SOLD
        event_type = CustodyEventType.SOLD
    else:  # TRADE_IN — dealer acquires the vehicle from the customer
        vehicle.status = VehicleStatus.IN_STOCK
        event_type = CustodyEventType.ACQUIRED

    before_status = transaction.status
    transaction.status = TransactionStatus.COMPLETED
    transaction.transaction_date = utcnow()
    transaction.updated_by = actor_id
    transaction.version += 1

    record_audit_event(
        db,
        entity_type="transaction",
        entity_id=transaction.id,
        tenant_id=transaction.tenant_id,
        action="complete",
        actor_id=actor_id,
        before={"status": _plain(before_status)},
        after={"status": _plain(transaction.status), "amount": _plain(transaction.amount)},
    )

    # commit=False: this call's Vehicle/custody-event/audit mutations join
    # the single db.commit() below instead of committing on their own.
    # Cross-context write (sales mutating vehicle-owned rows/columns,
    # vehicle.status included above) tracked as residual coupling — see
    # module docstring; PR-4/PR-5 replace this with a published event
    # vehicle consumes idempotently instead of a direct synchronous call.
    create_custody_event(
        db,
        vehicle=vehicle,
        event_type=event_type,
        partner_id=transaction.tenant_id,
        event_date=transaction.transaction_date,
        transaction_id=transaction.id,
        actor_id=actor_id,
        commit=False,
    )

    db.commit()
    db.refresh(transaction)
    return transaction


def cancel_transaction(
    db: Session, *, transaction: Transaction, reason: str | None, actor_id: uuid.UUID
) -> Transaction:
    """Status change only — must never mutate Vehicle custody/status
    (spec §4)."""

    if transaction.status != TransactionStatus.DRAFT:
        raise ConflictError(
            f"Transaction status '{transaction.status.value}' cannot be cancelled — only draft"
            " transactions may be cancelled.",
            details={"currentStatus": transaction.status.value},
        )

    before_status = transaction.status
    transaction.status = TransactionStatus.CANCELLED
    transaction.updated_by = actor_id
    transaction.version += 1

    record_audit_event(
        db,
        entity_type="transaction",
        entity_id=transaction.id,
        tenant_id=transaction.tenant_id,
        action="cancel",
        actor_id=actor_id,
        before={"status": _plain(before_status)},
        after={"status": _plain(transaction.status)},
        reason=reason,
    )
    db.commit()
    db.refresh(transaction)
    return transaction


def repoint_customer_transactions(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> int:
    """Called from app.customer's merge flow via app.sales.public — sales
    stays the sole writer of Transaction.customer_id (one writer per fact)
    even though the merge decision itself belongs to customer. Does not
    commit: joins the caller's transaction, same as before the restructure.
    """

    rows = list(db.scalars(select(Transaction).where(Transaction.customer_id == duplicate_id)).all())
    for txn in rows:
        txn.customer_id = target_id
    return len(rows)
