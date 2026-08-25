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

import enum
import uuid

from sqlalchemy import JSON, Boolean, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID
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

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Dealership). No DB-level FK."
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole, native_enum=False, length=32), nullable=False)
    # Role *values* (AccessRole.value strings), not AccessRole members —
    # SQLAlchemy's Enum type only maps a column to ONE enum member per row,
    # so a genuinely multi-valued set has to be plain JSON here. See
    # services/user.py for the AccessRole <-> str conversion at the
    # schema/service boundary; nothing outside that layer should read this
    # column directly.
    access_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    # Administration of THIS dealership only (Roles & Permissions RP-1,
    # ADR-026) — orthogonal to access_roles. A dealership must always have
    # at least one active manager; see services/user.py::_assert_not_last_manager.
    is_dealer_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        SAEnum(EmploymentStatus, native_enum=False, length=32),
        nullable=False,
        default=EmploymentStatus.ACTIVE,
    )

    # Placeholder FK to an external IdP subject (spec cross-cutting #10) —
    # no credentials stored here. Real IdP integration is still unselected;
    # see the PR notes for how this is provisioned in the shell.
    auth_identity_id: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, native_enum=False, length=32), nullable=False, default=UserStatus.INVITED
    )

    @property
    def dealership_id(self) -> uuid.UUID:
        """API-facing alias for tenant_id — see module docstring."""

        return self.tenant_id
