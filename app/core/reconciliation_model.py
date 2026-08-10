import datetime as dt

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ReconciliationRun(PrimaryKeyMixin, Base):
    """One row per reconciliation job execution (P-10). Persists even when
    zero orphans are found — that's what proves the job actually ran and
    checked something, rather than an empty reconciliation_orphan table
    being indistinguishable from the job never having run at all.
    """

    __tablename__ = "reconciliation_run"

    context: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    checks_run: Mapped[int] = mapped_column(Integer, nullable=False)
    orphans_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ReconciliationOrphan(PrimaryKeyMixin, Base):
    """One row per dangling cross-context reference found by a run. Never
    written to by anything except the reconciliation job itself — it
    reports, it never deletes or repairs (P-10).
    """

    __tablename__ = "reconciliation_orphan"

    run_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("reconciliation_run.id"), nullable=False, index=True)
    context: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    check_label: Mapped[str] = mapped_column(String(128), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_row_id: Mapped[GUID] = mapped_column(GUID(), nullable=False)
    target_table: Mapped[str] = mapped_column(String(64), nullable=False)
    dangling_value: Mapped[GUID] = mapped_column(GUID(), nullable=False)
    detected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
