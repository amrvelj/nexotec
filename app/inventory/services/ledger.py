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
from app.core.uuid7 import uuid7
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_ledger import (
    AUTOMATIC_ONLY_CATEGORIES,
    DIRECTION_BY_CATEGORY,
    LedgerCategory,
    StockItemLedger,
)

# KAN-146: the source_refs the purchase booking and its storno write. A hand
# booking may not use them (record_cost refuses), so the reversal's key —
# `storno:` + the reversed entry's id — can never be taken first by hand.
PURCHASE_SOURCE_REF_PREFIX = "purchase:"
STORNO_SOURCE_REF_PREFIX = "storno:"
_RESERVED_SOURCE_REF_PREFIXES = (PURCHASE_SOURCE_REF_PREFIX, STORNO_SOURCE_REF_PREFIX)


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

    # Before the replay lookup: a hand booking naming a purchase or storno key
    # would otherwise be answered with that automatic entry.
    if not is_auto and source_ref.startswith(_RESERVED_SOURCE_REF_PREFIXES):
        raise UnprocessableEntityError(
            "This sourceRef is reserved for the purchase booking and its storno.",
            details={"reason": "source_ref_reserved", "field": "sourceRef"},
        )
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
            details={"reason": "category_automatic_only", "category": category.value},
        )
    entry = _add_entry(
        db,
        item=item,
        category=category,
        amount=amount,
        occurred_at=occurred_at,
        source_ref=source_ref,
        actor_id=actor_id,
        is_auto=is_auto,
    )
    db.commit()
    db.refresh(entry)
    return entry


def _add_entry(
    db: Session,
    *,
    item: StockItem,
    category: LedgerCategory,
    amount: Decimal,
    occurred_at: dt.datetime,
    source_ref: str,
    actor_id: uuid.UUID | None,
    is_auto: bool,
) -> StockItemLedger:
    """The one writer of a Wagenbuch row: the closed-book and closed-period
    refusals, then the append. Flushes only — record_cost commits its own;
    the purchase booking and its storno commit with the item (KAN-146)."""

    if item.left_stock_at is not None:
        raise UnprocessableEntityError(
            "This stock item has been sold — its Wagenbuch is closed to new entries.",
            details={"reason": "wagenbuch_closed", "stockItemId": str(item.id)},
        )
    if is_period_closed(item.tenant_id, occurred_at):
        raise UnprocessableEntityError(
            "The accounting period for this date is closed.",
            details={"reason": "accounting_period_closed", "occurredAt": str(occurred_at)},
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
    db.flush()
    return entry


def post_purchase_entries(
    db: Session, *, item: StockItem, occurred_at: dt.datetime, actor_id: uuid.UUID | None
) -> list[StockItemLedger]:
    """KAN-146 (FR-I-15a: the Einstandspreis is an automatic position): the
    purchase booking posts the purchase price as `einstandspreis`, tagged
    automatic, in the booking's own transaction. One key per booking
    (`purchase:<uuid7>`), so a car bought, stornoed and bought again has one
    entry per booking and its storno reverses exactly the one it undoes.
    Landed cost is not posted here: Zoll / Steuern / Transport stay the
    hand-bookable `landed_cost` category (FR-I-15a), and the stock item's own
    `landed_cost` figure is the margin's cost basis (Sales' pricing), not a
    Wagenbuch line."""

    if item.purchase_price is None:
        return []
    return [
        _add_entry(
            db,
            item=item,
            category=LedgerCategory.EINSTANDSPREIS,
            amount=item.purchase_price,
            occurred_at=occurred_at,
            source_ref=f"{PURCHASE_SOURCE_REF_PREFIX}{uuid7()}",
            actor_id=actor_id,
            is_auto=True,
        )
    ]


def reverse_purchase_entries(
    db: Session, *, item: StockItem, occurred_at: dt.datetime, actor_id: uuid.UUID | None
) -> list[StockItemLedger]:
    """KAN-146 (FR-I-05: the acquisition entries are reversed by append, never
    edited): one counter-entry — same category, negated amount — for every
    `einstandspreis` entry of this item not reversed yet, keyed
    `storno:<the reversed entry's id>`, so the counter-entry names the entry
    it reverses and a second reversal of it cannot be written (an id, not the
    entry's own key: a hand-booked key may already use the column's 200
    characters). That is the
    booking's automatic entry and, for a car bought before KAN-146, the
    Einstandspreis a colleague booked by hand then (the category was
    hand-bookable until KAN-146); after the storno the car's Einstandspreis
    nets to zero either way. Counter-entries themselves are never reversed."""

    entries = list(
        db.scalars(
            select(StockItemLedger)
            .where(
                StockItemLedger.tenant_id == item.tenant_id,
                StockItemLedger.stock_item_id == item.id,
                StockItemLedger.category == LedgerCategory.EINSTANDSPREIS,
                ~StockItemLedger.source_ref.startswith(STORNO_SOURCE_REF_PREFIX, autoescape=True),
            )
            .order_by(StockItemLedger.created_at, StockItemLedger.id)
        ).all()
    )
    if not entries:
        return []
    reversed_refs = set(
        db.scalars(
            select(StockItemLedger.source_ref).where(
                StockItemLedger.tenant_id == item.tenant_id,
                StockItemLedger.source_ref.in_([f"{STORNO_SOURCE_REF_PREFIX}{entry.id}" for entry in entries]),
            )
        ).all()
    )
    return [
        _add_entry(
            db,
            item=item,
            category=entry.category,
            amount=-entry.amount,
            occurred_at=occurred_at,
            source_ref=f"{STORNO_SOURCE_REF_PREFIX}{entry.id}",
            actor_id=actor_id,
            is_auto=True,
        )
        for entry in entries
        if f"{STORNO_SOURCE_REF_PREFIX}{entry.id}" not in reversed_refs
    ]


def list_ledger_entries(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> list[StockItemLedger]:
    return list(
        db.scalars(
            select(StockItemLedger)
            .where(StockItemLedger.tenant_id == tenant_id, StockItemLedger.stock_item_id == stock_item_id)
            .order_by(StockItemLedger.occurred_at.desc(), StockItemLedger.created_at.desc())
        ).all()
    )


def purchase_booked_after(
    db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID, after: dt.datetime
) -> bool:
    """KAN-572: whether this Betrieb booked a purchase of the stock item after
    `after` — an automatic `einstandspreis` entry keyed `purchase:` (one per
    booking, KAN-146), written later. The customer context's
    `customer.trade_in_storno` asks it with the storno event's `occurred_at`:
    delivery is unordered after a failed delivery, and a storno that a later
    booking overtook — whether that booking still stands or was stornoed in
    turn — is stale and must not reopen the customer as holder. A SELECT only;
    an item of another dealership, or none, is False."""

    return (
        db.scalar(
            select(StockItemLedger.id)
            .where(
                StockItemLedger.tenant_id == tenant_id,
                StockItemLedger.stock_item_id == stock_item_id,
                StockItemLedger.is_auto.is_(True),
                StockItemLedger.source_ref.startswith(PURCHASE_SOURCE_REF_PREFIX, autoescape=True),
                StockItemLedger.created_at > after,
            )
            .limit(1)
        )
        is not None
    )

