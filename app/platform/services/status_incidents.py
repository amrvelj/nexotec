"""Nexotec's own platform status (KAN-223; D-A-38: «Nexotec status from a
platform-maintained status record — platform admin sets incidents; no
external status-page dependency in v1»).

Platform staff open an incident (severity, title and text in the four UI
languages, from, optionally to) and end it by setting `endsAt`. An incident
is never deleted: it is the record of what every Betrieb's page said. Every
write is audited (tenant null — it is Nexotec's, not a Betrieb's). Never
commits.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.platform.models.status_page import IncidentSeverity, PlatformStatusIncident

ENTITY_TYPE = "platform_status_incident"
LANGUAGES = ("de", "fr", "it", "en")
TITLE_MAX = 200
BODY_MAX = 2000


def _texts(field: str, value: dict[str, str], limit: int) -> dict[str, str]:
    """All four languages, each non-empty and within `limit` — the page never
    falls back to another language (CLAUDE.md, i18n)."""

    missing = [language for language in LANGUAGES if not (value.get(language) or "").strip()]
    if missing or set(value) - set(LANGUAGES):
        raise UnprocessableEntityError(
            f"An incident's {field} is needed in German, French, Italian and English.",
            details={"reason": "status_incident_language_missing", "field": field, "languages": missing},
        )
    too_long = [language for language in LANGUAGES if len(value[language].strip()) > limit]
    if too_long:
        raise UnprocessableEntityError(
            f"An incident's {field} is at most {limit} characters.",
            details={"reason": "status_incident_text_too_long", "field": field, "languages": too_long},
        )
    return {language: value[language].strip() for language in LANGUAGES}


def _check_window(starts_at: dt.datetime, ends_at: dt.datetime | None) -> None:
    if ends_at is not None and ends_at <= starts_at:
        raise UnprocessableEntityError(
            "An incident ends after it starts.", details={"reason": "status_incident_ends_before_start"}
        )


def _audit_state(row: PlatformStatusIncident) -> dict[str, object]:
    return {
        "severity": row.severity.value,
        "title": row.title,
        "body": row.body,
        "startsAt": row.starts_at.isoformat(),
        "endsAt": row.ends_at.isoformat() if row.ends_at else None,
    }


def list_incidents(db: Session, *, limit: int = 100) -> list[PlatformStatusIncident]:
    """The newest first — the staff screen's list."""

    return list(
        db.scalars(
            select(PlatformStatusIncident)
            .order_by(PlatformStatusIncident.starts_at.desc(), PlatformStatusIncident.id.desc())
            .limit(limit)
        ).all()
    )


def get_incident_or_404(db: Session, incident_id: uuid.UUID, *, for_update: bool = False) -> PlatformStatusIncident:
    query = select(PlatformStatusIncident).where(PlatformStatusIncident.id == incident_id)
    if for_update:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise NotFoundError(f"Status incident {incident_id} was not found.")
    return row


def open_incident(
    db: Session,
    *,
    severity: IncidentSeverity,
    title: dict[str, str],
    body: dict[str, str],
    starts_at: dt.datetime | None,
    ends_at: dt.datetime | None,
    actor_id: uuid.UUID,
) -> PlatformStatusIncident:
    start = starts_at or utcnow()
    _check_window(start, ends_at)
    row = PlatformStatusIncident(
        severity=severity,
        title=_texts("title", title, TITLE_MAX),
        body=_texts("body", body, BODY_MAX),
        starts_at=start,
        ends_at=ends_at,
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        tenant_id=None,
        action="status_incident_opened",
        actor_id=actor_id,
        after=_audit_state(row),
    )
    return row


_UNSET = object()


def update_incident(
    db: Session,
    row: PlatformStatusIncident,
    *,
    actor_id: uuid.UUID,
    severity: IncidentSeverity | None = None,
    title: dict[str, str] | None = None,
    body: dict[str, str] | None = None,
    starts_at: dt.datetime | None = None,
    ends_at: "dt.datetime | None | object" = _UNSET,
) -> PlatformStatusIncident:
    """Changes what was given. `ends_at=None` reopens an ended incident;
    leaving it out keeps it."""

    before = _audit_state(row)
    if severity is not None:
        row.severity = severity
    if title is not None:
        row.title = _texts("title", title, TITLE_MAX)
    if body is not None:
        row.body = _texts("body", body, BODY_MAX)
    if starts_at is not None:
        row.starts_at = starts_at
    if ends_at is not _UNSET:
        row.ends_at = ends_at  # type: ignore[assignment]
    _check_window(row.starts_at, row.ends_at)
    after = _audit_state(row)
    if after != before:
        row.version += 1
        db.flush()
        record_audit_event(
            db,
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            tenant_id=None,
            action="status_incident_updated",
            actor_id=actor_id,
            before=before,
            after=after,
        )
    return row
