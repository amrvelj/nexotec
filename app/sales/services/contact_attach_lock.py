"""KAN-657 — a buyer attach and the `sales.contact_merged` consumer
serialise on the contact, inside Sales.

The race this closes: an attach read the duplicate as active, the merge
committed, the consumer ran while the attach's UPDATE was still uncommitted
and missed the deal, and the attach then committed on the merged record.

Both sides take one transaction-scoped Postgres advisory lock per contact,
`sales.contact_attach:<contact id>`, owned by Sales:

- the attach (`offer.apply_contact_change`, `contract.create_contract`'s
  contact entry, and the offer and contract PATCH routes) takes the lock of
  the contact it attaches **before** it locks its offer or contract row, then
  reads the contact;
- the consumer takes the lock of the merged duplicate before it reads any
  Sales row naming it.

So an attach that holds the lock first commits before the consumer reads, and
the consumer moves its deal to the survivor; a consumer that holds it first has
run after the merge committed, so the waiting attach then reads the contact
`merged` and is refused (KAN-656). Neither side waits on the other's row while
holding a row the other needs: the lock comes before every row lock on both
sides (offer → contract → deal after it, KAN-147).

Why not a FOR SHARE read of the contact row through `contact.public`: a row
lock taken in Sales' transaction through another context's function is a
shared transaction (ADR-047; `test_adr_047_no_shared_transaction.py` refuses
it), which only an ADR ruling may allow. This lock is Sales' own, on Sales'
own question.

The lock is re-entrant within a transaction, so a route that takes it before
its row lock and the service that takes it again stay correct. SQLite (the fast
local lane, ADR-011) has no advisory locks and serialises writers itself: a
no-op there.
"""

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_contact_for_attach(db: Session, contact_id: uuid.UUID | None) -> None:
    if contact_id is None or db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"sales.contact_attach:{contact_id}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})
