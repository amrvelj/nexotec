"""The planner's advisory transaction locks, one of each per Betrieb (KAN-808;
ADR-221). No-op on SQLite (the fast lane, ADR-011), which serialises writers
itself.

- `lock_master_data`: every write of a list entry or a resource takes it
  first, so a name check, the «one person resource per user» check and a
  delete's «in use» check cannot race another writer of the same Betrieb.
  The partial unique indexes back the two uniqueness rules for any writer
  that bypasses this.
- `lock_bookings` (ADR-221 (1)): every booking write — book, move, release,
  block — and deactivating a resource takes it before it reads a booking, so
  the overlap rules judge every committed booking (READ COMMITTED: each
  statement after the lock sees what the previous holder committed). There
  is no exclusion constraint: the rules are conditional across rows and the
  warnings need application code anyway. A writer taking both takes the
  master-data lock first.
"""

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def _lock(db: Session, name: str) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(name.encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})


def lock_master_data(db: Session, dealership_id: uuid.UUID) -> None:
    _lock(db, f"planning.master_data:{dealership_id}")


def lock_bookings(db: Session, dealership_id: uuid.UUID) -> None:
    _lock(db, f"planning.bookings:{dealership_id}")
