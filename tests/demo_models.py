"""Test-only model exercising the shared mixins (PrimaryKeyMixin,
TimestampMixin, VersionedMixin, TenantScopedMixin) the way a real entity
from issue #2+ (Dealership, User, Customer, ...) will.

KAN-332: it lives on a declarative base of its own, never on app.db.Base, so
Base.metadata holds the production tables and nothing else — an in-process
comparison with a migrated database, or a create_all of the production
schema, never carries demo_widget. tests/conftest.py builds and resets
DemoBase.metadata beside Base.metadata (TEST_METADATA).
"""

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin


class DemoBase(DeclarativeBase):
    pass


class DemoWidget(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, DemoBase):
    __tablename__ = "demo_widget"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
