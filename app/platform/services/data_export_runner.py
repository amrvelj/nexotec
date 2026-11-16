"""Where the data export is produced and expired (KAN-738; ADR-081 as
amended by KAN-402, ADR-187).

The object store is the web service's persistent disk (KAN-402), and a
Render disk belongs to one service: the worker can neither write an archive
the web's download route reads, nor read the logos and imagery the archive
holds. So, while the store is the web's disk, the **web service** produces
every export and deletes every expired one, in a background thread of its
own process (`start`, from app.main) — the worker does neither.

The queue is the `export_job` row itself, not the outbox: a request is a row
in status `requested` (and the fact `platform.data_export.requested`, which
stays published for whoever wants it), and the runner claims one at a time
with `FOR UPDATE SKIP LOCKED`, produces it (`data_export.produce`) and
commits. A restart mid-production rolls the claim back, so the export is
still `requested` and the next pass produces it: nothing is lost, nothing is
produced twice. A request wakes the runner at once (`wake`); otherwise it
looks every `POLL_INTERVAL`. Each pass also deletes the files whose seven
days have passed (`data_export.expire_downloads`, Anto's Q1 A) — within a
minute of expiry instead of the next daily run.

Costs, accepted while the store is a disk: a large export takes CPU from the
web process's requests for its duration (the archive streams to a temporary
file and then to the store in chunks, KAN-715, so memory stays bounded);
and the web is one instance (the disk already rules out scaling out). When
the Swiss S3 store comes, the worker can read the store again; moving the
export back to it is that follow-up's decision.
"""

import logging
import threading
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.object_store import ObjectStore
from app.platform.models.export_job import ExportJob, ExportJobStatus
from app.platform.services import data_export

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30.0


def _claim_next(db: Session):
    return db.scalar(
        select(ExportJob.id)
        .where(ExportJob.status == ExportJobStatus.REQUESTED)
        .order_by(ExportJob.created_at, ExportJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def run_once(session_factory: Callable[[], Session], *, store: ObjectStore | None = None) -> int:
    """One pass: every requested export produced, one per transaction, then
    every expired file deleted. Returns the number of exports produced or
    recorded as failed. An export another pass holds is skipped."""

    handled = 0
    while True:
        db = session_factory()
        try:
            export_id = _claim_next(db)
            if export_id is None:
                db.rollback()
                break
            data_export.produce(db, export_id=export_id, store=store)
            db.commit()
            handled += 1
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    db = session_factory()
    try:
        data_export.expire_downloads(db, store=store)
    finally:
        db.close()
    return handled


class _Runner:
    def __init__(self, session_factory: Callable[[], Session], interval: float) -> None:
        self._session_factory = session_factory
        self._interval = interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="data-export-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                run_once(self._session_factory)
            except Exception:
                logger.exception("data export runner: a pass failed")
            self._wake.wait(self._interval)


_runner: _Runner | None = None


def start(session_factory: Callable[[], Session], *, interval: float = POLL_INTERVAL_SECONDS) -> None:
    """Starts this process's runner (once). app.main calls it in every
    deployed environment and in local development, never under pytest —
    tests call `run_once`."""

    global _runner
    if _runner is None:
        _runner = _Runner(session_factory, interval)
        _runner.start()


def stop() -> None:
    global _runner
    if _runner is not None:
        _runner.stop(timeout=5)
        _runner = None


def wake() -> None:
    """A new request: look now, not at the next interval. A no-op where no
    runner runs (pytest)."""

    if _runner is not None:
        _runner.wake()
