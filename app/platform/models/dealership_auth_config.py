"""DealershipAuthConfig (FR-AU-02, FR-AU-11, D-A-09, D-A-11, KAN-196): how a
Betrieb's users sign in. `auth_method` exists for the federation seam and
only ever holds `local` in v1 (the EntraOidcProvider is [P3]); `mfa_enforced`
is the manager's choice, default ON — a default that has to be actively
weakened. Created on first read with the defaults, so a Betrieb without a
row is a Betrieb with MFA enforced.
"""

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, UniqueConstraint
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
    __table_args__ = (UniqueConstraint("dealership_id", name="uq_dealership_auth_config_dealership_id"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    auth_method: Mapped[AuthMethod] = mapped_column(
        StoredEnum(AuthMethod, length=16), nullable=False, default=AuthMethod.LOCAL
    )
    mfa_enforced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
