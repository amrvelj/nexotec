"""Where the Business Profile photos are uploaded (KAN-226, KAN-784;
ADR-209): in the **web service**, beside the object store's disk (ADR-081 as
amended, KAN-738) — the pattern of `data_export_runner`.

The queue is `integration_gbp_photo_job` (the worker's consumers write it,
`gbp_photos.queue_photo_pass`). Each pass claims one due job at a time
(`gbp_photos.claim_due_job`: `FOR UPDATE SKIP LOCKED`, the job moved a lease
ahead, committed) and runs it (`gbp_photos.run_photo_job`), which commits
every upload as it lands. So a second runner never takes the same job while
it runs, a restart mid-pass leaves the job to be taken again after its lease,
and an upload recorded before the restart is never sent again.

One job's failure never holds the others back: `run_photo_job` records its
own failures on the job (backoff, then dead letter); anything that still
escapes is logged at ERROR and the job is skipped for the rest of the pass.
Each pass has its own correlation id (KAN-125). It looks every
`POLL_INTERVAL_SECONDS`; started from app.main, never under pytest — tests
call `run_once`.
"""

import logging
import threading
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.correlation import correlation_scope
from app.core.object_store import ObjectStore
from app.integration.services import gbp_photos

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60.0


def run_once(session_factory: Callable[[], Session], *, store: ObjectStore | None = None) -> int:
    """One pass over every due job. Returns how many jobs it ran."""

    with correlation_scope():
        return _pass(session_factory, store=store)


def _pass(session_factory: Callable[[], Session], *, store: ObjectStore | None) -> int:
    handled = 0
    seen: set[uuid.UUID] = set()
    while True:
        db = session_factory()
        try:
            try:
                claim = gbp_photos.claim_due_job(db, skip=seen)
            except Exception:
                db.rollback()
                logger.exception("business profile photo runner: could not claim a due job")
                return handled
            if claim is None:
                return handled
            seen.add(claim.job_id)
            try:
                gbp_photos.run_photo_job(db, claim, store=store)
                handled += 1
            except Exception:
                db.rollback()
                logger.exception(
                    "business profile photo runner: job %s left for after its lease",
                    claim.job_id,
                    extra={"photoJobId": str(claim.job_id)},
                )
        finally:
            db.close()


class _Runner:
    def __init__(self, session_factory: Callable[[], Session], interval: float) -> None:
        self._session_factory = session_factory
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="gbp-photo-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                run_once(self._session_factory)
            except Exception:
                logger.exception("business profile photo runner: a pass failed")
            self._stop.wait(self._interval)


_runner: _Runner | None = None


def start(session_factory: Callable[[], Session], *, interval: float = POLL_INTERVAL_SECONDS) -> None:
    """Starts this process's runner (once). app.main calls it beside the
    export runner."""

    global _runner
    if _runner is None:
        _runner = _Runner(session_factory, interval)
        _runner.start()


def stop() -> None:
    global _runner
    if _runner is not None:
        _runner.stop(timeout=5)
        _runner = None
