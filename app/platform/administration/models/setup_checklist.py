"""What the setup checklist keeps besides its derivation (KAN-214; Dealer
Administration D-A-21, FR-A-26, FR-A-34).

The checklist itself is **derived** on every read (`services/setup_checklist.py`)
— no item is ever ticked or stored. The manager's confirmation of the number
ranges is a fact of the ranges (`organisation/models/number_range.py`,
`number_range_confirmation`). What is kept here:

- `setup_checklist_regression` — the daily platform job's ledger of an item
  that broke after activation (an IBAN deleted, a location's hours removed):
  one open row per Betrieb × item while it stays broken, so the managers get
  one `administration` notification per regression, not one per night.
  `resolved_at` is set by the same job once the item is complete again.
"""

import datetime as dt
import uuid

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class SetupChecklistRegression(PrimaryKeyMixin, Base):
    __tablename__ = "setup_checklist_regression"
    __table_args__ = (
        Index(
            "uq_setup_checklist_regression_open",
            "dealership_id",
            "item_code",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
            sqlite_where=text("resolved_at IS NULL"),
        ),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    item_code: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
