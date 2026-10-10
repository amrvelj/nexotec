"""KAN-231 — log and limit every read of a vehicle's plate history.

ADR-039 / PRD Vehicles (Halterauskunftssperre, requirement 2): a plate
already held may be resolved to a vehicle, but the plate table must never be
listable, browsable, pageable or exportable. Each plate read here is targeted
(one known vehicle id), yet the vehicle list pages through every vehicle with
no identifier, so the two composed would export the table in N+1 calls.
Anto's ruling (2026-10-07, defaults 2026-10-10): any user may still see any
one vehicle's plate history, but

- every read is written to the append-only audit log (who, which dealership,
  which vehicle, when — never the plate values), and
- a user who has read the plates of `plate_read_limit` DISTINCT vehicles
  inside the rolling `plate_read_window_seconds` is refused a new vehicle; a
  vehicle they already read in the window stays readable and does not count
  again. The refusal is audited too.

The count is taken from those same audit rows, so the log is the one record
of what was read. Concurrent reads by one user are serialised on a
transaction-scoped advisory lock keyed by the user, so two parallel requests
at the limit cannot both slip through.

This is the only module that may call `plate.list_plates_for_vehicle`
(`tests/architecture/test_plate_lookup_is_not_enumerable.py`).
"""

import datetime as dt
import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.config import get_settings
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.services.plate import list_plates_for_vehicle

PLATE_READ_ENTITY_TYPE = "vehicle_plate_history"
ACTION_PLATE_READ = "plate_read"
ACTION_PLATE_READ_REFUSED = "plate_read_refused"
REFUSAL_REASON = "plate_read_limit_reached"


def read_plate_history(
    db: Session, *, actor_id: uuid.UUID, tenant_id: uuid.UUID, vehicle_id: uuid.UUID, purpose: str
) -> list[VehiclePlate] | None:
    """This vehicle's plate history, or `None` when the actor is over the
    limit. Either way the attempt is audited and committed before returning.
    `purpose` names the caller (`plates_tab`, `search_hit`) in the audit
    row's reason.
    """

    if not _authorize(db, actor_id=actor_id, tenant_id=tenant_id, vehicle_id=vehicle_id, purpose=purpose):
        return None
    return list_plates_for_vehicle(db, vehicle_id=vehicle_id)


def _authorize(
    db: Session, *, actor_id: uuid.UUID, tenant_id: uuid.UUID, vehicle_id: uuid.UUID, purpose: str
) -> bool:
    settings = get_settings()
    _lock_actor(db, actor_id)
    since = utcnow() - dt.timedelta(seconds=settings.plate_read_window_seconds)
    already_read = set(
        db.scalars(
            select(AuditEvent.entity_id)
            .where(
                AuditEvent.entity_type == PLATE_READ_ENTITY_TYPE,
                AuditEvent.action == ACTION_PLATE_READ,
                AuditEvent.actor_id == actor_id,
                AuditEvent.created_at >= since,
            )
            .distinct()
        ).all()
    )
    allowed = vehicle_id in already_read or len(already_read) < settings.plate_read_limit
    record_audit_event(
        db,
        entity_type=PLATE_READ_ENTITY_TYPE,
        entity_id=vehicle_id,
        tenant_id=tenant_id,
        action=ACTION_PLATE_READ if allowed else ACTION_PLATE_READ_REFUSED,
        actor_id=actor_id,
        reason=purpose if allowed else f"{purpose}: {REFUSAL_REASON}",
    )
    # Commit here: the audit row must survive whatever the caller does next,
    # and the commit releases the advisory lock.
    db.commit()
    return allowed


def _lock_actor(db: Session, actor_id: uuid.UUID) -> None:
    """pg_advisory_xact_lock on a key derived from the actor; released by
    the commit above. SQLite (the fast local lane, ADR-011) has no advisory
    locks and serialises writers on its own, so this is a no-op there.
    """

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"plate_read:{actor_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
