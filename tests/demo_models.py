"""Test-only model exercising the shared mixins (PrimaryKeyMixin,
TimestampMixin, VersionedMixin, TenantScopedMixin) the way a real entity
from issue #2+ (Dealer, User, Customer, ...) will. Not part of the
production schema — only registered on Base.metadata within the test suite.
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin


class DemoWidget(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "demo_widget"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
