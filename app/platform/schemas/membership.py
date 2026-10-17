"""Membership, manager-flag and standard-text-delegation payloads (KAN-199)."""

import datetime as dt
import uuid

from app.core.schemas import CamelModel
from app.platform.models.standard_text_delegation import StandardTextCategory


class MembershipCreate(CamelModel):
    """Grant the user access to a sister dealership. The target is the body,
    the user's home dealership the path — a manager of both sides, or
    platform staff, may grant (D-A-01)."""

    dealership_id: uuid.UUID
    is_dealer_manager: bool = False


class MembershipRead(CamelModel):
    dealership_id: uuid.UUID
    # Denormalised display label (ADR-014 three-column pattern is for
    # cross-context ids; dealership is platform-owned, so this is a join).
    dealership_label: str
    is_dealer_manager: bool
    granted_at: dt.datetime
    granted_by: uuid.UUID | None
    valid_to: dt.datetime | None
    active: bool


class MembershipList(CamelModel):
    """Every membership the user ever held, ended ones included (they keep
    their row with `valid_to`); `user_version` is the If-Match value for the
    writes."""

    items: list[MembershipRead]
    user_version: int


class ManagerFlagUpdate(CamelModel):
    is_dealer_manager: bool


class TextDelegationsUpdate(CamelModel):
    """Full replacement of the user's delegated categories — the categories
    not listed are withdrawn."""

    categories: list[StandardTextCategory]


class TextDelegationsRead(CamelModel):
    categories: list[StandardTextCategory]
    user_version: int
