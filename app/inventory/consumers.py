"""The inventory context's outbox consumers. They run wherever the outbox
worker (`python -m app.worker`) is provisioned; `app/worker.py::
register_handlers` is the list of what is registered, with the event type
and `consumer_name` of each:

- `sales.contract.confirmed` -> `inventory.sales_contract_confirmed`
  (`handle_sales_contract_confirmed_message`, WP-7 PR-2): creates the
  pipeline stock item, a trade-in's carrying its valuation pointer (KAN-101).
- `sales.contract.cancelled` -> `inventory.sales_contract_cancelled`
  (`handle_sales_contract_cancelled_message`, KAN-158): releases the
  reservation a manual configuration's pipeline item holds.
- `inventory.stock_item.published` / `.unpublished` -> the marketplace
  transmission consumers `inventory.marketplace_transmission_on_publish` /
  `_on_unpublish` (`handle_stock_item_published_message` /
  `handle_stock_item_unpublished_message`, KAN-27, ADR-062).
- `sales.contract.created` / `.confirmed` / `.invoice_requested` /
  `.cancelled` -> `inventory.sales_contract_projection_on_*`
  (`handle_sales_contract_fact_message`, KAN-630): Stock's projection of the
  contracts its orphan sweep and «Einkauf stornieren» need
  (app.inventory.services.sales_contract; the names are its `CONSUMERS`).
- `platform.dealership.offboarded` -> `inventory.listings_withdrawn_on_offboarded`
  (`handle_dealership_offboarded_message`, KAN-218): every live marketplace
  listing of the offboarded Betrieb withdrawn — an empty feed through the
  gateway, then the listings unpublished (app.inventory.services.listing_withdrawal).
"""

import uuid

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item_publishing import MarketplaceChannel
from app.inventory.services import listing_withdrawal, marketplace_transmission
from app.inventory.services.pipeline import handle_sales_contract_confirmed
from app.inventory.services.reservation import record_contract_cancelled
from app.inventory.services.sales_contract import apply_contract_message


def handle_sales_contract_confirmed_message(db: Session, message: OutboxMessage) -> None:
    if message.tenant_id is None:
        raise ValueError(f"sales.contract.confirmed message {message.id} has no tenant_id.")
    handle_sales_contract_confirmed(db, tenant_id=message.tenant_id, payload=message.payload)


def _handle_marketplace_transmission_message(db: Session, message: OutboxMessage) -> None:
    """Shared by both `inventory.stock_item.published` and
    `inventory.stock_item.unpublished` (KAN-27) — both re-trigger the
    exact same thing: recompute and re-send the complete currently-
    published set for this (tenant, channel). The event's own payload
    only ever carries `channel`; which item triggered it is irrelevant
    once the handler re-derives the full set from the database itself."""

    if message.tenant_id is None:
        raise ValueError(f"{message.event_type} message {message.id} has no tenant_id.")
    channel = MarketplaceChannel(message.payload["channel"])
    marketplace_transmission.assemble_and_transmit(db, tenant_id=message.tenant_id, channel=channel)


def handle_stock_item_published_message(db: Session, message: OutboxMessage) -> None:
    _handle_marketplace_transmission_message(db, message)


def handle_stock_item_unpublished_message(db: Session, message: OutboxMessage) -> None:
    _handle_marketplace_transmission_message(db, message)


def handle_sales_contract_cancelled_message(db: Session, message: OutboxMessage) -> None:
    """KAN-158 — Stock records the cancellation and releases what the
    contract still holds; Stock is the one writer of both. The contract is
    the event's aggregate."""

    if message.tenant_id is None:
        raise ValueError(f"sales.contract.cancelled message {message.id} has no tenant_id.")
    record_contract_cancelled(
        db,
        tenant_id=message.tenant_id,
        contract_id=message.aggregate_id,
        contract_label=message.payload["contractNumber"],
        cancelled_at=message.occurred_at,
    )


def handle_sales_contract_fact_message(db: Session, message: OutboxMessage) -> None:
    """KAN-630 — one of Sales' four contract facts merged into Stock's
    projection (order-free, so redelivery and reordering change nothing; the
    harness makes a redelivery a no-op in the first place)."""

    apply_contract_message(db, message)


def handle_dealership_offboarded_message(db: Session, message: OutboxMessage) -> None:
    """KAN-218 (Anto, 2026-11-14, Q3 A): platform's fact that a Betrieb was
    offboarded; Stock withdraws its live listings on this consumer's own
    session, committed by the harness with the processed-event row."""

    dealership_id = uuid.UUID(message.payload["dealershipId"])
    if message.tenant_id is not None and message.tenant_id != dealership_id:
        raise ValueError(f"platform.dealership.offboarded message {message.id} names two Betriebe.")
    listing_withdrawal.withdraw_live_listings(db, tenant_id=dealership_id)
