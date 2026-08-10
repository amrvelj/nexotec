"""User: a person acting on behalf of a Dealer. User.tenant_id (inherited
from TenantScopedMixin, FK-constrained here to dealer.id) is the spec's
`dealer_id` field — reusing the shared mixin's column name keeps every
tenant-scoped entity uniform for app.core.tenancy.get_or_404 and friends;
the API layer exposes it as `dealerId` (see app/schemas/user.py) to match
the spec's domain language.
"""

import enum
import uuid

from sqlalchemy import Enum as SAEnum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.auth import AccessRole
from app.db import Base
from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID


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

    # Overrides TenantScopedMixin's bare column to add the FK to dealer.id —
    # User is the first entity to introduce a real FK relationship (CTO
    # review flagged this as the point where SQLite-vs-Postgres constraint
    # enforcement starts to matter; see the Postgres CI-lane addition).
    tenant_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("dealer.id"), nullable=False, index=True)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole, native_enum=False, length=32), nullable=False)
    access_role: Mapped[AccessRole] = mapped_column(
        SAEnum(AccessRole, native_enum=False, length=32), nullable=False
    )
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
    def dealer_id(self) -> uuid.UUID:
        """API-facing alias for tenant_id — see module docstring."""

        return self.tenant_id
