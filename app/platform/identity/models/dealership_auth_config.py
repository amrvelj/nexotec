"""DealershipAuthConfig (FR-AU-02, FR-AU-11, D-A-09, D-A-11, KAN-196): how a
Betrieb's users sign in. `auth_method` exists for the federation seam and
only ever holds `local` in v1 (the EntraOidcProvider is [P3]); `mfa_enforced`
is the manager's choice, default ON — a default that has to be actively
weakened. `session_idle_minutes` / `session_absolute_hours` are the
Betrieb's stricter session policy (KAN-229, D-A-37). Created on first read with the defaults, so a Betrieb without a
row is a Betrieb with MFA enforced.
"""

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID
from app.db import Base


class AuthMethod(str, enum.Enum):
    LOCAL = "local"


class DealershipAuthConfig(PrimaryKeyMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "dealership_auth_config"

    # KAN-431: the migration's constraint name, not SQLAlchemy's default
    # `{table}_{column}_key` — the schema gate compares indexes by name.
    __table_args__ = (
        UniqueConstraint("dealership_id", name="uq_dealership_auth_config_dealership_id"),
        # KAN-229 (D-A-37, FR-AU-28; Anto 2026-11-11): the bounds a Betrieb may
        # set — `app/platform/identity/services/session_policy.py` holds the same numbers.
        CheckConstraint(
            "session_idle_minutes IS NULL OR session_idle_minutes BETWEEN 15 AND 120",
            name="ck_dealership_auth_config_session_idle_minutes",
        ),
        CheckConstraint(
            "session_absolute_hours IS NULL OR session_absolute_hours BETWEEN 1 AND 12",
            name="ck_dealership_auth_config_session_absolute_hours",
        ),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    auth_method: Mapped[AuthMethod] = mapped_column(
        StoredEnum(AuthMethod, length=16), nullable=False, default=AuthMethod.LOCAL
    )
    mfa_enforced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # KAN-229 (D-A-37, FR-AU-28): the Betrieb's own, stricter session policy.
    # NULL = the platform value (idle `session_idle_minutes`, absolute end at
    # the next midnight Europe/Zurich). Never looser than the platform.
    session_idle_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_absolute_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
