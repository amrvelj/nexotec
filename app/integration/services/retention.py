"""ADR-024's three retention tiers (WP-6 PR-6):

- `integration_call_log` (call metadata) — 24 months.
- `integration_call_payload` where `kind=error` — 30 days.
- `integration_call_payload` where `kind=success` — 7 days.

Each purge function deletes rows strictly older than its own cutoff and
returns the count deleted, so the daily job (app/integration/daily_jobs.py)
can log what it did rather than purging silently. Nothing here ever reads
a payload's own content — a purge is a blind, tenant-agnostic age check
against `created_at`, the same posture the break-glass endpoint's own
docstring describes for reads.

`capture_call_payload` is the write path — currently unreached in this
session's own adapters. Neither `MockAutoIDatAdapter` (PR-2) nor
`AutoIDatSoapAdapter` (PR-3) has anywhere it surfaces a raw wire payload
today (the mock has no wire payload at all; the real adapter's `zeep`
client isn't wired to capture request/response XML in this session, per
that adapter's own "no real WSDL" caveat) — `services/gateway.py`'s
`call_capability` accepts an optional `capture_raw_payload` callable
specifically so a future adapter revision can start supplying one without
any change to this module, the purge jobs, or the break-glass endpoint,
all of which are already fully correct and tested against directly-
constructed rows.
"""

import datetime as dt
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import NotFoundError
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload, PayloadKind

_CALL_LOG_RETENTION_DAYS = 24 * 30  # ~24 months
_ERROR_PAYLOAD_RETENTION_DAYS = 30
_SUCCESS_PAYLOAD_RETENTION_DAYS = 7


def capture_call_payload(
    db: Session, *, call_log: IntegrationCallLog, kind: PayloadKind, payload: str
) -> IntegrationCallPayload:
    row = IntegrationCallPayload(call_log_id=call_log.id, tenant_id=call_log.tenant_id, kind=kind, payload=payload)
    db.add(row)
    db.flush()
    return row


def _cutoff_datetime(*, today: dt.date, retention_days: int) -> dt.datetime:
    cutoff_date = today - dt.timedelta(days=retention_days)
    return dt.datetime.combine(cutoff_date, dt.time.min, tzinfo=dt.UTC)


def _purge_where(db: Session, *, model, ids: list[uuid.UUID]) -> int:
    if not ids:
        return 0
    db.execute(delete(model).where(model.id.in_(ids)))
    db.commit()
    return len(ids)


def purge_call_log_metadata(db: Session, *, today: dt.date | None = None) -> int:
    cutoff = _cutoff_datetime(today=today or utcnow().date(), retention_days=_CALL_LOG_RETENTION_DAYS)
    ids = list(db.scalars(select(IntegrationCallLog.id).where(IntegrationCallLog.created_at < cutoff)).all())
    return _purge_where(db, model=IntegrationCallLog, ids=ids)


def purge_error_payloads(db: Session, *, today: dt.date | None = None) -> int:
    cutoff = _cutoff_datetime(today=today or utcnow().date(), retention_days=_ERROR_PAYLOAD_RETENTION_DAYS)
    ids = list(
        db.scalars(
            select(IntegrationCallPayload.id).where(
                IntegrationCallPayload.kind == PayloadKind.ERROR, IntegrationCallPayload.created_at < cutoff
            )
        ).all()
    )
    return _purge_where(db, model=IntegrationCallPayload, ids=ids)


def purge_success_payloads(db: Session, *, today: dt.date | None = None) -> int:
    cutoff = _cutoff_datetime(today=today or utcnow().date(), retention_days=_SUCCESS_PAYLOAD_RETENTION_DAYS)
    ids = list(
        db.scalars(
            select(IntegrationCallPayload.id).where(
                IntegrationCallPayload.kind == PayloadKind.SUCCESS, IntegrationCallPayload.created_at < cutoff
            )
        ).all()
    )
    return _purge_where(db, model=IntegrationCallPayload, ids=ids)


def get_call_payload_or_404(db: Session, *, payload_id: uuid.UUID) -> IntegrationCallPayload:
    row = db.get(IntegrationCallPayload, payload_id)
    if row is None:
        raise NotFoundError(f"Call payload {payload_id} was not found.")
    return row
