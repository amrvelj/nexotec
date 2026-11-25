"""One advisory transaction lock per Betrieb for the planner's master data
(KAN-808; ADR-221): every write of a list entry or a resource takes it
first, so a name check, the «one person resource per user» check and a
delete's «in use» check cannot race another writer of the same Betrieb.
The partial unique indexes back the two uniqueness rules for any writer that
bypasses this. Booking writes take their own lock (PR-1c). No-op on SQLite
(the fast lane, ADR-011), which serialises writers itself.
"""

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_master_data(db: Session, dealership_id: uuid.UUID) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"planning.master_data:{dealership_id}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})
