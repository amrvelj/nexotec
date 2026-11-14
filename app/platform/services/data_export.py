"""The data export job (KAN-218; Dealer Administration D-A-31, FR-A-44;
Anto, 2026-11-13: Q1 A, Q2 B, Q4 A).

The manager requests an export of the whole Betrieb at any time; platform
staff produce one from the ten-year archive for a former manager who asks
support (no dealer login after offboarding), with a reason. Either way the
request is a row and the fact `platform.data_export.requested` in one
transaction; the worker's consumer produces the file (`produce`), so a large
Betrieb never holds a request open. Staging runs no worker (ADR-081): there a
request stays `requested`.

The file is one ZIP in the object store (`write_archive` in app.core.export:
CSV and JSON per entity, `schema.json`, every issued document as PDF, the
audit log, the uploaded files), read from one consistent snapshot of the
database whose start is `produced_at` — everything written before it is in
the file. It is downloadable through the authenticated GET for seven days
(a signed URL comes with the bucket, KAN-402); then the daily job deletes it
(`expire_downloads`). A new export can be requested at any time.

Every step is audited on the entity `data_export` (requested, produced,
failed, downloaded, acknowledged, expired); that trail is not a write of the
Betrieb's (app.core.export_core.NON_WRITE_ENTITY_TYPES), so acknowledging an
export never makes it out of date.
"""

import dataclasses
import datetime as dt
import logging
import tempfile
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.export import ExportScope, exporters, write_archive
from app.core.object_store import ObjectStore, get_object_store
from app.core.pagination import PageParams, build_page, paginate_query
from app.platform.models.export_job import ExportJob, ExportJobKind, ExportJobStatus
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.services import dealership as dealership_service

logger = logging.getLogger(__name__)

DOWNLOAD_WINDOW = dt.timedelta(days=7)
REQUESTED_EVENT = "platform.data_export.requested"
CONSUMER_NAME = "platform.data_export_production"
PRODUCER = "platform"
ENTITY_TYPE = "data_export"
CONTENT_TYPE = "application/zip"


def _audit(db: Session, job: ExportJob, action: str, *, actor_id: uuid.UUID | None, after=None, reason=None) -> None:
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=job.id,
        tenant_id=job.dealership_id,
        action=action,
        actor_id=actor_id,
        after=after,
        reason=reason,
    )


def scope_for(db: Session, dealership: Dealership) -> ExportScope:
    """The Betrieb, its group, and whether the group holds it alone (a
    customer that predates provenance is then its own too)."""

    group = dealership_service.list_group_dealerships(db, dealer_group_id=dealership.dealer_group_id)
    return ExportScope(
        dealership_id=dealership.id,
        group_id=dealership.dealer_group_id,
        group_is_single=all(other.id == dealership.id for other in group),
    )


def _with_references(db: Session, scope: ExportScope) -> ExportScope:
    """Every context names the records of others its rows carry, so a
    group-owned customer comes for the Betrieb's own relationship."""

    references: dict[str, set[uuid.UUID]] = {}
    for exporter in exporters():
        for kind, ids in exporter.references(db, scope).items():
            references.setdefault(kind, set()).update(ids)
    return dataclasses.replace(scope, references={kind: frozenset(ids) for kind, ids in references.items()})


def request_export(
    db: Session,
    *,
    dealership: Dealership,
    kind: ExportJobKind,
    actor_id: uuid.UUID,
    reason: str | None = None,
) -> ExportJob:
    """The manager's own export of a Betrieb that is not offboarded, or
    staff's archive export of one that is (Q2 B)."""

    if kind == ExportJobKind.SELF_SERVICE and dealership.status == DealershipStatus.OFFBOARDED:
        raise ConflictError(
            "This Betrieb is offboarded: its archive is exported by Nexotec support.",
            details={"reason": "dealership_offboarded"},
        )
    if kind == ExportJobKind.ARCHIVE and dealership.status != DealershipStatus.OFFBOARDED:
        raise ConflictError(
            "An archive export is for an offboarded Betrieb; a live Betrieb's manager exports it.",
            details={"reason": "dealership_not_offboarded"},
        )
    job = ExportJob(
        dealership_id=dealership.id,
        kind=kind,
        status=ExportJobStatus.REQUESTED,
        requested_by=actor_id,
        reason=reason,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(job)
    db.flush()
    outbox.publish(
        db,
        outbox.OutboxEvent(
            event_type=REQUESTED_EVENT,
            tenant_id=dealership.id,
            producer=PRODUCER,
            aggregate_type="data_export",
            aggregate_id=job.id,
            payload={"exportId": str(job.id), "dealershipId": str(dealership.id), "kind": kind.value},
        ),
    )
    _audit(db, job, "requested", actor_id=actor_id, after={"kind": kind.value}, reason=reason)
    db.commit()
    db.refresh(job)
    return job


def list_exports(
    db: Session, *, dealership_id: uuid.UUID, params: PageParams, kind: ExportJobKind | None = None
) -> tuple[list[ExportJob], str | None]:
    stmt = select(ExportJob).where(ExportJob.dealership_id == dealership_id)
    if kind is not None:
        stmt = stmt.where(ExportJob.kind == kind)
    rows = list(db.scalars(paginate_query(stmt, model=ExportJob, params=params)).all())
    return build_page(rows, params)


def get_export_or_404(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    export_id: uuid.UUID,
    kind: ExportJobKind | None = None,
    for_update: bool = False,
) -> ExportJob:
    stmt = select(ExportJob).where(ExportJob.id == export_id, ExportJob.dealership_id == dealership_id)
    if kind is not None:
        stmt = stmt.where(ExportJob.kind == kind)
    if for_update:
        stmt = stmt.with_for_update()
    job = db.scalar(stmt)
    if job is None:
        raise NotFoundError(f"Export {export_id} was not found.")
    return job


def _assert_downloadable(job: ExportJob, now: dt.datetime) -> None:
    if job.status == ExportJobStatus.EXPIRED or (
        job.status == ExportJobStatus.READY and job.download_expires_at is not None and job.download_expires_at <= now
    ):
        raise ConflictError(
            "This export's download has expired and its file was deleted — request a new export.",
            details={"reason": "export_expired"},
        )
    if job.status != ExportJobStatus.READY:
        raise ConflictError(
            "This export is not ready yet.",
            details={"reason": f"export_{job.status.value}", "status": job.status.value},
        )


def download(db: Session, *, job: ExportJob, actor_id: uuid.UUID, store: ObjectStore | None = None) -> bytes:
    """The file, through the authenticated GET (no signed URL before the
    bucket, KAN-402). Every download is audited."""

    _assert_downloadable(job, utcnow())
    stored = (store or get_object_store()).get(job.object_key or "")
    if stored is None:
        logger.error("data export %s is ready but its file is missing", job.id, extra={"exportId": str(job.id)})
        raise ConflictError(
            "This export's file is no longer there — request a new export.", details={"reason": "export_file_missing"}
        )
    _audit(db, job, "downloaded", actor_id=actor_id)
    db.commit()
    return stored.data


def acknowledge(db: Session, *, job: ExportJob, actor_id: uuid.UUID) -> ExportJob:
    """The manager confirms they received the export (FR-A-45): offboarding
    needs one acknowledged and produced after the Betrieb's last write."""

    if job.kind != ExportJobKind.SELF_SERVICE:
        raise ConflictError("Only the Betrieb's own export is acknowledged.", details={"reason": "export_not_own"})
    if job.acknowledged_at is not None:
        raise ConflictError("This export is acknowledged already.", details={"reason": "export_already_acknowledged"})
    _assert_downloadable(job, utcnow())
    job.acknowledged_at = utcnow()
    job.acknowledged_by = actor_id
    job.updated_by = actor_id
    job.version += 1
    _audit(db, job, "acknowledged", actor_id=actor_id)
    db.commit()
    db.refresh(job)
    return job


# --- production (the worker) ---------------------------------------------------


def _object_key(job: ExportJob) -> str:
    return f"dealerships/{job.dealership_id}/exports/{job.id}.zip"


# KAN-716: how far before the oldest writing transaction the look-back
# reaches — stamps taken early, clock skew between the application and the
# database (see `_snapshot_session`).
LOOKBACK_MARGIN = dt.timedelta(hours=1)


_XID_MASK = (1 << 32) - 1


@dataclasses.dataclass(frozen=True)
class _Snapshot:
    session: Session
    produced_at: dt.datetime
    # KAN-716: Postgres only (None on SQLite).
    xid_floor: int | None = None
    lookback_from: dt.datetime | None = None


def _snapshot_session(db: Session) -> _Snapshot:
    """A session of its own on one consistent snapshot (REPEATABLE READ on
    Postgres) and the snapshot's start — the export's `produced_at`.

    KAN-716: first, on a connection of its own, a probe of this database's
    open transactions that hold an id. The floor is the oldest such id, else
    the next id to be assigned: a transaction still open when the snapshot
    begins either held an id at the probe (at or above the floor) or takes
    one later (above every id at the probe), and a savepoint's id is above
    its parent's. Only this database is asked — a snapshot's own xmin counts
    every database of the cluster, so another's long transaction would hold
    it back. The look-back is the earliest start of those transactions, less
    `LOOKBACK_MARGIN`: a stamp is the application's clock, taken before its
    row reaches the database (sometimes earlier: a service takes `now` once
    and writes it later), and the clocks may skew. The look-back costs no
    precision; it only keeps old rows, whose 32-bit ids may have wrapped or
    been frozen, out of the id test. Transactions of another database role
    are not listed to this one; none writes a Betrieb's rows."""

    engine = db.get_bind().engine
    if engine.dialect.name != "postgresql":
        return _Snapshot(session=Session(bind=engine), produced_at=utcnow())
    with engine.connect() as probe:
        read_at, oldest_open, next_xid, open_xids = probe.execute(
            text(
                "SELECT clock_timestamp(), min(a.xact_start), n.next_xid,"
                " coalesce(array_agg(a.backend_xid::text::bigint)"
                " FILTER (WHERE a.backend_xid IS NOT NULL), '{}')"
                " FROM (SELECT pg_snapshot_xmax(pg_current_snapshot())::text::bigint AS next_xid) n"
                " LEFT JOIN pg_stat_activity a ON a.datname = current_database()"
                " AND a.backend_xid IS NOT NULL AND a.pid <> pg_backend_pid()"
                " GROUP BY n.next_xid"
            )
        ).one()
        probe.rollback()
    # Each open id as its distance below the next one (32-bit ids, modulo
    # 2^32); an id taken after the next one was read reads as "not below".
    below = [(next_xid - xid) & _XID_MASK for xid in open_xids]
    floor = next_xid - max([d for d in below if d < 1 << 31], default=0)
    snapshot = Session(bind=engine.execution_options(isolation_level="REPEATABLE READ"))
    # The first statement takes the snapshot; now() is its transaction's start.
    produced_at = snapshot.scalar(select(func.now())) or utcnow()
    lookback = (min(read_at, oldest_open) if oldest_open is not None else read_at) - LOOKBACK_MARGIN
    return _Snapshot(session=snapshot, produced_at=produced_at, xid_floor=floor, lookback_from=lookback)


def produce(db: Session, *, export_id: uuid.UUID, store: ObjectStore | None = None) -> ExportJob | None:
    """Writes the file of a requested export. Idempotent: an export no
    longer `requested` (a redelivery) is left alone. A failure is recorded
    as `failed` with a stable code and logged at ERROR — the manager
    requests a new one; nothing half-written is offered for download."""

    job = db.scalar(select(ExportJob).where(ExportJob.id == export_id).with_for_update())
    if job is None or job.status != ExportJobStatus.REQUESTED:
        return job
    store = store or get_object_store()
    taken = _snapshot_session(db)
    snapshot, produced_at = taken.session, taken.produced_at
    try:
        dealership_row = snapshot.get(Dealership, job.dealership_id)
        if dealership_row is None:
            raise LookupError("the export's Betrieb is gone")
        scope = _with_references(snapshot, scope_for(snapshot, dealership_row))
        # KAN-715: the archive goes to a temporary file on disk, then to the
        # store in chunks — never the whole ZIP in the worker's memory.
        key = _object_key(job)
        with tempfile.TemporaryFile(prefix="nexotec-export-") as spool:
            archive = write_archive(
                snapshot,
                target=spool,
                scope=scope,
                produced_at=produced_at,
                header={
                    "exportId": job.id,
                    "kind": job.kind.value,
                    "dealershipId": dealership_row.id,
                    "legalName": dealership_row.legal_name,
                    "uid": dealership_row.uid,
                },
            )
            spool.seek(0)
            store.put_file(key, spool, content_type=CONTENT_TYPE)
    except Exception:
        logger.exception("data export %s could not be produced", job.id, extra={"exportId": str(job.id)})
        job.status = ExportJobStatus.FAILED
        job.failure_reason = "production_failed"
        job.version += 1
        _audit(db, job, "failed", actor_id=None, after={"failure_reason": "production_failed"})
        db.flush()
        return job
    finally:
        snapshot.close()
    job.status = ExportJobStatus.READY
    job.produced_at = produced_at
    job.produced_xid_floor = taken.xid_floor
    job.produced_lookback_from = taken.lookback_from
    job.object_key = key
    job.byte_size = archive.byte_size
    job.download_expires_at = utcnow() + DOWNLOAD_WINDOW
    job.version += 1
    _audit(
        db,
        job,
        "produced",
        actor_id=None,
        after={
            "producedAt": produced_at.isoformat(),
            "documents": archive.document_count,
            "files": archive.file_count,
            "entities": len(archive.row_counts),
            "rows": sum(archive.row_counts.values()),
        },
    )
    db.flush()
    return job


def expire_downloads(db: Session, *, now: dt.datetime | None = None, store: ObjectStore | None = None) -> int:
    """Q1 A: a download stays seven days, then the file is deleted. One
    commit per export, so a stopped run loses nothing."""

    now = now or utcnow()
    store = store or get_object_store()
    due = db.scalars(
        select(ExportJob.id).where(ExportJob.status == ExportJobStatus.READY, ExportJob.download_expires_at <= now)
    ).all()
    for export_id in due:
        job = db.scalar(select(ExportJob).where(ExportJob.id == export_id).with_for_update())
        if job is None or job.status != ExportJobStatus.READY:
            db.rollback()
            continue
        if job.object_key:
            store.delete(job.object_key)
        job.status = ExportJobStatus.EXPIRED
        job.expired_at = now
        job.object_key = None
        job.version += 1
        _audit(db, job, "expired", actor_id=None)
        db.commit()
    return len(due)
