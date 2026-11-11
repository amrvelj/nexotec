"""Per-Betrieb session policy (KAN-229; D-A-37, FR-AU-28).

A Betrieb may make its sessions stricter than the platform's, never looser
(Anto, 2026-11-11, option 1):

- idle 15–120 minutes, absolute maximum 1–12 hours;
- the session ends at whichever comes first: login + the Betrieb's hours, or
  the next midnight Europe/Zurich — midnight stays the ceiling;
- platform staff always get the platform values; a Betrieb that sets nothing
  gets the platform values;
- a switch to another Betrieb applies the target's policy but never makes
  the session last longer than it already would (architect's call, recorded
  on the ticket): the absolute end and the current idle end only move
  earlier. Later activity extends the idle end by the target's window.

The numbers live here and in the CHECK constraints on
`dealership_auth_config` (`ck_dealership_auth_config_session_*`); the
migration `a7d2c5e9f041` carries the same ones.
"""

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import UnprocessableEntityError
from app.platform.models.auth_session import AuthSession
from app.platform.services import identity

IDLE_MINUTES_MIN = 15
IDLE_MINUTES_MAX = 120
ABSOLUTE_HOURS_MIN = 1
ABSOLUTE_HOURS_MAX = 12


@dataclass(frozen=True)
class SessionPolicy:
    idle_minutes: int
    # None = no limit of the Betrieb's own; the next midnight still ends it.
    absolute_hours: int | None


def platform_policy() -> SessionPolicy:
    return SessionPolicy(idle_minutes=get_settings().session_idle_minutes, absolute_hours=None)


def validate(*, idle_minutes: int | None, absolute_hours: int | None) -> None:
    """422 with a stable `details.reason` outside the ruled bounds (None = the
    platform value, always allowed)."""

    if idle_minutes is not None and not IDLE_MINUTES_MIN <= idle_minutes <= IDLE_MINUTES_MAX:
        raise UnprocessableEntityError(
            f"sessionIdleMinutes must be between {IDLE_MINUTES_MIN} and {IDLE_MINUTES_MAX}.",
            details={
                "reason": "session_idle_minutes_out_of_bounds",
                "field": "sessionIdleMinutes",
                "min": IDLE_MINUTES_MIN,
                "max": IDLE_MINUTES_MAX,
            },
        )
    if absolute_hours is not None and not ABSOLUTE_HOURS_MIN <= absolute_hours <= ABSOLUTE_HOURS_MAX:
        raise UnprocessableEntityError(
            f"sessionAbsoluteHours must be between {ABSOLUTE_HOURS_MIN} and {ABSOLUTE_HOURS_MAX}.",
            details={
                "reason": "session_absolute_hours_out_of_bounds",
                "field": "sessionAbsoluteHours",
                "min": ABSOLUTE_HOURS_MIN,
                "max": ABSOLUTE_HOURS_MAX,
            },
        )


def effective_policy(db: Session, *, dealership_id: uuid.UUID, is_platform_staff: bool) -> SessionPolicy:
    """The policy a session in this Betrieb runs under. Staff: the platform's.
    The idle window is also capped by the platform's own setting, so a
    deployment configured stricter than 120 minutes is never loosened."""

    platform = platform_policy()
    if is_platform_staff:
        return platform
    config = identity.read_auth_config(db, dealership_id)
    idle = platform.idle_minutes
    if config.session_idle_minutes is not None:
        idle = min(idle, config.session_idle_minutes)
    return SessionPolicy(idle_minutes=idle, absolute_hours=config.session_absolute_hours)


def absolute_end(*, started_at: dt.datetime, policy: SessionPolicy, next_midnight: dt.datetime) -> dt.datetime:
    """Whichever comes first: the start + the Betrieb's hours, or the next
    midnight after the start."""

    if policy.absolute_hours is None:
        return next_midnight
    return min(next_midnight, started_at + dt.timedelta(hours=policy.absolute_hours))


def rebound_for_switch(
    row: AuthSession, *, policy: SessionPolicy, now: dt.datetime, next_midnight: dt.datetime
) -> None:
    """A switch applies the target Betrieb's policy without ever lengthening
    the session: the absolute end is the earlier of the current one and the
    target's (counted from the login, `created_at`); the idle end the earlier
    of the current one and now + the target's window."""

    target_absolute = absolute_end(started_at=row.created_at, policy=policy, next_midnight=next_midnight)
    row.absolute_expires_at = min(row.absolute_expires_at, target_absolute)
    row.idle_minutes = policy.idle_minutes
    row.idle_expires_at = min(
        row.idle_expires_at, now + dt.timedelta(minutes=policy.idle_minutes), row.absolute_expires_at
    )
