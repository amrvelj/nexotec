"""Sales' own outbox consumers (WP-8 PR-6) — the first real production
consumer on this side of the codebase (mirrors app.inventory.consumers'
own first-consumer status from WP-7 PR-2).

Registered in app.worker.register_handlers: `inventory.stock_item.purchased`
as "sales.stock_item_purchased", since KAN-144 `inventory.stock_item.added`
as "sales.stock_item_added", since KAN-146 `inventory.stock_item.storno`
as "sales.stock_item_storno", and since KAN-185 `customer.merged` as
"sales.customer_merged".
"""

import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.customer.public import resolve_live_customer_id
from app.inventory.public import is_stock_item_purchased
from app.sales.services.customer_merge import repoint_merged_customer_records
from app.sales.services.pipeline_link import link_manual_configuration_to_pipeline_item
from app.sales.services.stock_item_purchase import record_stock_item_purchased, record_stock_item_storno


def handle_stock_item_purchased_message(db: Session, message: OutboxMessage) -> None:
    """ADR-052 — keeps Sales' local replica of Stock's purchase fact
    (app.sales.services.stock_item_purchase). Flushes only; consume_once
    commits it together with the processed_event row."""

    if message.tenant_id is None:
        raise ValueError(f"inventory.stock_item.purchased message {message.id} has no tenant_id.")
    record_stock_item_purchased(
        db,
        tenant_id=message.tenant_id,
        stock_item_id=message.aggregate_id,
        event_id=message.id,
        stock_item_label=(message.payload or {}).get("stockNumber"),
        recorded_at=message.occurred_at,
    )


def handle_stock_item_storno_message(db: Session, message: OutboxMessage) -> None:
    """KAN-146 (ADR-052: `inventory.stock_item.storno` sets it back) — Stock
    reversed the car's purchase, so Sales' replica row goes and every
    contract on the car derives `is_invoiceable = False`. Stock's fact is
    re-read as it is now (delivery is unordered: a storno may arrive after
    the car was bought again), through Stock's public read — the
    asynchronous consumer's read, never the invoicing path's, which stays
    local (ADR-052). Flushes only; consume_once commits it together with the
    processed_event row."""

    if message.tenant_id is None:
        raise ValueError(f"inventory.stock_item.storno message {message.id} has no tenant_id.")
    tenant_id, stock_item_id = message.tenant_id, message.aggregate_id
    record_stock_item_storno(
        db,
        tenant_id=tenant_id,
        stock_item_id=stock_item_id,
        purchased_in_stock=lambda: is_stock_item_purchased(db, tenant_id=tenant_id, stock_item_id=stock_item_id),
    )


def handle_stock_item_added_message(db: Session, message: OutboxMessage) -> None:
    """KAN-144 — only an item a contract's confirmation created for its
    manually configured vehicle is linked; every other stock item added
    (a direct add, a trade-in) is ignored here."""

    if message.tenant_id is None:
        raise ValueError(f"inventory.stock_item.added message {message.id} has no tenant_id.")
    payload = message.payload or {}
    if payload.get("originRole") != "manual_configuration" or not payload.get("originContractId"):
        return
    link_manual_configuration_to_pipeline_item(
        db,
        tenant_id=message.tenant_id,
        contract_id=uuid.UUID(payload["originContractId"]),
        stock_item_id=message.aggregate_id,
        stock_item_label=payload.get("stockNumber"),
        refreshed_at=message.occurred_at,
    )


def handle_customer_merged_message(db: Session, message: OutboxMessage) -> None:
    """KAN-185 (ADR-047; Anto, 2026-10-31, option A) — a customer merge
    moves everything Sales holds for the duplicate (legacy transactions,
    offers, contracts, deals) to the survivor, here, in Sales' own
    transaction; the merge writes nothing of Sales'. Flushes only;
    consume_once commits it together with the processed_event row.

    Delivery is unordered: when X→D is delivered after D→T, the payload's
    survivor D is itself merged by then. The survivor is therefore re-read
    from its owner (`customer.public.resolve_live_customer_id`, the end of
    D's merge chain), so X's records land on T, never on a merged customer.
    A chain that leaves the group or does not end falls back to the
    payload's survivor."""

    if message.tenant_id is None:
        raise ValueError(f"customer.merged message {message.id} has no tenant_id.")
    payload = message.payload or {}
    duplicate_id = uuid.UUID(payload["customerId"])
    survivor_id = uuid.UUID(payload["duplicateOfCustomerId"])
    target_id = (
        resolve_live_customer_id(db, group_id=message.tenant_id, customer_id=survivor_id) or survivor_id
    )
    repoint_merged_customer_records(db, duplicate_id=duplicate_id, target_id=target_id)
