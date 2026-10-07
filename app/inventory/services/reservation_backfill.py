"""KAN-166 — backfill for the manual configurations Stock handled before
KAN-158 (PRD-Stock K-12, FR-I-11).

Before KAN-158, a manual configuration's pipeline item
(`pipeline_ref = contract:<id>:manual`) was created unreserved, and
Stock had no consumer for `sales.contract.cancelled`, so no cancellation
was recorded in `inventory_cancelled_contract`. The live consumers never
repair either: a re-emitted confirmation never reserves an item that
already exists (so a cancelled contract cannot get its car back).

WHERE STOCK LEARNS A CONTRACT'S STATE — from the events Sales published,
never from Sales' tables. Every `sales.contract.confirmed` and
`sales.contract.cancelled` is still in the outbox (nothing deletes outbox
rows), and they are Sales' public facts: reading them is what the consumer
would have done had it existed. A contract with a confirmation and no
cancellation is still live — confirmed or invoiced — and its ordered car
is reserved, also once it has left stock (an invoiced car is sold to that
customer and must not be offered to another; Anto, 2026-10-07).

TWO PHASES, ADR-047. Phase 1 reads the event log and Stock's manual items
in one read-only pass, then ends that transaction. Phase 2 writes one
contract per Stock transaction, under the same per-contract advisory lock
the KAN-158 consumers take, re-reading Stock's own state under it. Nothing
outside Stock is read inside a write transaction.

Per contract:
- live, item unreserved, no cancellation recorded, never released -> reserved
  for it (reserve_and_flush, which publishes `inventory.stock_item.reserved`).
- live, but Stock released the item before (an `inventory.stock_item.released`
  for it) -> reported, never re-reserved: someone released it on purpose.
- live, item already reserved by it -> nothing.
- item reserved by another contract -> reported, never overwritten.
- cancelled, not yet recorded -> record_contract_cancelled, the consumer's
  own step (the event's contract number as rule 2's label, its occurred_at
  as cancelled_at), for every cancelled contract, manual or not; the item
  stays unreserved, and anything the contract still holds is released (the
  report line says how many). Already recorded -> nothing.
- an item with no confirmation event, a tenant that differs from the
  event's, or a cancellation event without a tenant -> reported, never
  written (the contract is never treated as live).

Re-running is a no-op: every write is guarded by the state it produces.
`commit=False` (the default of the script) runs each contract's
transaction and rolls it back, so the report shows exactly what a real
run would do and nothing lands.
"""

import dataclasses
import datetime as dt
import enum
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import ReservationState, StockItem
from app.inventory.services.reservation import (
    contract_is_cancelled,
    lock_contract,
    record_contract_cancelled,
    reserve_and_flush,
)

_MANUAL_REF_PREFIX = "contract:"
_MANUAL_REF_SUFFIX = ":manual"


class BackfillOutcome(str, enum.Enum):
    RESERVED = "reserved"
    ALREADY_RESERVED = "already_reserved"
    CANCELLATION_RECORDED = "cancellation_recorded"
    CANCELLATION_ALREADY_RECORDED = "cancellation_already_recorded"
    # Reported for a person to look at; never written.
    HELD_BY_OTHER_CONTRACT = "held_by_other_contract"
    CANCELLATION_RECORDED_WITHOUT_EVENT = "cancellation_recorded_without_event"
    NO_CONFIRMATION_EVENT = "no_confirmation_event"
    TENANT_MISMATCH = "tenant_mismatch"
    CANCELLATION_WITHOUT_TENANT = "cancellation_without_tenant"
    RELEASED_BEFORE = "released_before"


_NEEDS_ATTENTION = {
    BackfillOutcome.HELD_BY_OTHER_CONTRACT,
    BackfillOutcome.CANCELLATION_RECORDED_WITHOUT_EVENT,
    BackfillOutcome.NO_CONFIRMATION_EVENT,
    BackfillOutcome.TENANT_MISMATCH,
    BackfillOutcome.CANCELLATION_WITHOUT_TENANT,
    BackfillOutcome.RELEASED_BEFORE,
}


@dataclasses.dataclass(frozen=True)
class BackfillLine:
    contract_id: uuid.UUID
    tenant_id: uuid.UUID | None
    contract_label: str | None
    stock_item_id: uuid.UUID | None
    outcome: BackfillOutcome
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class BackfillReport:
    committed: bool
    lines: list[BackfillLine]

    @property
    def needs_attention(self) -> list[BackfillLine]:
        return [line for line in self.lines if line.outcome in _NEEDS_ATTENTION]

    def counts(self) -> dict[BackfillOutcome, int]:
        return {outcome: sum(1 for line in self.lines if line.outcome == outcome) for outcome in BackfillOutcome}


@dataclasses.dataclass(frozen=True)
class _Cancellation:
    tenant_id: uuid.UUID | None
    contract_label: str
    cancelled_at: dt.datetime


@dataclasses.dataclass(frozen=True)
class _ManualItem:
    stock_item_id: uuid.UUID
    tenant_id: uuid.UUID


def _contract_id_from_ref(pipeline_ref: str) -> uuid.UUID | None:
    if not (pipeline_ref.startswith(_MANUAL_REF_PREFIX) and pipeline_ref.endswith(_MANUAL_REF_SUFFIX)):
        return None
    try:
        return uuid.UUID(pipeline_ref[len(_MANUAL_REF_PREFIX) : -len(_MANUAL_REF_SUFFIX)])
    except ValueError:
        return None


def _read_phase(
    db: Session,
) -> tuple[dict[uuid.UUID, _ManualItem], dict[uuid.UUID, uuid.UUID | None], dict[uuid.UUID, _Cancellation]]:
    """Phase 1: Stock's manual items, and Sales' published confirmations and
    cancellations, keyed by contract id. A contract's earliest cancellation
    with a tenant wins when Sales emitted it twice."""

    items: dict[uuid.UUID, _ManualItem] = {}
    for item_id, tenant_id, pipeline_ref in db.execute(
        select(StockItem.id, StockItem.tenant_id, StockItem.pipeline_ref).where(
            StockItem.pipeline_ref.like(f"{_MANUAL_REF_PREFIX}%{_MANUAL_REF_SUFFIX}")
        )
    ):
        contract_id = _contract_id_from_ref(pipeline_ref)
        if contract_id is not None:
            items[contract_id] = _ManualItem(stock_item_id=item_id, tenant_id=tenant_id)

    confirmations: dict[uuid.UUID, uuid.UUID | None] = {}
    for contract_id, tenant_id in db.execute(
        select(OutboxMessage.aggregate_id, OutboxMessage.tenant_id).where(
            OutboxMessage.event_type == "sales.contract.confirmed"
        )
    ):
        confirmations[contract_id] = tenant_id

    cancellations: dict[uuid.UUID, _Cancellation] = {}
    for message in db.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.event_type == "sales.contract.cancelled")
        .order_by(OutboxMessage.occurred_at)
    ):
        known = cancellations.get(message.aggregate_id)
        if known is not None and (known.tenant_id is not None or message.tenant_id is None):
            continue
        cancellations[message.aggregate_id] = _Cancellation(
            tenant_id=message.tenant_id,
            contract_label=message.payload["contractNumber"],
            cancelled_at=message.occurred_at,
        )
    return items, confirmations, cancellations


def _backfill_cancelled(
    db: Session, *, contract_id: uuid.UUID, cancellation: _Cancellation, item: _ManualItem | None
) -> BackfillLine:
    def line(outcome: BackfillOutcome, detail: str = "") -> BackfillLine:
        return BackfillLine(
            contract_id=contract_id,
            tenant_id=cancellation.tenant_id,
            contract_label=cancellation.contract_label,
            stock_item_id=item.stock_item_id if item else None,
            outcome=outcome,
            detail=detail,
        )

    tenant_id = cancellation.tenant_id
    if tenant_id is None:
        return line(BackfillOutcome.CANCELLATION_WITHOUT_TENANT)
    if item is not None and item.tenant_id != tenant_id:
        return line(BackfillOutcome.TENANT_MISMATCH, f"item tenant {item.tenant_id}")

    lock_contract(db, tenant_id=tenant_id, contract_id=contract_id)
    if contract_is_cancelled(db, tenant_id=tenant_id, contract_id=contract_id):
        return line(BackfillOutcome.CANCELLATION_ALREADY_RECORDED)
    held = db.scalar(
        select(func.count())
        .select_from(StockItem)
        .where(
            StockItem.tenant_id == tenant_id,
            StockItem.reserved_by_contract_id == contract_id,
            StockItem.reservation_state == ReservationState.RESERVED,
        )
    )
    record_contract_cancelled(
        db,
        tenant_id=tenant_id,
        contract_id=contract_id,
        contract_label=cancellation.contract_label,
        cancelled_at=cancellation.cancelled_at,
    )
    return line(BackfillOutcome.CANCELLATION_RECORDED, f"released {held} item(s)" if held else "")


def _backfill_live(
    db: Session, *, contract_id: uuid.UUID, item: _ManualItem, confirmed_tenant_id: uuid.UUID | None, confirmed: bool
) -> BackfillLine:
    def line(outcome: BackfillOutcome, detail: str = "") -> BackfillLine:
        return BackfillLine(
            contract_id=contract_id,
            tenant_id=item.tenant_id,
            contract_label=None,
            stock_item_id=item.stock_item_id,
            outcome=outcome,
            detail=detail,
        )

    if not confirmed:
        return line(BackfillOutcome.NO_CONFIRMATION_EVENT)
    if confirmed_tenant_id != item.tenant_id:
        return line(BackfillOutcome.TENANT_MISMATCH, f"confirmation tenant {confirmed_tenant_id}")

    lock_contract(db, tenant_id=item.tenant_id, contract_id=contract_id)
    if contract_is_cancelled(db, tenant_id=item.tenant_id, contract_id=contract_id):
        # Stock recorded a cancellation Sales' log does not show.
        return line(BackfillOutcome.CANCELLATION_RECORDED_WITHOUT_EVENT)
    stock_item = db.scalar(
        select(StockItem)
        .where(StockItem.id == item.stock_item_id, StockItem.tenant_id == item.tenant_id)
        .with_for_update()
    )
    assert stock_item is not None  # read in phase 1; stock items are never deleted
    if stock_item.reservation_state == ReservationState.RESERVED:
        if stock_item.reserved_by_contract_id == contract_id:
            return line(BackfillOutcome.ALREADY_RESERVED)
        return line(BackfillOutcome.HELD_BY_OTHER_CONTRACT, f"reserved by contract {stock_item.reserved_by_contract_id}")
    released_before = db.scalar(
        select(OutboxMessage.id)
        .where(
            OutboxMessage.event_type == "inventory.stock_item.released",
            OutboxMessage.aggregate_id == stock_item.id,
        )
        .limit(1)
    )
    if released_before is not None:
        return line(BackfillOutcome.RELEASED_BEFORE)
    # An item that has left stock is reserved too: it is this contract's
    # invoiced car (Anto, 2026-10-07).
    reserve_and_flush(db, item=stock_item, contract_id=contract_id)
    return line(BackfillOutcome.RESERVED)


def backfill_manual_configuration_reservations(db: Session, *, commit: bool) -> BackfillReport:
    items, confirmations, cancellations = _read_phase(db)
    db.rollback()  # end phase 1's read transaction before any write

    lines: list[BackfillLine] = []
    for contract_id in sorted(set(items) | set(cancellations)):
        try:
            if contract_id in cancellations:
                line = _backfill_cancelled(
                    db, contract_id=contract_id, cancellation=cancellations[contract_id], item=items.get(contract_id)
                )
            else:
                line = _backfill_live(
                    db,
                    contract_id=contract_id,
                    item=items[contract_id],
                    confirmed_tenant_id=confirmations.get(contract_id),
                    confirmed=contract_id in confirmations,
                )
            if commit:
                db.commit()
            else:
                db.rollback()
        except BaseException:
            db.rollback()
            raise
        lines.append(line)
    return BackfillReport(committed=commit, lines=lines)
