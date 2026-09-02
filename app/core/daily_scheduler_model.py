import datetime as dt

from sqlalchemy import Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.db import Base


class DailyJobRun(PrimaryKeyMixin, TimestampMixin, Base):
    """One row per (job_name, run_date) that has actually completed —
    written only AFTER the job body returns without raising (see
    app/core/daily_scheduler.py::run_due_daily_jobs). This table's whole
    job is answering "has this run today, successfully", not recording
    every attempt: a failed attempt leaves no row, so the next poll cycle
    (1s later, app/worker.py's own interval) tries again, same as the
    outbox worker's own at-least-once posture for a claimed message.
    """

    __tablename__ = "daily_job_run"
    __table_args__ = (UniqueConstraint("job_name", "run_date", name="uq_daily_job_run_job_name_run_date"),)

    job_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    run_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
