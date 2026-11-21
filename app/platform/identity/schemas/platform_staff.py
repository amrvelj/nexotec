"""KAN-221 PR-6: the Platform users panel's shapes."""

import uuid

from pydantic import EmailStr, Field, field_validator

from app.core.schemas import CamelModel
from app.platform.identity.models.user import UserStatus


class PlatformStaffEntry(CamelModel):
    """One holder of `platform_admin`. `inPlatformTenant` false is a legacy
    holder still homed in a dealer's Betrieb (KAN-199; ADR-188)."""

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    status: UserStatus
    version: int
    home_dealership_id: uuid.UUID
    home_dealership_name: str
    in_platform_tenant: bool
    mfa_enrolled: bool
    running_support_session_id: uuid.UUID | None


class PlatformStaffPage(CamelModel):
    items: list[PlatformStaffEntry]
    next_cursor: str | None


class PlatformStaffCreate(CamelModel):
    """A new Nexotec staff member: an account in the platform tenant holding
    `platform_admin`, invited by e-mail to set their own password."""

    first_name: str = Field(max_length=100, min_length=1)
    last_name: str = Field(max_length=100, min_length=1)
    email: EmailStr


class PlatformStaffRevoke(CamelModel):
    """Why the staff access ends — mandatory, kept on the audit row."""

    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _reason_is_said(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Revoking staff access needs a stated reason.")
        return stripped


class PlatformStaffRevoked(CamelModel):
    entry: PlatformStaffEntry
    # True when the account itself was deactivated (a staff account in the
    # platform tenant holds nothing but the role); false for a legacy holder,
    # who keeps their Betrieb account without the role.
    deactivated: bool
