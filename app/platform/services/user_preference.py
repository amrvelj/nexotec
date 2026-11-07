"""UserPreference service: per-user layout state, keyed by scope.

Not audit-logged like Customer PII writes (FR-11) — a column drag or a
density toggle is UX state, not a business record, and the UI/UX doc is
explicit that preference writes must never block or interrupt the user.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.platform.models.dealership import Dealership
from app.platform.models.user import User
from app.platform.models.user_preference import UserPreference

# UI/UX Core Principles § User-Level Preference Persistence: "64 KB per
# scope, enforced server-side with 422."
MAX_PAYLOAD_BYTES = 64 * 1024


UI_SCOPE = "ui"


def default_ui_language(db: Session, *, user_id: uuid.UUID) -> str | None:
    """KAN-104 item 2 (Anto, 2026-11-03, decision 1 B): the language a user
    who has never chosen one starts in — their home Betrieb's correspondence
    language. Only a starting value: the user's own choice is the `ui`
    preference's `uiLanguage`, a field of its own (CLAUDE.md: the customer's
    correspondence language is not the user's UI language). None for a user
    row that no longer exists."""

    language = db.scalar(
        select(Dealership.default_correspondence_language)
        .join(User, User.tenant_id == Dealership.id)
        .where(User.id == user_id)
    )
    return language.value if language is not None else None


def ui_language_of(db: Session, *, user_id: uuid.UUID) -> str | None:
    """The language the SPA shows this user: their stored `uiLanguage`, else
    the starting value above. One rule for the screen, the platform mail texts
    (`identity._language_of`) and the push title (`notifications.ui_language_of`)."""

    preference = db.get(UserPreference, (user_id, UI_SCOPE))
    stored = (preference.payload or {}).get("uiLanguage") if preference is not None else None
    if isinstance(stored, str):
        return stored
    return default_ui_language(db, user_id=user_id)


def payload_as_read(db: Session, *, user_id: uuid.UUID, scope: str, payload: dict | None) -> dict:
    """What a read of `scope` answers: the stored payload, and for the `ui`
    scope a `uiLanguage` that is the user's own when it is a string, else the
    starting language above (KAN-104 item 2) — the same rule as
    `ui_language_of`, so the screen, mail and push never disagree."""

    answer = dict(payload or {})
    if scope == UI_SCOPE and not isinstance(answer.get("uiLanguage"), str):
        language = default_ui_language(db, user_id=user_id)
        if language is not None:
            answer["uiLanguage"] = language
        else:
            answer.pop("uiLanguage", None)
    return answer


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
        # KAN-393: a grid that saves its layout twice in quick succession can
        # send two first saves of one scope; the second insert hit the
        # primary key at an unguarded commit, a 500. In a savepoint the loser
        # waits for the winner's commit, its insert fails, and it overwrites
        # the winner's row like any later save.
        try:
            with db.begin_nested():
                pref = UserPreference(user_id=user_id, scope=scope, payload=payload, schema_version=schema_version)
                db.add(pref)
        except IntegrityError:
            pref = db.get(UserPreference, (user_id, scope), populate_existing=True)
            assert pref is not None, "UserPreference row missing after the concurrent winner's INSERT"
            pref.payload = payload
            pref.schema_version = schema_version
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
