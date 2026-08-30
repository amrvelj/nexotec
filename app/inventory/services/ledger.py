"""The Wagenbuch command surface (WP-7 PR-6). "Building this now is what
stops aftersales writing into Stock's tables later — five of the eleven
Wagenbuch cost categories originate in contexts that do not exist yet."
recordCost is exposed for two callers today: this PR's own manual-
booking-by-hand UI path (FR-I-15a), and — once WP-8/9/10/11 exist — every
future context that needs to post a cost or revenue against a vehicle,
never given direct table access.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_ledger import (
    AUTOMATIC_ONLY_CATEGORIES,
    DIRECTION_BY_CATEGORY,
    LedgerCategory,
    StockItemLedger,
)


def is_period_closed(tenant_id: uuid.UUID, occurred_at: dt.datetime) -> bool:
    """Stubbed — no app.finance/closed-accounting-period concept exists
    yet. Always False until WP-8+ wires a real one; recordCost already
    calls through this function so that wiring is a one-line change, not
    a new call site to find.
    """

    return False


def record_cost(
    db: Session,
    *,
    item: StockItem,
    category: LedgerCategory,
    amount: Decimal,
    occurred_at: dt.datetime,
    source_ref: str,
    actor_id: uuid.UUID | None,
    is_auto: bool = False,
) -> StockItemLedger:
    """Idempotent by (tenant_id, source_ref) — a duplicate submit (the same
    client-generated key resent) returns the EXISTING row, never a 422 and
    never a second entry.
    """

    existing = db.scalar(
        select(StockItemLedger).where(
            StockItemLedger.tenant_id == item.tenant_id, StockItemLedger.source_ref == source_ref
        )
    )
    if existing is not None:
        return existing

    if category in AUTOMATIC_ONLY_CATEGORIES and not is_auto:
        raise UnprocessableEntityError(
            f"'{category.value}' may only be booked automatically, never by hand.",
            details={"category": category.value},
        )
    if item.left_stock_at is not None:
        raise UnprocessableEntityError(
            "This stock item has been sold — its Wagenbuch is closed to new entries.",
            details={"stockItemId": str(item.id)},
        )
    if is_period_closed(item.tenant_id, occurred_at):
        raise UnprocessableEntityError(
            "The accounting period for this date is closed.", details={"occurredAt": str(occurred_at)}
        )

    entry = StockItemLedger(
        tenant_id=item.tenant_id,
        stock_item_id=item.id,
        category=category,
        direction=DIRECTION_BY_CATEGORY[category],
        amount=amount,
        occurred_at=occurred_at,
        source_ref=source_ref,
        is_auto=is_auto,
        created_by=actor_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_ledger_entries(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> list[StockItemLedger]:
    return list(
        db.scalars(
            select(StockItemLedger)
            .where(StockItemLedger.tenant_id == tenant_id, StockItemLedger.stock_item_id == stock_item_id)
            .order_by(StockItemLedger.occurred_at.desc(), StockItemLedger.created_at.desc())
        ).all()
    )
