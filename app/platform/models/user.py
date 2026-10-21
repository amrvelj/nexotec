"""User: a person acting on behalf of a Dealership. User.tenant_id (inherited
from TenantScopedMixin) is the spec's `dealership_id` field — reusing the
shared mixin's column name keeps every tenant-scoped entity uniform for
app.core.tenancy.get_or_404 and friends; the API layer exposes it as
`dealershipId` (see app/platform/schemas/user.py) to match the spec's domain
language.

tenant_id -> dealership.id has no DB-level FK (PR-2, ADR-015): both User and
Dealership are platform-owned, so this was never actually a cross-context FK,
but it's named explicitly in PR-2's scope ("every tenant_id FK to
dealer.id") so it's dropped here too rather than re-litigated.

access_roles / is_dealer_manager (WP-2 PR-2, Roles & Permissions RP-1/RP-3):
replaces the old scalar access_role. A JSON array, not a child table like
CustomerPhone/CustomerEmail — Roles & Permissions is explicit that there is
"no per-dealership role editor in v1" and a dealership's whole user list is
small (the "four-person-garage" case is the design target), so there is no
need to query "every user holding role X" with an indexed join; the one
place that filters by role (services/user.py::list_users) does it in
Python over an already-small, already-paginated result set.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.i18n import SwissLanguage
from app.core.types import GUID, UTCDateTime
from app.db import Base


class UserRole(str, enum.Enum):
    """Descriptive job title — distinct from AccessRole (authorization).
    Swiss addendum Round 3 explicitly splits these two concerns.
    """

    SALES = "sales"
    SERVICE_ADVISOR = "service_advisor"
    FINANCE_MANAGER = "finance_manager"
    GM = "gm"
    ADMIN = "admin"
    TECHNICIAN = "technician"
    PARTS = "parts"
    OTHER = "other"


class EmploymentStatus(str, enum.Enum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    TERMINATED = "terminated"


class UserStatus(str, enum.Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class User(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "user"
    # KAN-196: the address is the sign-in and recovery channel, resolved
    # case-insensitively — stored lower-cased, so the unique index above
    # cannot admit a second spelling of one mailbox.
    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_user_email_lower"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Dealership). No DB-level FK."
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    role: Mapped[UserRole] = mapped_column(StoredEnum(UserRole, length=32), nullable=False)
    # Role *values* (AccessRole.value strings), not AccessRole members —
    # SQLAlchemy's Enum type only maps a column to ONE enum member per row,
    # so a genuinely multi-valued set has to be plain JSON here. See
    # services/user.py for the AccessRole <-> str conversion at the
    # schema/service boundary; nothing outside that layer should read this
    # column directly.
    access_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    # Administration of THIS (home) dealership only (Roles & Permissions
    # RP-1, ADR-026) — orthogonal to access_roles. A sister dealership the
    # user may switch to has its own flag on DealershipMembership (KAN-98,
    # D-A-01). A dealership must always have at least one active manager;
    # see services/user.py::_assert_not_last_manager.
    is_dealer_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        StoredEnum(EmploymentStatus, length=32),
        nullable=False,
        default=EmploymentStatus.ACTIVE,
    )

    # The identity-provider subject (D-A-08 / D-A-11, KAN-196): the join a
    # federated provider (`EntraOidcProvider`, [P3]) resolves a user by. A
    # local-password user (every user today) is resolved by e-mail and
    # carries `local:<user id>` here, so the column stays unique and
    # non-null for the federation seam. No credential is stored on this row,
    # ever: the password hash lives in `credential` and nowhere else
    # (tests/architecture/test_no_password_credential_storage.py).
    auth_identity_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    status: Mapped[UserStatus] = mapped_column(
        StoredEnum(UserStatus, length=32), nullable=False, default=UserStatus.INVITED
    )

    # --- KAN-200 (Dealer Administration, Users → Fields) -------------------
    # `phone` above is the mobile number (the PRD's "mobile"); this is the
    # desk extension.
    direct_dial: Mapped[str | None] = mapped_column(String(20), nullable=True)
    employee_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The person this user reports to — another user of the same dealership
    # (checked in the service). Platform-owned, so a plain reference without
    # a FK is a choice, not rule 2: a line manager who is deactivated keeps
    # being named, and a FK would make reactivation order matter.
    line_manager_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    # Commercial: the marketplace lead-attribution code and whether the user
    # takes part in commission (the figure itself is Finance's, not built).
    sales_advisor_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    commission_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Lifecycle (FR-A-12): calendar days in the platform zone. A user whose
    # `valid_from` is still to come cannot sign in; one whose `valid_to` has
    # passed is deactivated by the nightly job, open records released.
    valid_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    invited_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deactivated_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deactivation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The signature image's key in the object store (app.core.object_store),
    # never the image: `dealerships/<dealership>/users/<user>/signature`.
    signature_image_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- KAN-201 (My account; Users → Personal settings) --------------------
    # The language of the documents this user authors, where a document has
    # no customer language of its own; NULL = the Betrieb's
    # `default_correspondence_language`. Not the UI language (a preference,
    # scope `ui`), and never the customer's correspondence language.
    correspondence_language: Mapped[SwissLanguage | None] = mapped_column(
        StoredEnum(SwissLanguage, length=8), nullable=True
    )
    # The signature block: the plain-text lines under the user's name on a
    # document or a mail they author (title, department, phone) — the image
    # is `signature_image_ref` above.
    signature_block: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    @property
    def dealership_id(self) -> uuid.UUID:
        """API-facing alias for tenant_id — see module docstring."""

        return self.tenant_id
