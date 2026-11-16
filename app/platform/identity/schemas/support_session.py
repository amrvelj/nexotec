"""KAN-221 PR-2: a support session's request and read shapes."""

import datetime as dt
import uuid

from pydantic import Field, field_validator

from app.core.schemas import CamelModel
from app.platform.identity.models.support_session import SupportSessionEndedBy


class SupportSessionStart(CamelModel):
    """FR-A-15: the user the staff member steps in for, and why — the reason
    is mandatory and is shown to the user on the banner."""

    target_user_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _reason_is_said(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A support session needs a stated reason.")
        return stripped


class SupportSessionRead(CamelModel):
    id: uuid.UUID
    staff_user_id: uuid.UUID
    target_user_id: uuid.UUID
    dealership_id: uuid.UUID
    reason: str
    started_at: dt.datetime
    expires_at: dt.datetime
    # How it ended — `timeout` from `expiresAt` on when nobody ended it.
    ended_at: dt.datetime | None
    ended_by: SupportSessionEndedBy | None
    active: bool
