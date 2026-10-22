"""Platform staff's account recovery (FR-AU-25…27, KAN-202)."""

import datetime as dt
import uuid

from pydantic import ConfigDict, Field, field_validator

from app.core.schemas import CamelModel
from app.platform.models.user import UserStatus
from app.platform.services.recovery import REASON_MAX_LENGTH


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


class RecoveryResult(CamelModel):
    """What was recorded: the auth event the Betrieb's stream lists."""

    action: str
    user_id: uuid.UUID
    dealership_id: uuid.UUID
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


class RecoveryUserList(CamelModel):
    items: list[RecoveryUserRead]
