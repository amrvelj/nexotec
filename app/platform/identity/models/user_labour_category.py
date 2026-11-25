"""A user allocated to a labour-rate category of a Betrieb (KAN-806; D-A-19:
«technicians are allocated to one or more categories, so a job line proposes
the right rate from who did the work»).

One row per user and category: it exists or it does not, like a switched-on
contact group. A user may be in several categories. The row is kept when the
user is deactivated (reads mark the member inactive) and when the category
is deactivated. Users, Betriebe and categories are platform's, so the foreign
keys are real; the composite key keeps the category inside the row's Betrieb.
"""

import uuid

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID
from app.db import Base


class UserLabourCategory(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_labour_category"
    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "dealership_id"],
            ["labour_rate_category.id", "labour_rate_category.dealership_id"],
            name="fk_user_labour_category_category_dealership",
        ),
        UniqueConstraint("user_id", "category_id", name="uq_user_labour_category_user_id_category_id"),
        Index("ix_user_labour_category_dealership_id", "dealership_id"),
        Index("ix_user_labour_category_category_id", "category_id"),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
