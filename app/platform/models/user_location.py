"""UserLocation: which sites of the Betrieb a user works at (KAN-200;
Dealer Administration, Users → Fields: "primary location · additional
locations", FR-A-02). One row per (user, location); exactly one primary per
user, enforced by a partial unique index — a location is an attribute,
never a scoping boundary (ADR-014), so this carries no permission.

Both ends are platform-owned, so these are real foreign keys, not rule 2's
three-column pattern. The rows are replaced as a set through the user's
own create and PATCH (`primaryLocationId`, `additionalLocationIds`, in
`services/user_location.set_locations`); a location of another dealership
is refused there, never admitted here.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin
from app.core.types import GUID
from app.db import Base


class UserLocation(PrimaryKeyMixin, Base):
    __tablename__ = "user_location"
    __table_args__ = (
        UniqueConstraint("user_id", "location_id", name="uq_user_location_user_id_location_id"),
        Index(
            "uq_user_location_one_primary",
            "user_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("location.id"), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
