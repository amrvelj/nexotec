"""UserPreference service: per-user layout state, keyed by scope.

Not audit-logged like Customer PII writes (FR-11) — a column drag or a
density toggle is UX state, not a business record, and the UI/UX doc is
explicit that preference writes must never block or interrupt the user.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.platform.models.user_preference import UserPreference

# UI/UX Core Principles § User-Level Preference Persistence: "64 KB per
# scope, enforced server-side with 422."
MAX_PAYLOAD_BYTES = 64 * 1024


def list_preferences(db: Session, *, user_id: uuid.UUID) -> list[UserPreference]:
    stmt = select(UserPreference).where(UserPreference.user_id == user_id).order_by(UserPreference.scope)
    return list(db.scalars(stmt).all())


def get_preference(db: Session, *, user_id: uuid.UUID, scope: str) -> UserPreference | None:
    return db.get(UserPreference, (user_id, scope))


def put_preference(
    db: Session, *, user_id: uuid.UUID, scope: str, payload: dict, schema_version: int
) -> UserPreference:
    size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise UnprocessableEntityError(
            f"Preference payload for scope '{scope}' is {size} bytes, exceeding the {MAX_PAYLOAD_BYTES}-byte limit.",
            details={"scope": scope, "sizeBytes": size, "maxBytes": MAX_PAYLOAD_BYTES},
        )

    pref = db.get(UserPreference, (user_id, scope))
    if pref is None:
        pref = UserPreference(user_id=user_id, scope=scope, payload=payload, schema_version=schema_version)
        db.add(pref)
    else:
        # Last-write-wins, no version check — see model docstring.
        pref.payload = payload
        pref.schema_version = schema_version

    db.commit()
    db.refresh(pref)
    return pref


def delete_preference(db: Session, *, user_id: uuid.UUID, scope: str) -> None:
    """Idempotent: deleting an already-absent scope is not an error — the
    caller's intent ("reset to module default") is already satisfied.
    """

    pref = db.get(UserPreference, (user_id, scope))
    if pref is not None:
        db.delete(pref)
        db.commit()
