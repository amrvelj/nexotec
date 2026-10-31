"""Web Push subscriptions bound to sessions (KAN-216; FR-A-49, FR-AU-17).

A browser registers its subscription from a signed-in session; the row is
that session's. **A revoked session loses its subscription** — logout, the
user ending a session in My account, a reset, a deactivation, platform
staff's revoke: `auth_sessions.revoke` and `revoke_all_for_user` delete the
rows of every session they end, in the same transaction. A session that ran
out (idle or past midnight) is not revoked and keeps its subscription until
the browser signs in again, which moves the row to the new session (one row
per endpoint, whoever registers it last owns it — a shared PC's next user
takes the browser over, the previous user's row goes with it).

The keys are encrypted at rest; the endpoint is a capability URL and never
leaves the server once stored.
"""

import hashlib
import logging
import uuid
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableEntityError
from app.platform.models.auth_session import AuthSession
from app.platform.models.notification import PushSubscription
from app.platform.services import web_push
from app.platform.services.web_push import PushMessage, PushTarget

logger = logging.getLogger(__name__)

MAX_ENDPOINT_LENGTH = 2048


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _check_endpoint(endpoint: str) -> None:
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc or len(endpoint) > MAX_ENDPOINT_LENGTH:
        # A push service is always reached over HTTPS (RFC 8030); anything
        # else is not a browser's subscription.
        raise UnprocessableEntityError(
            "The push subscription's endpoint must be an HTTPS URL.", details={"rule": "endpoint"}
        )


def register(
    db: Session, *, user_id: uuid.UUID, session_id: uuid.UUID, endpoint: str, p256dh: str, auth: str
) -> PushSubscription:
    """Upsert by endpoint: the same browser registering again (a new sign-in,
    a renewed subscription) moves the one row to this session and user."""

    _check_endpoint(endpoint)
    digest = _endpoint_hash(endpoint)
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == digest).with_for_update())
    if row is None:
        candidate = PushSubscription(
            user_id=user_id, session_id=session_id, endpoint=endpoint, p256dh=p256dh, auth=auth, endpoint_hash=digest
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            return candidate
        except IntegrityError:
            # A concurrent registration of the same browser won: take its row.
            row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == digest).with_for_update())
            if row is None:  # pragma: no cover - the winner's row is committed
                raise
    row.user_id = user_id
    row.session_id = session_id
    row.endpoint = endpoint
    row.p256dh = p256dh
    row.auth = auth
    db.flush()
    return row


def get_own_or_404(db: Session, *, user_id: uuid.UUID, subscription_id: uuid.UUID) -> PushSubscription:
    row = db.get(PushSubscription, subscription_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("Push subscription not found.")
    return row


def unregister(db: Session, *, user_id: uuid.UUID, subscription_id: uuid.UUID) -> None:
    db.delete(get_own_or_404(db, user_id=user_id, subscription_id=subscription_id))
    db.flush()


def delete_for_session(db: Session, session_id: uuid.UUID) -> int:
    result = db.execute(delete(PushSubscription).where(PushSubscription.session_id == session_id))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def delete_for_revoked_sessions(db: Session, *, user_id: uuid.UUID | None = None) -> int:
    """Every subscription whose session is revoked — the user's, or (the
    daily purge's belt) everyone's."""

    revoked = select(AuthSession.id).where(AuthSession.revoked_at.is_not(None))
    if user_id is not None:
        revoked = revoked.where(AuthSession.user_id == user_id)
    result = db.execute(
        delete(PushSubscription)
        .where(PushSubscription.session_id.in_(revoked))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def live_targets(db: Session, *, user_id: uuid.UUID) -> list[tuple[PushSubscription, PushTarget]]:
    """The user's subscriptions whose session is not revoked."""

    rows = db.scalars(
        select(PushSubscription)
        .join(AuthSession, AuthSession.id == PushSubscription.session_id)
        .where(PushSubscription.user_id == user_id, AuthSession.revoked_at.is_(None))
        .order_by(PushSubscription.created_at)
    ).all()
    return [(row, PushTarget(endpoint=row.endpoint, p256dh=row.p256dh, auth=row.auth)) for row in rows]


def push_to_user(db: Session, *, user_id: uuid.UUID, message: PushMessage) -> dict[str, int]:
    """Sends one pointer to every live subscription of the user through the
    registered sender (`web_push.get_web_push_sender`). A subscription the
    push service reports gone is deleted; any other failure is logged and
    not retried — the inbox holds the notification either way (FR-A-50), and
    a retry would repeat the push on the devices that already showed it.
    Returns the outcome counts (by `PushOutcome` value)."""

    sender = web_push.get_web_push_sender()
    counts: dict[str, int] = {}
    if sender.public_key() is None:
        counts[web_push.PushOutcome.NOT_CONFIGURED.value] = 1
        return counts
    for row, target in live_targets(db, user_id=user_id):
        outcome = sender.send(target, message)
        counts[outcome.value] = counts.get(outcome.value, 0) + 1
        if outcome == web_push.PushOutcome.GONE:
            db.delete(row)
        elif outcome == web_push.PushOutcome.FAILED:
            logger.warning("web_push_failed", extra={"pushSubscriptionId": str(row.id), "userId": str(user_id)})
    db.flush()
    return counts


def for_session(db: Session, *, user_id: uuid.UUID, session_id: uuid.UUID) -> list[PushSubscription]:
    return list(
        db.scalars(
            select(PushSubscription).where(
                PushSubscription.user_id == user_id, PushSubscription.session_id == session_id
            )
        ).all()
    )
