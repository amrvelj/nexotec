"""The marker of a Betrieb's planner starting sets (KAN-808 PR-1d; ADR-222).

When a Betrieb first holds Aftersales, planning copies Nexotec's starting sets
into its lists — technician types and workplace types, each entry a sample
(`services/starting_sets.py`). This row says the copy happened: one per
Betrieb, ever (`uq_planning_starting_set_tenant_id`). The copy checks it under
the Betrieb's master-data lock, so a redelivered fact, a later re-grant or a
restatement copies nothing again — and a Betrieb that deleted a sample never
gets it back.

- `edition`: which version of the sets was copied (`starting_sets.EDITION`).
- `source_event_id`: the outbox message whose fact caused the copy (core's
  `outbox_message.id`; no foreign key — the outbox is pruned, the marker is
  kept). None for a copy no fact caused.

The row carries no actor: the copy is the system's, its audit row
`starting_set_copied` says which fact caused it.
"""

import datetime as dt
import uuid

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.text_collation import IDENTIFIER_TEXT
from app.core.types import GUID, UTCDateTime
from app.db import Base

EDITION_LENGTH = 16


class StartingSet(PrimaryKeyMixin, Base):
    __tablename__ = "planning_starting_set"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_planning_starting_set_tenant_id"),)

    # Unique, so not the mixin's plain index (ADR-221 T7: every table carries it).
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Dealership.id). No DB-level FK."
    )
    edition: Mapped[str] = mapped_column(String(EDITION_LENGTH), nullable=False, info=IDENTIFIER_TEXT)
    copied_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="core's outbox_message.id of the fact that caused the copy. No DB-level FK."
    )
