"""Offer/contract number allocation (WP-8 PR-1). Row-lock-then-increment,
one counter per (tenant_id, series) — the same idiom as
app.inventory.services.stock_item.allocate_stock_number,
app.vehicle.services.vehicle_mdm.allocate_vehicle_number, and
app.customer.services.customer._allocate_customer_number. Scoped per
TENANT (dealership), like stock numbers, since offers and contracts are
this dealership's own paperwork, not a group or global fact.

KAN-207 (ADR-109): the offer's number comes from the Betrieb's `offer` number
range in Platform, handed out inside this transaction
(`platform.public.allocate_number`) — a rolled back offer gives its number
back. The range continues the sequence this module's own `offer` counter
reached (Anto, 2026-11-01, option 1): during the switch that counter is
locked, passed as `continue_after` and kept in step (`allocate_offer_number`),
so an instance still running the old code during a rolling deploy never
repeats a number. The legacy transaction import keeps this module's own
counter (`allocate_legacy_offer_number`). Contracts keep this module's
counter until they switch in their own ticket.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.platform.public import NumberRangeDocumentType, allocate_number
from app.sales.models.deal import SalesNumberSequence

_OFFER_SERIES = "offer"
_CONTRACT_SERIES = "contract"


def _locked_counter(db: Session, *, tenant_id: uuid.UUID, series: str) -> SalesNumberSequence:
    key = (tenant_id, series)
    row = db.get(SalesNumberSequence, key, with_for_update=True)
    if row is None:
        # First use of this key. A concurrent first caller can insert the same
        # row: its commit turns our INSERT into a UniqueViolation (KAN-70). The
        # savepoint keeps the caller's transaction alive through that, and the
        # locked re-read below then waits for and takes the winner's row.
        # Flush the caller's own pending rows first, so that the except below
        # can only ever see this counter row's INSERT.
        db.flush()
        try:
            with db.begin_nested():
                db.add(SalesNumberSequence(tenant_id=tenant_id, series=series, next_value=1))
        except IntegrityError:
            pass  # the concurrent caller's row now exists; re-read it below
        row = db.get(SalesNumberSequence, key, with_for_update=True)
        assert row is not None, "SalesNumberSequence row missing after its first-use INSERT or the concurrent winner's"
    return row


def _allocate(db: Session, *, tenant_id: uuid.UUID, series: str, prefix: str) -> str:
    row = _locked_counter(db, tenant_id=tenant_id, series=series)
    value = row.next_value
    row.next_value += 1
    db.flush()
    return f"{prefix}-{value:06d}"


def legacy_offer_counters(db: Session) -> dict[uuid.UUID, int]:
    """Every Betrieb's `last_legacy_offer_counter` that is set — what the
    one-off `scripts/continue_offer_number_ranges.py` hands to Platform."""

    rows = db.scalars(
        select(SalesNumberSequence).where(
            SalesNumberSequence.series == _OFFER_SERIES, SalesNumberSequence.next_value > 1
        )
    ).all()
    return {row.tenant_id: row.next_value - 1 for row in rows}


def allocate_offer_number(db: Session, tenant_id: uuid.UUID) -> str:
    """From the Betrieb's `offer` range in Platform (KAN-207, ADR-109).

    During the switch this module's own `offer` counter is locked first and
    kept in step with what the range hands out, and passed as
    `continue_after`: an instance still running the old numbering (a rolling
    deploy) takes the same row lock and numbers after the range, and the
    range never numbers at or below what that instance handed out — so no
    offer number repeats either way. Lock order: Sales' counter row, then
    Platform's range row, everywhere. Retiring the old counter once every
    instance runs this code is KAN-540."""

    legacy = _locked_counter(db, tenant_id=tenant_id, series=_OFFER_SERIES)
    allocated = allocate_number(
        db,
        dealership_id=tenant_id,
        document_type=NumberRangeDocumentType.OFFER,
        continue_after=legacy.next_value - 1 if legacy.next_value > 1 else None,
    )
    if allocated.counter + 1 > legacy.next_value:
        legacy.next_value = allocated.counter + 1
        db.flush()
    return allocated.number


def allocate_legacy_offer_number(db: Session, tenant_id: uuid.UUID) -> str:
    """The legacy transaction import's offer number. No caller since KAN-79
    deleted that import; KAN-540 removes it with the counter floor. It was
    this module's own counter, as before KAN-207 — history neither consumes
    nor freezes the Betrieb's offer range (KAN-207, settled (b)); the range
    continues after it, since it never goes below this counter."""

    return _allocate(db, tenant_id=tenant_id, series=_OFFER_SERIES, prefix="O")


def allocate_contract_number(db: Session, tenant_id: uuid.UUID) -> str:
    return _allocate(db, tenant_id=tenant_id, series=_CONTRACT_SERIES, prefix="C")
