"""UserPreference: per-user, per-scope UI layout state (grid columns, sort,
density, saved views, sidebar state) — the platform capability `UI/UX Core
Principles` § User-Level Preference Persistence requires before any data
grid can be built (blocker U-01). One row per (user, scope); PUT replaces
the whole scope, matching the doc's `/v1/me/preferences/{scope}` contract.

No VersionedMixin/If-Match: the doc is explicit that layout preferences use
last-write-wins ("on conflict the server value wins... never block") rather
than optimistic concurrency — losing a column-drag race is not worth
interrupting the user's work over.
"""

import datetime as dt

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import utcnow
from app.models.types import GUID, UTCDateTime
from app.db import Base


class UserPreference(Base):
    __tablename__ = "user_preference"

    # No synthetic id: the doc specifies primary key (user_id, scope)
    # directly, and there is no use case for referencing a preference row
    # from elsewhere.
    user_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("user.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), primary_key=True)

    # Generic JSON (not a Postgres-specific JSONB variant), matching the
    # portable-column convention every other model uses (see
    # Dealer.oem_affiliations) so the SQLite test lane keeps working.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )
