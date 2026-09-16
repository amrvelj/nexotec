"""The inventory context's outbox consumers (WP-7 PR-2) — the first real
production consumer registration in the codebase. Every prior context
(app.worker.py::register_handlers) ships this deliberately empty; only a
test and a CI smoke probe exercise the mechanism so far.

Registered in app.worker.register_handlers as:

    transport.register(
        "sales.contract.confirmed",
        consumer_name="inventory.sales_contract_confirmed",
        handler=handle_sales_contract_confirmed_message,
    )
"""

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item_publishing import MarketplaceChannel
from app.inventory.services import marketplace_transmission
from app.inventory.services.pipeline import handle_sales_contract_confirmed


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
