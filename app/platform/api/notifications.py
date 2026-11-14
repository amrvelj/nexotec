"""Staff notifications for the caller (KAN-216; Dealer Administration D-A-40,
FR-A-47…50): the inbox, the per-family preferences and this browser's Web
Push subscription. Self-scoped like the rest of `/v1/me`: no user id in any
path — the caller is the token's `sub` — and someone else's notification or
subscription is a 404, never a 403. Every POST honours `Idempotency-Key`.

A session that still owes a second factor reaches none of these (the session
guard confines it to the enrolment paths): its inbox waits until it enrolled.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import require_if_match
from app.core.config import get_settings
from app.core.errors import UnprocessableEntityError
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.sorting import SortField
from app.db import get_db
from app.platform.models.notification import Notification, NotificationEvent, NotificationFamily
from app.platform.reference.services import web_push
from app.platform.schemas.notification import (
    MarkAllReadResult,
    NotificationPage,
    NotificationPreferenceList,
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
    NotificationReadUpdate,
    PushConfigRead,
    PushSubscriptionCreate,
    PushSubscriptionRead,
    UnreadCountRead,
)
from app.platform.services import notifications, push_subscriptions

settings = get_settings()
router = APIRouter(tags=["notifications"], route_class=IdempotentRoute)

# Newest first; the id breaks ties (the cursor's tiebreaker).
_INBOX_ORDER = [SortField(api_name="deliveredAt", column=Notification.delivered_at, direction="desc", nullable=False)]


def _read(row: Notification, event: NotificationEvent) -> NotificationRead:
    return NotificationRead(
        id=row.id,
        event_code=row.event_code,
        family=event.family,
        title_key=event.title_key,
        body_key=event.body_key,
        params=row.params or {},
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        link=row.link,
        created_at=row.created_at,
        delivered_at=row.delivered_at,  # type: ignore[arg-type]  # the inbox lists delivered rows only
        read_at=row.read_at,
    )


def _reads(db: Session, rows: list[Notification]) -> list[NotificationRead]:
    events = notifications.catalogue_rows(db, {row.event_code for row in rows})
    return [_read(row, events[row.event_code]) for row in rows]


@router.get("/me/notifications", response_model=NotificationPage)
def list_my_notifications(
    unread: bool = Query(default=False, description="Only the unread ones."),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The inbox (FR-A-50), newest first, cursor-paginated. A notification
    held for the daily digest is not listed until the digest releases it."""

    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=_INBOX_ORDER
    )
    rows, next_cursor = notifications.list_inbox(db, user_id=principal.user_id, unread_only=unread, params=params)
    return NotificationPage(items=_reads(db, rows), next_cursor=next_cursor)


@router.get("/me/notifications/unread-count", response_model=UnreadCountRead)
def my_unread_count(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    """The bell's badge."""

    return UnreadCountRead(count=notifications.unread_count(db, user_id=principal.user_id))


@router.patch("/me/notifications/{notification_id}", response_model=NotificationRead)
def mark_my_notification(
    notification_id: uuid.UUID,
    body: NotificationReadUpdate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Read or unread again. Unversioned (the reader's own stamp, idempotent):
    no If-Match."""

    row = notifications.set_read(db, user_id=principal.user_id, notification_id=notification_id, read=body.read)
    db.commit()
    return _reads(db, [row])[0]


@router.post("/me/notifications/read-all", response_model=MarkAllReadResult)
def mark_all_my_notifications_read(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    marked = notifications.mark_all_read(db, user_id=principal.user_id)
    db.commit()
    return MarkAllReadResult(marked=marked)


def _preference_read(pref: notifications.FamilyPreference) -> NotificationPreferenceRead:
    return NotificationPreferenceRead(
        family=pref.family,
        timing=pref.timing,
        in_app=pref.in_app,
        push=pref.push,
        switchable=pref.switchable,
        has_events=pref.has_events,
        version=pref.version,
    )


@router.get("/me/notification-preferences", response_model=NotificationPreferenceList)
def list_my_notification_preferences(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    """Every family with the caller's choice or the platform default (FR-A-48)."""

    return NotificationPreferenceList(
        items=[_preference_read(p) for p in notifications.list_preferences(db, user_id=principal.user_id)]
    )


@router.put("/me/notification-preferences/{family}", response_model=NotificationPreferenceRead)
def put_my_notification_preference(
    family: NotificationFamily,
    body: NotificationPreferenceUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The caller's own choice for one family — the user decides everything,
    never the manager (D-A-40). If-Match on the version the list returned
    (0 for the first save). `security` is a 422 (`family_locked`)."""

    pref = notifications.set_preference(
        db,
        user_id=principal.user_id,
        family=family,
        timing=body.timing,
        in_app=body.in_app,
        push=body.push,
        if_match=if_match,
    )
    db.commit()
    return _preference_read(pref)


@router.get("/me/push/config", response_model=PushConfigRead)
def my_push_config(principal: Principal = Depends(get_current_principal)):
    """Whether this deployment sends Web Push, and the key to subscribe with."""

    key = web_push.vapid_public_key()
    return PushConfigRead(available=key is not None, vapid_public_key=key)


@router.post("/me/push-subscriptions", response_model=PushSubscriptionRead, status_code=201)
def register_my_push_subscription(
    body: PushSubscriptionCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """This browser's subscription, bound to the calling session (FR-A-49):
    when the session is revoked, the subscription goes. Registering the same
    browser again moves it to this session."""

    if principal.session_id is None:
        raise UnprocessableEntityError(
            "A push subscription belongs to a signed-in session.", details={"rule": "session_required"}
        )
    row = push_subscriptions.register(
        db,
        user_id=principal.user_id,
        session_id=principal.session_id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
    )
    db.commit()
    return PushSubscriptionRead(id=row.id, session_id=row.session_id, created_at=row.created_at)


@router.delete("/me/push-subscriptions/{subscription_id}", status_code=204)
def delete_my_push_subscription(
    subscription_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Push off on this device. Unversioned: no If-Match."""

    push_subscriptions.unregister(db, user_id=principal.user_id, subscription_id=subscription_id)
    db.commit()
