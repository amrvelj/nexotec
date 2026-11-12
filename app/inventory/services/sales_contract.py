"""Stock's projection of Sales' contracts (KAN-630, ADR-131): what Stock knows
of a contract from Sales' own facts, and what its two rules read.

Fed by four facts Sales publishes through the outbox, one consumer each
(`CONSUMERS`, registered in app/worker.py), and by the backfill
(`apply_backfilled_contract`, `record_backfill`) for contracts older than
the consumers. Every write is a merge that is order-free and idempotent:
the status only rises (`RANK`, cancelled final), the invoice request and
its car are set once, so a redelivered or overtaken fact changes nothing.

A projection is late by the outbox's delay. The two rules it serves would
be weakened by a late answer in one direction each — the sweep freeing a
signed contract's car, «Einkauf stornieren» reversing the purchase of a car
whose invoice was requested — so both read the projection *and the facts
Sales committed that Stock's consumer has not applied yet*
(`_unapplied_messages`): the outbox row is written in Sales' business
transaction (rule 4), so projection plus unapplied facts covers what Sales
has committed — read in the order below, never the other way round. A fact is unapplied while
its consumer has no processed-event row for it and the message is not yet
published (pending, or dead-lettered: then it stays unapplied until it is
replayed, and the rule waits — fail safe). A published message Stock's
consumer never processed predates the consumers; it counts as unapplied
until a backfill run is recorded, after which the backfill covers it.

Read order (KAN-630 review). «Projection plus unapplied facts» is more
than one statement, and under READ COMMITTED each statement sees what was
committed when it began. A consumer commits the projection row and its
processed-event row together. So every reader reads the outbox's unapplied
facts first and the projection after: a fact the first read sees as
applied had its row committed before, so the second read holds it; a fact
applied in between is still unapplied for the first. The reverse order lets
a fact applied between the reads escape both. The backfill record is read
before both (a run commits its rows with its record). With that order the
pair holds every fact Sales had committed when the outbox read began — not a
snapshot of one instant: a fact committed later may or may not be seen, as
with any read that does not lock Sales out.
"""

import datetime as dt
import uuid
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.processed_event_model import ProcessedEvent
from app.core.uuid7 import uuid7
from app.inventory.models.sales_contract import (
    RANK,
    InventorySalesContract,
    InventorySalesContractBackfill,
    ProjectedContractStatus,
)

CREATED = "sales.contract.created"
CONFIRMED = "sales.contract.confirmed"
INVOICE_REQUESTED = "sales.contract.invoice_requested"
CANCELLED = "sales.contract.cancelled"

# Event type -> Stock's consumer of it (app/worker.py registers exactly these).
CONSUMERS: dict[str, str] = {
    CREATED: "inventory.sales_contract_projection_on_created",
    CONFIRMED: "inventory.sales_contract_projection_on_confirmed",
    INVOICE_REQUESTED: "inventory.sales_contract_projection_on_invoice_requested",
    CANCELLED: "inventory.sales_contract_projection_on_cancelled",
}

# The status each fact proves. «Rechnung anfordern» needs a confirmed
# contract and leaves it confirmed (INVOICED is Finance's, WP-9).
_STATUS_OF: dict[str, ProjectedContractStatus] = {
    CREATED: ProjectedContractStatus.PENDING,
    CONFIRMED: ProjectedContractStatus.CONFIRMED,
    INVOICE_REQUESTED: ProjectedContractStatus.CONFIRMED,
    CANCELLED: ProjectedContractStatus.CANCELLED,
}

# Bound parameters per query: far below the driver's limit (65535).
_CHUNK = 1000


@dataclass(frozen=True)
class ContractFact:
    """One contract as a fact or the backfill states it. None means the
    source does not say."""

    tenant_id: uuid.UUID
    contract_id: uuid.UUID
    status: ProjectedContractStatus
    contract_label: str | None = None
    invoice_requested_at: dt.datetime | None = None
    stock_item_id: uuid.UUID | None = None


def apply_contract_fact(db: Session, fact: ContractFact, *, observed_at: dt.datetime) -> bool:
    """Merges `fact` into the projection, commit-free (the consumer harness
    or the backfill commits). Returns whether the row changed.

    The row is created with an `INSERT … ON CONFLICT DO NOTHING` and then
    locked, so two workers applying two facts of one contract at once
    serialise on the row instead of one failing on the unique constraint."""

    insert = postgresql_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    now = utcnow()
    inserted = db.execute(
        insert(InventorySalesContract)
        .values(
            id=uuid7(),
            tenant_id=fact.tenant_id,
            contract_id=fact.contract_id,
            status=fact.status,
            contract_label=fact.contract_label,
            contract_label_refreshed_at=observed_at if fact.contract_label is not None else None,
            stock_item_id=fact.stock_item_id,
            invoice_requested_at=fact.invoice_requested_at,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "contract_id"])
        .returning(InventorySalesContract.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return True

    row = db.scalars(
        select(InventorySalesContract)
        .where(InventorySalesContract.tenant_id == fact.tenant_id, InventorySalesContract.contract_id == fact.contract_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    changed = False
    if RANK[fact.status] > RANK[row.status]:
        row.status = fact.status
        changed = True
    if fact.invoice_requested_at is not None and row.invoice_requested_at is None:
        row.invoice_requested_at = fact.invoice_requested_at
        changed = True
    if fact.stock_item_id is not None and row.stock_item_id is None:
        row.stock_item_id = fact.stock_item_id
        changed = True
    if fact.contract_label is not None and (
        row.contract_label_refreshed_at is None or observed_at > row.contract_label_refreshed_at
    ):
        changed = changed or row.contract_label != fact.contract_label
        row.contract_label = fact.contract_label
        row.contract_label_refreshed_at = observed_at
    if changed:
        row.updated_at = now
    db.flush()
    return changed


def apply_contract_message(db: Session, message: OutboxMessage) -> None:
    """One of Sales' four contract facts, as its consumer receives it. The
    contract is the message's aggregate."""

    if message.tenant_id is None:
        raise ValueError(f"{message.event_type} message {message.id} has no tenant_id.")
    payload = message.payload or {}
    stock_item_id = payload.get("stockItemId") if message.event_type == INVOICE_REQUESTED else None
    apply_contract_fact(
        db,
        ContractFact(
            tenant_id=message.tenant_id,
            contract_id=message.aggregate_id,
            status=_STATUS_OF[message.event_type],
            contract_label=payload.get("contractNumber"),
            invoice_requested_at=message.occurred_at if message.event_type == INVOICE_REQUESTED else None,
            stock_item_id=uuid.UUID(stock_item_id) if stock_item_id else None,
        ),
        observed_at=message.occurred_at,
    )


# --- what the rules read ------------------------------------------------------------


def backfill_recorded(db: Session) -> bool:
    """Whether a backfill run has been committed (`record_backfill`)."""

    return db.scalar(select(exists().select_from(InventorySalesContractBackfill))) or False


def _unapplied(db: Session) -> ColumnElement[bool]:
    """A contract fact Stock's consumer has not applied (module docstring)."""

    not_processed = ~exists().where(
        ProcessedEvent.event_id == OutboxMessage.id,
        ProcessedEvent.consumer_name.in_(list(CONSUMERS.values())),
    )
    if backfill_recorded(db):
        return not_processed & (OutboxMessage.status != OutboxStatus.PUBLISHED)
    return not_processed


def contract_statuses(
    db: Session, *, tenant_id: uuid.UUID, contract_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, ProjectedContractStatus]:
    """The projected status of each of `contract_ids` Stock knows in
    `tenant_id`; an unknown contract is absent."""

    ids = list(dict.fromkeys(contract_ids))
    statuses: dict[uuid.UUID, ProjectedContractStatus] = {}
    for start in range(0, len(ids), _CHUNK):
        rows = db.execute(
            select(InventorySalesContract.contract_id, InventorySalesContract.status).where(
                InventorySalesContract.tenant_id == tenant_id,
                InventorySalesContract.contract_id.in_(ids[start : start + _CHUNK]),
            )
        ).all()
        statuses.update({row.contract_id: row.status for row in rows})
    return statuses


def contracts_with_unapplied_facts(
    db: Session, *, tenant_id: uuid.UUID, contract_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of `contract_ids` have a fact Sales committed that the
    projection does not hold yet: their projected status may be behind."""

    ids = list(dict.fromkeys(contract_ids))
    unapplied = _unapplied(db)
    found: set[uuid.UUID] = set()
    for start in range(0, len(ids), _CHUNK):
        found.update(
            db.scalars(
                select(OutboxMessage.aggregate_id)
                .where(
                    OutboxMessage.tenant_id == tenant_id,
                    OutboxMessage.aggregate_id.in_(ids[start : start + _CHUNK]),
                    OutboxMessage.event_type.in_(list(CONSUMERS)),
                    unapplied,
                )
                .distinct()
            ).all()
        )
    return found


def contract_with_invoice_requested(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> str | None:
    """KAN-146's question, answered by Stock (KAN-630): the number of a
    contract on this car, not cancelled, whose invoice Sales requested —
    held by the projection, or committed by Sales and not applied yet — else
    None. A contract the projection knows as cancelled never counts."""

    # Outbox first, projection second — the order is the safety (module
    # docstring, «Read order»): a request applied between the two reads is
    # either still unapplied for the first or visible to the second.
    cancelled = select(InventorySalesContract.contract_id).where(
        InventorySalesContract.tenant_id == tenant_id,
        InventorySalesContract.contract_id == OutboxMessage.aggregate_id,
        InventorySalesContract.status == ProjectedContractStatus.CANCELLED,
    )
    in_flight = db.scalars(
        select(OutboxMessage)
        .where(
            OutboxMessage.event_type == INVOICE_REQUESTED,
            OutboxMessage.tenant_id == tenant_id,
            OutboxMessage.payload["stockItemId"].as_string() == str(stock_item_id),
            _unapplied(db),
            ~cancelled.exists(),
        )
        .order_by(OutboxMessage.occurred_at)
        .limit(1)
    ).first()

    held = db.scalar(
        select(InventorySalesContract.contract_label)
        .where(
            InventorySalesContract.tenant_id == tenant_id,
            InventorySalesContract.stock_item_id == stock_item_id,
            InventorySalesContract.invoice_requested_at.is_not(None),
            InventorySalesContract.status != ProjectedContractStatus.CANCELLED,
        )
        .order_by(InventorySalesContract.invoice_requested_at)
        .limit(1)
    )
    if held is not None:
        return held
    if in_flight is None:
        return None
    return str((in_flight.payload or {}).get("contractNumber") or in_flight.aggregate_id)


# --- the backfill (scripts/backfill_inventory_sales_contract_projection.py) ---------


def apply_backfilled_contract(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    status: str,
    contract_number: str | None,
    stock_item_id: uuid.UUID | None,
    invoice_requested_at: dt.datetime | None,
    read_at: dt.datetime,
) -> bool:
    """One contract as Sales holds it, merged as any fact is (so a fact
    consumed meanwhile is never overwritten by an older read). `status` is
    Sales' stored value; the car counts only with an invoice request, as the
    event carries it."""

    return apply_contract_fact(
        db,
        ContractFact(
            tenant_id=tenant_id,
            contract_id=contract_id,
            status=ProjectedContractStatus(status),
            contract_label=contract_number,
            invoice_requested_at=invoice_requested_at,
            stock_item_id=stock_item_id if invoice_requested_at is not None else None,
        ),
        observed_at=read_at,
    )


def record_backfill(db: Session, *, contracts_read: int, rows_written: int) -> None:
    """The record that the projection now covers every contract that existed
    when the run read Sales (see `backfill_recorded`). Commit-free."""

    db.add(InventorySalesContractBackfill(contracts_read=contracts_read, rows_written=rows_written))
    db.flush()
