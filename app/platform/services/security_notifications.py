"""The `security` notification family's seam for platform recovery
(D-A-40, FR-AU-26; KAN-202, filled by KAN-216).

Every platform recovery action notifies the affected user — in-app and push,
never an e-mail from staff (D-A-40; a re-issued invitation is a flow and goes
by e-mail on its own). `notify` is called inside the action's own
transaction, with the session, so the notification catalogue writes its
inbox row (and its outbox row for push) in that same commit.

The sink is the catalogue's (`security_alerts.notify_recovery_action`,
`security.platform_recovery_action`); tests may register a recording sink
and `reset_sink` puts the catalogue's back. The notice carries the kind
and the ids — never the stated reason or any other free text.
"""

import dataclasses
import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from app.platform.services import security_alerts


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


def _catalogue_sink(db: Session, notice: SecurityNotice) -> None:
    security_alerts.notify_recovery_action(
        db, user_id=notice.user_id, dealership_id=notice.dealership_id, kind=notice.kind
    )


_sink: SecurityNotificationSink = _catalogue_sink


def register_sink(sink: SecurityNotificationSink) -> None:
    """Replaces the catalogue's sink (tests)."""

    global _sink
    _sink = sink


def reset_sink() -> None:
    """Back to the catalogue's sink (tests)."""

    global _sink
    _sink = _catalogue_sink


def notify(db: Session, notice: SecurityNotice) -> None:
    _sink(db, notice)
