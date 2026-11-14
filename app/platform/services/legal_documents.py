"""Legal documents and their acceptance (KAN-214; Dealer Administration
FR-A-39, D-A-34; Anto, 2026-11-13: Q1 A, Q3 A).

- **Publishing** — platform staff publish a version of the ToS or the DPA in
  one language (`publish`). A version is never edited; a correction is a new
  version. Versions are dotted numbers compared number by number ("0.10" is
  after "0.9").
- **Current version** — per type, the highest version whose effective date
  has come (Swiss calendar day). Every language of a version shares it: a
  Betrieb accepts the version, in the language it read.
- **Acceptance** — the manager accepts the current version of each type for
  the Betrieb (`accept`): one append-only `legal_acceptance` row with the
  user, the time, the address and the browser. Activation needs both
  current versions accepted (`services/activation.py`).
- **A new version** (`acceptance_state`): a Betrieb that has not accepted a
  current version is *due*; it is prompted at every login of its managers
  until `effective_date + acceptance_grace_days` (30 by default, Q1 A), and
  after that its managers are *held*: the session guard lets them reach only
  the acceptance screen (`app/platform/session_guard.py`). Colleagues are
  never held. A Betrieb still `pending_onboarding` is never held: its
  acceptance is part of activation.
- **Production refuses a draft** (Q3 A): activation in production is refused
  while a current version is below 1.0 — the 0.9 «Entwurf» texts the
  migration seeded are good for staging only (`drafts_block_activation`).
"""

import dataclasses
import datetime as dt
import re
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.config import get_settings
from app.core.deployment_environment import DeploymentEnvironment, current_deployment_environment
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.platform.models.legal import LegalAcceptance, LegalDocument, LegalDocumentType
from app.platform.organisation.models.dealership import Dealership, DealershipStatus

_VERSION = re.compile(r"^\d{1,3}(\.\d{1,3}){0,2}$")
# The first version counsel's text carries (Q3 A); below it a text is a draft.
FINAL_VERSION = (1,)  # version_key("1.0")


def swiss_today() -> dt.date:
    return dt.datetime.now(ZoneInfo(get_settings().session_timezone)).date()


def version_key(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in version.split("."))
    # "1" and "1.0" are the same version: compare without trailing zeros.
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def is_draft(version: str) -> bool:
    return version_key(version) < FINAL_VERSION


def validate_version(version: str) -> str:
    version = version.strip()
    if not _VERSION.match(version):
        raise UnprocessableEntityError(
            "A version is one to three dotted numbers, for example 1.0 or 1.2.1.",
            details={"reason": "legal_document_version_invalid", "field": "version"},
        )
    return version


# --- publishing -------------------------------------------------------------------------------


def list_documents(db: Session) -> list[LegalDocument]:
    rows = list(db.scalars(select(LegalDocument)).all())
    rows.sort(key=lambda d: (d.document_type.value, version_key(d.version), d.language.value), reverse=False)
    return rows


def get_document_or_404(db: Session, document_id: uuid.UUID) -> LegalDocument:
    row = db.get(LegalDocument, document_id)
    if row is None:
        raise NotFoundError("Legal document not found.")
    return row


def publish(
    db: Session,
    *,
    document_type: LegalDocumentType,
    version: str,
    language: SwissLanguage,
    effective_date: dt.date,
    title: str,
    body_markdown: str,
    acceptance_grace_days: int,
    actor_id: uuid.UUID,
) -> LegalDocument:
    """A new version of one document in one language, by platform staff.
    Refused when that type × version × language exists (409), when the
    version is older than the type's newest one (409: versions only move
    forward), and for an empty title or body (422). Audited. Never commits."""

    version = validate_version(version)
    if not title.strip() or not body_markdown.strip():
        raise UnprocessableEntityError(
            "A legal document needs a title and a text.", details={"reason": "legal_document_empty"}
        )
    existing = db.scalars(select(LegalDocument).where(LegalDocument.document_type == document_type)).all()
    if any(version_key(row.version) == version_key(version) and row.language == language for row in existing):
        raise ConflictError(
            "This version of the document already exists in this language.",
            details={"reason": "legal_document_version_exists"},
        )
    newest = max((version_key(row.version) for row in existing), default=None)
    if newest is not None and version_key(version) < newest:
        raise ConflictError(
            "A new version must be later than the newest published one.",
            details={"reason": "legal_document_version_not_newer"},
        )
    row = LegalDocument(
        document_type=document_type,
        version=version,
        language=language,
        effective_date=effective_date,
        title=title.strip(),
        body_markdown=body_markdown,
        acceptance_grace_days=acceptance_grace_days,
        published_by=actor_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "This version of the document already exists in this language.",
            details={"reason": "legal_document_version_exists"},
        ) from None
    record_audit_event(
        db,
        entity_type="legal_document",
        entity_id=row.id,
        tenant_id=None,
        action="publish",
        actor_id=actor_id,
        after={
            "documentType": document_type.value,
            "version": version,
            "language": language.value,
            "effectiveDate": effective_date.isoformat(),
            "acceptanceGraceDays": acceptance_grace_days,
        },
    )
    return row


# --- the current versions ---------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CurrentVersion:
    document_type: LegalDocumentType
    version: str
    effective_date: dt.date
    acceptance_grace_days: int
    # The rows of this version, one per language it was published in.
    documents: tuple[LegalDocument, ...]

    @property
    def due_by(self) -> dt.date:
        return self.effective_date + dt.timedelta(days=self.acceptance_grace_days)

    def in_language(self, language: SwissLanguage | str | None) -> LegalDocument:
        """The text in `language`, else German, else the first published."""

        wanted = language.value if isinstance(language, SwissLanguage) else language
        for preferred in (wanted, SwissLanguage.DE.value):
            for row in self.documents:
                if row.language.value == preferred:
                    return row
        return self.documents[0]


def current_versions(db: Session, *, today: dt.date | None = None) -> dict[LegalDocumentType, CurrentVersion]:
    today = today or swiss_today()
    effective = db.scalars(select(LegalDocument).where(LegalDocument.effective_date <= today)).all()
    by_type: dict[LegalDocumentType, list[LegalDocument]] = {}
    for row in effective:
        by_type.setdefault(row.document_type, []).append(row)
    result: dict[LegalDocumentType, CurrentVersion] = {}
    for document_type, rows in by_type.items():
        newest = max(version_key(row.version) for row in rows)
        of_version = sorted((row for row in rows if version_key(row.version) == newest), key=lambda r: r.language.value)
        first = min(of_version, key=lambda r: r.effective_date)
        result[document_type] = CurrentVersion(
            document_type=document_type,
            version=first.version,
            effective_date=first.effective_date,
            acceptance_grace_days=max(row.acceptance_grace_days for row in of_version),
            documents=tuple(of_version),
        )
    return result


def drafts_block_activation(versions: dict[LegalDocumentType, CurrentVersion]) -> bool:
    """Q3 A: production activates only on counsel's texts (1.0 and later)."""

    if current_deployment_environment() != DeploymentEnvironment.PRODUCTION:
        return False
    return any(is_draft(current.version) for current in versions.values())


# --- acceptance -------------------------------------------------------------------------------


def accepted_versions(db: Session, dealership_id: uuid.UUID) -> dict[LegalDocumentType, set[tuple[int, ...]]]:
    rows = db.execute(
        select(LegalAcceptance.document_type, LegalAcceptance.version).where(
            LegalAcceptance.dealership_id == dealership_id
        )
    ).all()
    accepted: dict[LegalDocumentType, set[tuple[int, ...]]] = {}
    for document_type, version in rows:
        accepted.setdefault(document_type, set()).add(version_key(version))
    return accepted


def accept(
    db: Session,
    *,
    dealership: Dealership,
    document: LegalDocument,
    user_id: uuid.UUID,
    ip_address: str | None,
    user_agent: str | None,
    today: dt.date | None = None,
) -> LegalAcceptance:
    """The manager accepts `document` for the Betrieb. Only the current
    version of its type can be accepted (409 `legal_document_not_current`);
    accepting it again records nothing new and returns the first record.
    Audited. Never commits."""

    current = current_versions(db, today=today).get(document.document_type)
    if current is None or version_key(current.version) != version_key(document.version):
        raise ConflictError(
            "Only the current version of a document can be accepted.",
            details={"reason": "legal_document_not_current"},
        )
    existing = db.scalar(
        select(LegalAcceptance)
        .where(
            LegalAcceptance.dealership_id == dealership.id,
            LegalAcceptance.document_type == document.document_type,
            LegalAcceptance.version == document.version,
        )
        .order_by(LegalAcceptance.accepted_at)
        .limit(1)
    )
    if existing is not None:
        return existing
    row = LegalAcceptance(
        dealership_id=dealership.id,
        legal_document_id=document.id,
        document_type=document.document_type,
        version=document.version,
        language=document.language,
        user_id=user_id,
        ip_address=(ip_address or None) and ip_address[:45],
        user_agent=(user_agent or None) and user_agent[:512],
    )
    db.add(row)
    db.flush()
    record_audit_event(
        db,
        entity_type="legal_acceptance",
        entity_id=row.id,
        tenant_id=dealership.id,
        action="accept",
        actor_id=user_id,
        after={
            "documentType": document.document_type.value,
            "version": document.version,
            "language": document.language.value,
        },
    )
    return row


def list_acceptances(db: Session, dealership_id: uuid.UUID) -> list[LegalAcceptance]:
    return list(
        db.scalars(
            select(LegalAcceptance)
            .where(LegalAcceptance.dealership_id == dealership_id)
            .order_by(LegalAcceptance.accepted_at.desc())
        ).all()
    )


@dataclasses.dataclass(frozen=True)
class AcceptanceState:
    # The current versions the Betrieb has not accepted, by type.
    due: tuple[CurrentVersion, ...]
    # The earliest last day of grace among them; None when nothing is due.
    due_by: dt.date | None
    # The grace has passed on at least one of them, and the Betrieb is
    # active: its managers reach only the acceptance screen (Q1 A).
    held: bool
    # Every type has a current version (the Betrieb can accept at all).
    complete_set: bool

    @property
    def is_due(self) -> bool:
        return bool(self.due)


def acceptance_state(db: Session, dealership: Dealership, *, today: dt.date | None = None) -> AcceptanceState:
    today = today or swiss_today()
    versions = current_versions(db, today=today)
    accepted = accepted_versions(db, dealership.id)
    # Q3 A: production never asks for a draft (below 1.0) — neither the
    # login prompt nor the hold. A draft is accepted on staging only.
    drafts_unacceptable = current_deployment_environment() == DeploymentEnvironment.PRODUCTION
    due = tuple(
        current
        for document_type, current in sorted(versions.items(), key=lambda kv: kv[0].value)
        if version_key(current.version) not in accepted.get(document_type, set())
        and not (drafts_unacceptable and is_draft(current.version))
    )
    due_by = min((current.due_by for current in due), default=None)
    held = dealership.status == DealershipStatus.ACTIVE and due_by is not None and today > due_by
    return AcceptanceState(
        due=due,
        due_by=due_by,
        held=held,
        complete_set=all(document_type in versions for document_type in LegalDocumentType),
    )
