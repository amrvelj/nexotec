"""StandardTextDelegation (KAN-199, FR-A-28, D-A-23): a manager grants one
named user write access to ONE category of the dealership's standard texts —
the service manager edits workshop texts, the sales lead edits offer texts —
without the manager flag. The one contained exception to "no per-dealership
roles": it grants write on one category to one user, nothing else, never on
`source = platform_seed` rows or the document template.

One row per (user, category), re-used across grants like module_entitlement:
a withdrawn delegation keeps its row with `valid_to`, a re-grant reopens it,
and deactivating the user ends every delegation. `dealership_id` is the
dealership the texts belong to — the user's home, where the manager who
granted it administers; the delegation never travels into a sister
dealership. Both sides are platform-owned, so real FKs are correct.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class StandardTextCategory(str, enum.Enum):
    """The PRD's standard-text categories (Dealer Administration, "Standard
    texts" data model): document texts · e-mail bodies · SMS · internal note
    templates · workshop job texts."""

    DOCUMENT = "document"
    EMAIL = "email"
    SMS = "sms"
    INTERNAL_NOTE = "internal_note"
    WORKSHOP = "workshop"


class StandardTextDelegation(PrimaryKeyMixin, Base):
    __tablename__ = "standard_text_delegation"
    __table_args__ = (UniqueConstraint("user_id", "category", name="uq_standard_text_delegation_user_id_category"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    category: Mapped[StandardTextCategory] = mapped_column(StoredEnum(StandardTextCategory, length=16), nullable=False)
    granted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    valid_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    def is_active(self, at: dt.datetime) -> bool:
        return self.valid_to is None or self.valid_to > at
