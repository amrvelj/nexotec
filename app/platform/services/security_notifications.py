"""The `security` notification family's seam (D-A-40, FR-AU-26; KAN-202).

Every platform recovery action notifies the affected user — in-app and push,
never an e-mail from staff (D-A-40; a re-issued invitation is a flow and goes
by e-mail on its own). The notification catalogue is KAN-216 and is not
built, so this module is the interface it fills: `notify` is called inside
the action's own transaction, with the session, so the catalogue can write
its in-app row (and its outbox row for push) in that same commit.

Until KAN-216 registers a sink, `notify` hands the notice to the unbuilt
sink, which logs that a notice is owed once the transaction commits — the user id and the kind, never
the stated reason or any other free text. Nothing is sent, and nothing
reaches the user: the PRD's status line says so.
"""

import dataclasses
import logging
import uuid
from typing import Protocol

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SecurityNotice:
    """What the user is told: what happened to their account, that platform
    staff did it, and when. `kind` is the auth event kind's value."""

    user_id: uuid.UUID
    dealership_id: uuid.UUID
    kind: str
    actor_id: uuid.UUID
    auth_event_id: uuid.UUID


class SecurityNotificationSink(Protocol):
    def __call__(self, db: Session, notice: SecurityNotice) -> None: ...


def _unbuilt_sink(db: Session, notice: SecurityNotice) -> None:
    # Logged once the action's transaction commits — a rolled-back action
    # owes nobody a notice.
    def _owed(_session: Session) -> None:
        logger.info(
            "security_notification_owed",
            extra={"user_id": str(notice.user_id), "kind": notice.kind, "auth_event_id": str(notice.auth_event_id)},
        )

    event.listen(db, "after_commit", _owed, once=True)


_sink: SecurityNotificationSink = _unbuilt_sink


def register_sink(sink: SecurityNotificationSink) -> None:
    """KAN-216 registers the catalogue's sink here, once, at start-up."""

    global _sink
    _sink = sink


def reset_sink() -> None:
    """Back to the unbuilt sink (tests)."""

    global _sink
    _sink = _unbuilt_sink


def notify(db: Session, notice: SecurityNotice) -> None:
    _sink(db, notice)
