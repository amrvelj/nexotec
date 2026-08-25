import uuid

from pydantic import EmailStr, Field

from app.core.schemas import CamelModel
from app.platform.schemas.user import UserRead


class CredentialSetRequest(CamelModel):
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class DealershipMembershipSummary(CamelModel):
    """Just enough to render the sidebar account cluster's dealership
    switcher (WP-3 PR-3) — not a full DealershipRead, since the switcher
    only needs a name to show, not the whole entity.
    """

    id: uuid.UUID
    legal_name: str


class LoginResponse(CamelModel):
    """The JWT itself never appears in this body — it's delivered only as
    the httpOnly session cookie (see app.core.auth.SESSION_COOKIE_NAME).
    This is what the frontend uses to render "logged in as X" state, since
    it can't read the cookie's claims directly. active_dealership/
    memberships (WP-3 PR-3) are what the sidebar switcher renders — the
    frontend never decodes the cookie to get at Principal.memberships
    itself.
    """

    user: UserRead
    active_dealership: DealershipMembershipSummary
    memberships: list[DealershipMembershipSummary]


class SwitchDealershipRequest(CamelModel):
    dealership_id: uuid.UUID


class LogoutResponse(CamelModel):
    ok: bool = True
