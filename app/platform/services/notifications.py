"""Staff notifications (KAN-216; Dealer Administration D-A-40, FR-A-47…50).

`notify(db, code, user_id=…, subject=…)` is the one way a notification is
raised, from any context (`app.platform.public.notify`). It runs inside the
caller's own transaction: the inbox row and its outbox row commit with the
fact they report, or not at all (rule 4) — a rolled-back action owes nobody
a notification. A code with no catalogue row raises `UnknownNotificationEvent`
(FR-A-47: an event not in the catalogue cannot be sent).

What happens is the user's decision (FR-A-48), per family:

- `off` — nothing is written.
- `immediate` — the inbox row is delivered at once and
  `platform.notification.delivered` is published; its consumer pushes the
  title and link to the user's live subscriptions when the family's push
  channel is on.
- `digest` — the row is held (`delivered_at` NULL) until the daily job
  `platform.notifications.digest` releases the day's rows into the inbox and
  publishes one `platform.notification_digest.released` per user, which
  pushes one pointer to the inbox.

The `security` family is not switchable: always immediate, in-app and push.
The inbox is the record (FR-A-50): in-app cannot be switched off while a
family delivers, and the daily `platform.notifications.purge` deletes rows
older than 90 days.

**Never an e-mail** (D-A-40): this module and its consumers do not import
the platform mailer, and an architecture test keeps it that way.
"""

import dataclasses
import datetime as dt
import enum
import uuid
from collections import defaultdict

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.pagination import SortPageParams, build_sorted_page, paginate_query_sorted
from app.platform.models.notification import (
    Notification,
    NotificationEvent,
    NotificationFamily,
    NotificationTiming,
    UserNotificationPreference,
)
from app.platform.services import notification_catalogue
from app.platform.services import user_preference as preference_service

RETENTION = dt.timedelta(days=90)
PRODUCER = "platform"
DELIVERED_EVENT = "platform.notification.delivered"
DIGEST_RELEASED_EVENT = "platform.notification_digest.released"

# Families the user may not change (FR-A-48: security is always immediate and
# cannot be switched off).
LOCKED_FAMILIES = frozenset({NotificationFamily.SECURITY})

_PARAM_TYPES = (str, int, bool)
_MAX_PARAM_LENGTH = 64


class UnknownNotificationEvent(LookupError):
    """A programming error: the code is not in the catalogue (FR-A-47)."""


@dataclasses.dataclass(frozen=True)
class NotificationSubject:
    """What the notification is about and where the SPA opens it. `type` and
    `id` name the subject in its owning context (a plain id, never a foreign
    key); `link` is an SPA path (`/account/security`), never a URL."""

    type: str
    link: str
    id: uuid.UUID | None = None


@dataclasses.dataclass(frozen=True)
class EffectivePreference:
    timing: NotificationTiming
    in_app: bool
    push: bool


def _code_value(code: "notification_catalogue.NotificationEventCode | str") -> str:
    return code.value if isinstance(code, enum.Enum) else str(code)


def _check_link(link: str) -> None:
    if not link.startswith("/") or link.startswith("//") or "\\" in link or len(link) > 512:
        raise ValueError(f"A notification links to an SPA path, not {link!r}.")


def _check_params(params: dict) -> dict:
    for key, value in params.items():
        if not isinstance(key, str) or not isinstance(value, _PARAM_TYPES):
            raise TypeError(f"Notification parameter {key!r} must be a string, number or flag.")
        if isinstance(value, str) and len(value) > _MAX_PARAM_LENGTH:
            raise ValueError(f"Notification parameter {key!r} is too long — closed codes only, never free text.")
    return dict(params)


def catalogue_event(db: Session, code: str) -> NotificationEvent:
    event = db.scalar(select(NotificationEvent).where(NotificationEvent.code == code))
    if event is None:
        raise UnknownNotificationEvent(f"{code!r} is not in the notification catalogue (notification_event).")
    return event


def _family_preference(
    db: Session, *, user_id: uuid.UUID, family: NotificationFamily
) -> UserNotificationPreference | None:
    return db.scalar(
        select(UserNotificationPreference).where(
            UserNotificationPreference.user_id == user_id, UserNotificationPreference.family == family
        )
    )


def effective_preference(db: Session, *, user_id: uuid.UUID, event: NotificationEvent) -> EffectivePreference:
    if not event.switchable or event.family in LOCKED_FAMILIES:
        return EffectivePreference(timing=event.default_timing, in_app=True, push=True)
    row = _family_preference(db, user_id=user_id, family=event.family)
    if row is None:
        return EffectivePreference(timing=event.default_timing, in_app=True, push=True)
    return EffectivePreference(timing=row.timing, in_app=row.in_app, push=row.push)


def family_push_enabled(db: Session, *, user_id: uuid.UUID, family: NotificationFamily) -> bool:
    if family in LOCKED_FAMILIES:
        return True
    row = _family_preference(db, user_id=user_id, family=family)
    return True if row is None else row.push


def notify(
    db: Session,
    event_code: "notification_catalogue.NotificationEventCode | str",
    *,
    user_id: uuid.UUID,
    subject: NotificationSubject,
    dealership_id: uuid.UUID | None = None,
    params: dict | None = None,
) -> Notification | None:
    """Raise one notification for one user. Returns the inbox row, or None
    when the user switched the family off. Does not commit."""

    code = _code_value(event_code)
    event = catalogue_event(db, code)
    _check_link(subject.link)
    checked = _check_params(params or {})
    preference = effective_preference(db, user_id=user_id, event=event)
    if preference.timing == NotificationTiming.OFF:
        return None
    now = utcnow()
    row = Notification(
        user_id=user_id,
        dealership_id=dealership_id,
        event_code=code,
        subject_type=subject.type,
        subject_id=subject.id,
        link=subject.link,
        params=checked,
        created_at=now,
        delivered_at=now if preference.timing == NotificationTiming.IMMEDIATE else None,
    )
    db.add(row)
    db.flush()
    if row.delivered_at is not None:
        outbox.publish(
            db,
            outbox.OutboxEvent(
                event_type=DELIVERED_EVENT,
                tenant_id=dealership_id,
                producer=PRODUCER,
                aggregate_type="notification",
                aggregate_id=row.id,
                # Ids and the code only — the consumer renders the title.
                payload={"notificationId": str(row.id), "userId": str(user_id), "eventCode": code},
                occurred_at=now,
            ),
        )
    return row


# --- the user's inbox (FR-A-50) ---------------------------------------------------------


def _inbox(user_id: uuid.UUID):
    return select(Notification).where(Notification.user_id == user_id, Notification.delivered_at.is_not(None))


def list_inbox(
    db: Session, *, user_id: uuid.UUID, unread_only: bool, params: SortPageParams
) -> tuple[list[Notification], str | None]:
    stmt = _inbox(user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = list(db.scalars(paginate_query_sorted(stmt, model=Notification, params=params)).all())
    return build_sorted_page(rows, params)


def unread_count(db: Session, *, user_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.delivered_at.is_not(None),
                Notification.read_at.is_(None),
            )
        )
        or 0
    )


def get_own_or_404(db: Session, *, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
    row = db.get(Notification, notification_id)
    # Someone else's, or one still held for the digest: not there (404).
    if row is None or row.user_id != user_id or row.delivered_at is None:
        raise NotFoundError("Notification not found.")
    return row


def set_read(db: Session, *, user_id: uuid.UUID, notification_id: uuid.UUID, read: bool) -> Notification:
    row = get_own_or_404(db, user_id=user_id, notification_id=notification_id)
    if read and row.read_at is None:
        row.read_at = utcnow()
    elif not read:
        row.read_at = None
    db.flush()
    return row


def mark_all_read(db: Session, *, user_id: uuid.UUID) -> int:
    result = db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.delivered_at.is_not(None),
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def catalogue_rows(db: Session, codes: set[str]) -> dict[str, NotificationEvent]:
    if not codes:
        return {}
    return {e.code: e for e in db.scalars(select(NotificationEvent).where(NotificationEvent.code.in_(codes))).all()}


# --- preferences (FR-A-48) --------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FamilyPreference:
    family: NotificationFamily
    timing: NotificationTiming
    in_app: bool
    push: bool
    switchable: bool
    # Whether any catalogue event belongs to the family yet (only security in v1).
    has_events: bool
    # 0 = no row yet, the platform default (the If-Match for the first save).
    version: int


# Every family defaults to immediate (D-A-40); nothing is set by the manager.
PLATFORM_DEFAULT_TIMING = NotificationTiming.IMMEDIATE


def list_preferences(db: Session, *, user_id: uuid.UUID) -> list[FamilyPreference]:
    rows = {
        row.family: row
        for row in db.scalars(
            select(UserNotificationPreference).where(UserNotificationPreference.user_id == user_id)
        ).all()
    }
    with_events = set(db.scalars(select(NotificationEvent.family).distinct()).all())
    result = []
    for family in NotificationFamily:
        locked = family in LOCKED_FAMILIES
        row = rows.get(family)
        if locked or row is None:
            result.append(
                FamilyPreference(
                    family=family,
                    timing=PLATFORM_DEFAULT_TIMING,
                    in_app=True,
                    push=True,
                    switchable=not locked,
                    has_events=family in with_events,
                    version=row.version if row is not None else 0,
                )
            )
        else:
            result.append(
                FamilyPreference(
                    family=family,
                    timing=row.timing,
                    in_app=row.in_app,
                    push=row.push,
                    switchable=True,
                    has_events=family in with_events,
                    version=row.version,
                )
            )
    return result


def set_preference(
    db: Session,
    *,
    user_id: uuid.UUID,
    family: NotificationFamily,
    timing: NotificationTiming,
    in_app: bool,
    push: bool,
    if_match: int,
) -> FamilyPreference:
    """The user's own choice for one family. If-Match on the row's version —
    0 for the first save, when the platform default is all there is."""

    if family in LOCKED_FAMILIES:
        raise UnprocessableEntityError(
            "Security notifications are always delivered immediately and cannot be changed.",
            details={"rule": "family_locked", "family": family.value},
        )
    if not in_app:
        raise UnprocessableEntityError(
            "The inbox keeps every notification; it cannot be switched off — choose «off» for the family instead.",
            details={"rule": "in_app_is_the_record"},
        )
    row = db.scalar(
        select(UserNotificationPreference)
        .where(UserNotificationPreference.user_id == user_id, UserNotificationPreference.family == family)
        .with_for_update()
    )
    if row is None:
        check_version(0, if_match, entity_name="UserNotificationPreference")
        row = UserNotificationPreference(user_id=user_id, family=family, timing=timing, in_app=in_app, push=push)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            # A first save from another tab committed meanwhile: its version
            # is 1, not the 0 this save matched.
            raise ConflictError(
                "UserNotificationPreference has been modified since If-Match version 0.",
                details={"currentVersion": 1, "ifMatchVersion": if_match},
            ) from exc
    else:
        check_version(row.version, if_match, entity_name="UserNotificationPreference")
        row.timing = timing
        row.in_app = in_app
        row.push = push
        row.version += 1
        db.flush()
    return next(p for p in list_preferences(db, user_id=user_id) if p.family == family)


# --- the daily jobs ---------------------------------------------------------------------


def release_digests(db: Session, *, now: dt.datetime | None = None) -> int:
    """Delivers every held notification into its inbox and publishes one
    `platform.notification_digest.released` per user. Returns how many users
    got a digest. Does not commit."""

    now = now or utcnow()
    held = db.execute(
        select(Notification.id, Notification.user_id, Notification.event_code, Notification.dealership_id)
        .where(Notification.delivered_at.is_(None))
        .order_by(Notification.user_id, Notification.created_at)
        .with_for_update()
    ).all()
    if not held:
        return 0
    events = catalogue_rows(db, {row.event_code for row in held})
    by_user: dict[uuid.UUID, list] = defaultdict(list)
    for row in held:
        by_user[row.user_id].append(row)
    db.execute(
        update(Notification)
        .where(Notification.id.in_([row.id for row in held]))
        .values(delivered_at=now)
        .execution_options(synchronize_session=False)
    )
    for user_id, rows in by_user.items():
        families = sorted({events[row.event_code].family.value for row in rows})
        outbox.publish(
            db,
            outbox.OutboxEvent(
                event_type=DIGEST_RELEASED_EVENT,
                tenant_id=rows[0].dealership_id,
                producer=PRODUCER,
                aggregate_type="user",
                aggregate_id=user_id,
                payload={"userId": str(user_id), "count": len(rows), "families": families},
                occurred_at=now,
            ),
        )
    return len(by_user)


def purge_expired(db: Session, *, now: dt.datetime | None = None) -> int:
    """FR-A-50: the inbox keeps 90 days. Returns how many rows went."""

    cutoff = (now or utcnow()) - RETENTION
    result = db.execute(delete(Notification).where(Notification.created_at < cutoff))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- rendering the push -----------------------------------------------------------------


def ui_language_of(db: Session, user_id: uuid.UUID) -> str | None:
    """The user's UI language preference — the language the SPA shows them,
    and so the push title's (the mail texts' rule, `identity._language_of`)."""

    preference = preference_service.get_preference(db, user_id=user_id, scope="ui")
    if preference is None:
        return None
    language = (preference.payload or {}).get("uiLanguage")
    return language if isinstance(language, str) else None
