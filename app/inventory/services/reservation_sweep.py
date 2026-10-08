"""The nightly orphan-reservation sweep (KAN-122; KAN-173): Stock frees a car
whose reservation no longer has a signed contract behind it.

A reservation is an orphan when the contract holding it is not signed —
cancelled, still pending, or missing from Sales altogether (Anto,
2026-10-07: "Contract needs to be signed"). A confirmed or invoiced
contract keeps its car (KAN-173). Since KAN-158 Stock's own
`sales.contract.cancelled` consumer releases what a cancelled contract
holds, so this catches what that path and Sales' synchronous calls left
behind: a confirmation whose compensating release failed, an event that
dead-lettered, a contract Sales no longer has.

ADR-047 (CLAUDE.md rule 12): Sales' read and Stock's write never share a
transaction. Three steps, each on a session of its own:

1. Stock lists its own reserved items, and ends that read.
2. Sales answers the owning contracts' statuses through
   `app.sales.public.get_contract_statuses` (plain data), per tenant.
3. Each orphan is released by `release()` on a session of its own: it
   locks the item, finds it by the reservation id read in step 1, and
   commits — so one failing release never rolls back another. A
   reservation that changed since step 1 (released, or made again under a
   new id) is not found and is skipped. The idempotency key is per
   reservation, as every release's is (KAN-114).

Safety margin: a pending contract's reservation made less than
`PENDING_MARGIN` ago is left for the next run. `confirm_contract` commits
the reservation before its own CONFIRMED commit, so between the two the
contract reads as pending; freeing the car then would leave a signed
contract on a car another contract can take (the KAN-114 end state). The
stock item's `updated_at` is the clock: reserving writes the row, and any
later edit only moves it forward, which can delay a release but never
hasten one. Not covered: a confirmation retried on an already-orphaned
pending contract in the instant between steps 2 and 3 — reserve and release
semantics are KAN-114's, and the confirmed-contract-without-reservation
state that would leave is what KAN-115's check is for.

Not reconciliation: app.core.reconciliation detects and never repairs (P-10,
ADR-079). This is the repair ADR-047 gives the nightly run, registered as a
daily job of its own (app.inventory.daily_jobs) after
`reconciliation.run_all` (app/worker.py), so that, when reconciliation ran
first that day, a reservation naming a missing contract is recorded as a
dangling reference before it is released. The one-hour margin is the
builder's (KAN-122), pending Anto's ruling.
"""

import dataclasses
import datetime as dt
import logging
import uuid

from sqlalchemy import Connection, Engine, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import NotFoundError
from app.inventory.models.stock_item import ReservationState, StockItem
from app.inventory.services.reservation import release
from app.sales.public import ContractStatus, get_contract_statuses

logger = logging.getLogger(__name__)

PENDING_MARGIN = dt.timedelta(hours=1)

# A signed contract keeps its car; every other status, and no contract at
# all, makes the reservation an orphan.
_SIGNED = frozenset({ContractStatus.CONFIRMED, ContractStatus.INVOICED})


@dataclasses.dataclass(frozen=True)
class _Reservation:
    tenant_id: uuid.UUID
    stock_item_id: uuid.UUID
    reservation_id: uuid.UUID
    contract_id: uuid.UUID | None
    reserved_at: dt.datetime


@dataclasses.dataclass
class SweepResult:
    """What one run did. `released` holds stock item ids; the rest count
    reservations: kept for a signed contract, kept inside the pending
    margin, changed since they were read (skipped), or failed to release."""

    released: list[uuid.UUID] = dataclasses.field(default_factory=list)
    kept_signed: int = 0
    kept_recent: int = 0
    changed: int = 0
    failed: list[uuid.UUID] = dataclasses.field(default_factory=list)


def release_orphaned_reservations(db: Session, *, now: dt.datetime | None = None) -> SweepResult:
    """Runs the sweep over every tenant. `db` only supplies the database:
    each step opens a session of its own on `db`'s bind, and nothing is
    read or written on `db` itself."""

    now = now or utcnow()
    bind = db.get_bind()
    result = SweepResult()

    with Session(bind=bind, autoflush=False, expire_on_commit=False) as stock_db:
        reservations = _list_reservations(stock_db)

    by_tenant: dict[uuid.UUID, list[_Reservation]] = {}
    for reservation in reservations:
        by_tenant.setdefault(reservation.tenant_id, []).append(reservation)

    for tenant_id, tenant_reservations in by_tenant.items():
        with Session(bind=bind, autoflush=False, expire_on_commit=False) as sales_db:
            statuses = get_contract_statuses(
                sales_db,
                tenant_id=tenant_id,
                contract_ids=[r.contract_id for r in tenant_reservations if r.contract_id is not None],
            )

        for reservation in tenant_reservations:
            status = statuses.get(reservation.contract_id) if reservation.contract_id is not None else None
            if status in _SIGNED:
                result.kept_signed += 1
            elif status == ContractStatus.PENDING and now - reservation.reserved_at < PENDING_MARGIN:
                result.kept_recent += 1
            else:
                _release_one(bind, reservation, result)

    return result


def _list_reservations(db: Session) -> list[_Reservation]:
    rows = db.execute(
        select(
            StockItem.tenant_id,
            StockItem.id,
            StockItem.active_reservation_id,
            StockItem.reserved_by_contract_id,
            StockItem.updated_at,
        )
        .where(
            StockItem.reservation_state == ReservationState.RESERVED,
            StockItem.active_reservation_id.is_not(None),
        )
        .order_by(StockItem.tenant_id, StockItem.id)
    ).all()
    return [
        _Reservation(
            tenant_id=row.tenant_id,
            stock_item_id=row.id,
            reservation_id=row.active_reservation_id,
            contract_id=row.reserved_by_contract_id,
            reserved_at=row.updated_at,
        )
        for row in rows
    ]


def _release_one(bind: Engine | Connection, reservation: _Reservation, result: SweepResult) -> None:
    with Session(bind=bind, autoflush=False, expire_on_commit=False) as release_db:
        try:
            release(
                release_db,
                tenant_id=reservation.tenant_id,
                reservation_id=reservation.reservation_id,
                idempotency_key=f"inventory.reservation_sweep:{reservation.reservation_id}",
            )
        except NotFoundError:
            release_db.rollback()
            result.changed += 1
        except Exception:
            release_db.rollback()
            logger.exception(
                "inventory.reservation_sweep: release failed",
                extra={
                    "stockItemId": str(reservation.stock_item_id),
                    "reservationId": str(reservation.reservation_id),
                },
            )
            result.failed.append(reservation.stock_item_id)
        else:
            result.released.append(reservation.stock_item_id)

