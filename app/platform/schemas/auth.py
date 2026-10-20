import datetime as dt
import uuid

from pydantic import Field

from app.core.permissions import ModuleCode
from app.core.schemas import CamelModel
from app.platform.schemas.user import UserRead


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

    # KAN-200: the session's user keeps the shape and the name it had — the
    # administration screen's fields are UserAdminRead's, never minted here.
    user: UserRead
    active_dealership: DealershipMembershipSummary
    memberships: list[DealershipMembershipSummary]
    # KAN-198 (D-A-04): the active dealership's licensed modules — what the
    # shell shows and the route guard honours (presentation only; the 404 the
    # API returns for an unlicensed module is the boundary).
    entitlements: list[ModuleCode]
    # KAN-196 (D-A-36, FR-AU-11): this session was minted without a second
    # factor while one is required — the SPA sends the user to enrolment
    # before any other screen.
    mfa_enrolment_required: bool = False


class SwitchDealershipRequest(CamelModel):
    dealership_id: uuid.UUID


class LogoutResponse(CamelModel):
    ok: bool = True


# --- KAN-196: the identity front door -----------------------------------------------------------


class LoginRequest(CamelModel):
    email: str = Field(max_length=320, min_length=3)
    password: str = Field(max_length=1024, min_length=1)


class LoginStepResponse(CamelModel):
    """What `POST /v1/auth/login` answers. Either the session is minted
    (`session` carries the login response, the cookie is set) or a second
    factor is due: `mfaRequired` with the `challengeId` the MFA step
    presents (5 minutes, single use). Never anything about why a login
    failed — a failure is the uniform 401."""

    mfa_required: bool = False
    challenge_id: str | None = None
    # The factors the user may answer with, so the screen knows what to offer.
    factors: list[str] = Field(default_factory=list)
    session: LoginResponse | None = None


class MfaVerifyRequest(CamelModel):
    challenge_id: str = Field(max_length=128, min_length=1)
    # A TOTP code or a recovery code.
    code: str = Field(max_length=32, min_length=1)


class PasskeyChallengeRequest(CamelModel):
    challenge_id: str = Field(max_length=128, min_length=1)


class PasskeyOptionsResponse(CamelModel):
    challenge_id: str
    # The WebAuthn options as the browser API expects them (serialised JSON).
    options: str


class PasskeyAssertionRequest(CamelModel):
    challenge_id: str = Field(max_length=128, min_length=1)
    credential: dict


class PasswordResetRequest(CamelModel):
    email: str = Field(max_length=320, min_length=3)


class PasswordResetConfirm(CamelModel):
    token: str = Field(max_length=128, min_length=1)
    password: str = Field(max_length=1024, min_length=1)


class InvitationAccept(CamelModel):
    token: str = Field(max_length=128, min_length=1)
    password: str = Field(max_length=1024, min_length=1)


class SessionRead(CamelModel):
    id: uuid.UUID
    device_summary: str
    ip_address: str
    created_at: dt.datetime
    last_seen_at: dt.datetime
    absolute_expires_at: dt.datetime
    current: bool


class SessionList(CamelModel):
    items: list[SessionRead]


class PasskeyRead(CamelModel):
    id: uuid.UUID
    label: str
    created_at: dt.datetime
    last_used_at: dt.datetime | None


class MfaStatusRead(CamelModel):
    totp_enrolled: bool
    passkeys: list[PasskeyRead]
    recovery_codes_remaining: int
    # D-A-09 / D-A-36: the Betrieb enforces a factor, or the user is platform staff.
    enforced: bool
    enrolment_required: bool


class TotpEnrolmentRead(CamelModel):
    secret: str
    provisioning_uri: str


class TotpConfirmRequest(CamelModel):
    code: str = Field(max_length=16, min_length=1)


class RecoveryCodesRead(CamelModel):
    codes: list[str]


class PasskeyRegisterRequest(CamelModel):
    challenge_id: str = Field(max_length=128, min_length=1)
    credential: dict
    label: str = Field(default="", max_length=100)


class AuthConfigRead(CamelModel):
    auth_method: str
    mfa_enforced: bool
    version: int


class AuthConfigUpdate(CamelModel):
    mfa_enforced: bool


class AuthEventRead(CamelModel):
    id: uuid.UUID
    kind: str
    user_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    ip_address: str
    occurred_at: dt.datetime
    details: dict | None


class AuthEventList(CamelModel):
    items: list[AuthEventRead]


class LoginStatusRead(CamelModel):
    """FR-AU-10: what a manager may see — never a password or its hash."""

    has_password: bool
    locked_until: dt.datetime | None
    failed_login_attempts: int
