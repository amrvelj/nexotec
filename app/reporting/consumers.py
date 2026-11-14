"""The reporting context's outbox consumers, registered in
`app/worker.py::register_handlers`:

- `inventory.stock_item.website_listing_changed` ->
  `reporting.public_stock_listing` (`handle_website_listing_changed_message`,
  KAN-225): the public stock projection (`services/public_stock.py`).
"""

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.reporting.services.public_stock import apply_listing_changed

CONSUMER_NAME = "reporting.public_stock_listing"
EVENT_TYPE = "inventory.stock_item.website_listing_changed"


def handle_website_listing_changed_message(db: Session, message: OutboxMessage) -> None:
    if message.tenant_id is None:
        raise ValueError(f"{message.event_type} message {message.id} has no tenant_id.")
    apply_listing_changed(db, tenant_id=message.tenant_id, payload=message.payload)
