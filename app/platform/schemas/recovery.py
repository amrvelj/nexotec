"""Platform staff's account recovery (FR-AU-25…27, KAN-202)."""

import datetime as dt
import uuid

from pydantic import ConfigDict, EmailStr, Field, field_validator

from app.core.schemas import CamelModel
from app.platform.models.user import UserStatus
from app.platform.services.recovery import REASON_MAX_LENGTH, RecordedAction


def _stated(value: str) -> str:
    if not value.strip():
        raise ValueError("A recovery action needs a stated reason.")
    return value


class RecoveryRequest(CamelModel):
    """FR-AU-25: every recovery action carries a stated reason — the user's
    Betrieb reads it in its auth stream (Anto, 2026-10-22)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)

    _reason_stated = field_validator("reason")(_stated)


class GrantManagerRequest(RecoveryRequest):
    user_id: uuid.UUID


class ChangeEmailRequest(RecoveryRequest):
    """The new sign-in address (Anto, 2026-10-22: the change is recovery)."""

    email: EmailStr


class RecoveryResult(CamelModel):
    """What was recorded: the auth event the Betrieb's stream lists."""

    action: RecordedAction
    user_id: uuid.UUID
    dealership_id: uuid.UUID
    # The user row's version after the action — the two actions that write
    # it (grant-manager, change-email) take If-Match on it.
    user_version: int
    auth_event_id: uuid.UUID
    occurred_at: dt.datetime


class RecoveryUserRead(CamelModel):
    """An account as the recovery page shows it — enough to choose an
    action, never a hash, a secret or a token."""

    id: uuid.UUID
    dealership_id: uuid.UUID
    dealership_name: str
    email: str
    first_name: str
    last_name: str
    status: UserStatus
    is_dealer_manager: bool
    is_platform_staff: bool
    has_password: bool
    locked_until: dt.datetime | None
    failed_login_attempts: int
    mfa_enrolled: bool
    open_sessions: int
    version: int


class RecoveryUserList(CamelModel):
    items: list[RecoveryUserRead]
    next_cursor: str | None
