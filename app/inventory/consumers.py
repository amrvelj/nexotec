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
from app.inventory.services.pipeline import handle_sales_contract_confirmed


def handle_sales_contract_confirmed_message(db: Session, message: OutboxMessage) -> None:
    if message.tenant_id is None:
        raise ValueError(f"sales.contract.confirmed message {message.id} has no tenant_id.")
    handle_sales_contract_confirmed(db, tenant_id=message.tenant_id, payload=message.payload)
