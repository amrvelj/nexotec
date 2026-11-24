"""KAN-465 — a purchase booking and the `inventory.customer_merged` consumer
serialise on the supplier contact, inside Stock (the pattern of Sales'
KAN-657, `app/sales/services/customer_attach_lock.py`).

The race this closes: a booking read its supplier as live, the contact merge
committed, the consumer ran while the booking's UPDATE was still uncommitted
and missed the item, and the booking then committed naming the merged
contact — which neither the consumer (already run) nor the nightly label
refresh (it leaves a merged contact alone) would ever move.

Both sides take one transaction-scoped Postgres advisory lock per contact,
`inventory.supplier_contact:<contact id>`, owned by Stock:

- the booking (the purchase route, before it locks the stock item row, and
  `record_purchase` again before it reads the contact);
- the consumer, for the merged duplicate, before it reads any stock item
  naming it.

A booking that holds the lock first commits before the consumer reads, and
the consumer moves its item; a consumer that holds it first ran after the
merge committed, so the waiting booking then reads the contact merged and is
refused (422 `supplier_contact_merged`). The lock comes before every row lock
on both sides. A FOR SHARE read of the contact row through `customer.public`
would be a shared transaction (ADR-047). Re-entrant within a transaction; a
no-op on SQLite (the fast lane, ADR-011), which serialises writers itself.
"""

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_supplier_contact(db: Session, contact_id: uuid.UUID | None) -> None:
    if contact_id is None or db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"inventory.supplier_contact:{contact_id}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})
