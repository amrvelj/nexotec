"""Aftersales' consumers (KAN-837), registered in `app/worker.py::register_handlers`.

- `contact.merged` → `aftersales.contact_merged`: the orders naming the
  duplicate as customer, invoice recipient or driver move to the survivor.
- `vehicle.vehicle_mdm.merged` → `aftersales.vehicle_merged`: the orders
  naming the merged record move to the record the car lives on now.

Both in Aftersales' own transaction (ADR-047), idempotent by eventId through
the consumer harness, and a redelivery finds nothing left to move
(`services/order_references.py`).
"""

import logging

from sqlalchemy.orm import Session

from app.aftersales.services import order_references
from app.core.outbox_model import OutboxMessage

logger = logging.getLogger(__name__)

CONTACT_MERGED_CONSUMER = "aftersales.contact_merged"
VEHICLE_MERGED_CONSUMER = "aftersales.vehicle_merged"


def handle_contact_merged_message(db: Session, message: OutboxMessage) -> None:
    moved = order_references.handle_contact_merged(db, message)
    logger.info("%s: %d workshop order(s) moved", CONTACT_MERGED_CONSUMER, len(moved))


def handle_vehicle_merged_message(db: Session, message: OutboxMessage) -> None:
    moved = order_references.handle_vehicle_merged(db, message)
    logger.info("%s: %d workshop order(s) moved", VEHICLE_MERGED_CONSUMER, len(moved))
