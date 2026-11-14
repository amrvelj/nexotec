"""Offboarding and the deletion after the retention period (KAN-218;
Dealer Administration FR-A-45/46; Anto, 2026-11-13: Q2 B, Q3 A).

`offboard` — platform staff move a Betrieb to `offboarded`, only after the
manager acknowledged an export produced after the Betrieb's last write (each
context's exporter reports its latest write). In one platform transaction:
the status, `offboarded_at`, the retention date (ten full years from the
offboarding date, for everything — the archive is kept through
`retention_until`, the deletion runs the day after, so nothing goes early),
every user of the Betrieb deactivated (A-RULE-5: never deleted) with their
sessions, delegations and connected accounts ended, every membership into it
ended, and the audit row. Operational access ends by construction: the gate
(`app/platform/session_guard.py::enforce_open_dealership`) answers 404 to the Betrieb's principals
everywhere. A former manager's copy of the archive is staff's archive export.

`run_erasure` — the daily job deletes an archive whose retention has
passed: each context's exporter erases its own rows on a session of its own
(ADR-047), platform last; `erasure_progress` names each context done, so a
run that stops resumes where it stopped; the end is one platform-level audit
row (no tenant) naming the Betrieb and the rows per context. Group-owned
records (customers, the plate register) stay while a sister Betrieb still
holds data. FR-A-46: an export is a copy, the archive the original — nothing
here touches the period lock or the gapless numbering.
"""

import dataclasses
import datetime as dt
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError
from app.core.export import exporters
from app.platform.models.dealership import Dealership, DealershipStatus
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.export_job import ExportJob, ExportJobKind
from app.platform.models.user import User, UserStatus
from app.platform.services import connected_account, text_delegation
from app.platform.services import dealership as dealership_service
from app.platform.services import user as user_service
from app.platform.services.data_export import scope_for
from app.platform.services.user_common import membership_is_active

logger = logging.getLogger(__name__)

RETENTION_YEARS = 10
OFFBOARDING_REASON = "dealership_offboarded"
ERASURE_REASON = "dealership_retention_expired"
# The order the contexts erase in: the highest layers first, so nothing a
# lower context still holds is named by a row already gone; platform last
# (its rows — the Betrieb, its users — are what the others' rows name), the
# audit log's redaction just before it.
_FIRST = ("sales", "aftersales", "finance", "reporting", "compliance", "inventory", "parts", "valuation")
_LAST = ("customer", "vehicle", "integration", "core", "platform")


def retention_end(offboarded_on: dt.date) -> dt.date:
    """The last day of the archive: ten full years from the offboarding
    date (29 February keeps until 1 March)."""

    try:
        return offboarded_on.replace(year=offboarded_on.year + RETENTION_YEARS)
    except ValueError:
        return dt.date(offboarded_on.year + RETENTION_YEARS, 3, 1)


def last_write_at(db: Session, dealership: Dealership) -> dt.datetime | None:
    scope = scope_for(db, dealership)
    stamps = [stamp for exporter in exporters() if (stamp := exporter.last_write_at(db, scope)) is not None]
    return max(stamps) if stamps else None


def latest_acknowledged_export(db: Session, dealership_id: uuid.UUID) -> ExportJob | None:
    return db.scalar(
        select(ExportJob)
        .where(
            ExportJob.dealership_id == dealership_id,
            ExportJob.kind == ExportJobKind.SELF_SERVICE,
            ExportJob.acknowledged_at.is_not(None),
            ExportJob.produced_at.is_not(None),
        )
        .order_by(ExportJob.produced_at.desc())
        .limit(1)
    )


@dataclasses.dataclass(frozen=True)
class OffboardingReadiness:
    last_write_at: dt.datetime | None
    export: ExportJob | None
    # None when offboarding may go ahead.
    refusal: str | None


def readiness(db: Session, dealership: Dealership) -> OffboardingReadiness:
    if dealership.status == DealershipStatus.OFFBOARDED:
        return OffboardingReadiness(None, None, "already_offboarded")
    export = latest_acknowledged_export(db, dealership.id)
    last = last_write_at(db, dealership)
    if export is None:
        return OffboardingReadiness(last, None, "export_not_acknowledged")
    assert export.produced_at is not None  # the query selects only produced exports
    if last is not None and last >= export.produced_at:
        return OffboardingReadiness(last, export, "export_out_of_date")
    return OffboardingReadiness(last, export, None)


def _home_users(db: Session, dealership_id: uuid.UUID) -> list[User]:
    return list(
        db.scalars(select(User).where(User.tenant_id == dealership_id).order_by(User.id).with_for_update()).all()
    )


def _assert_no_sister_loses_its_last_manager(db: Session, *, dealership_id: uuid.UUID, users: list[User]) -> None:
    """A user of the leaving Betrieb may manage a sister through a
    membership; deactivating them must not leave that sister with no
    manager (FR-A-13). Refused as a whole, never naming the sister."""

    for user in users:
        if user.status == UserStatus.DEACTIVATED:
            continue
        managed = db.scalars(
            select(DealershipMembership.dealership_id).where(
                DealershipMembership.user_id == user.id,
                DealershipMembership.is_dealer_manager.is_(True),
                DealershipMembership.dealership_id != dealership_id,
                membership_is_active(),
            )
        ).all()
        for sister_id in managed:
            try:
                user_service.assert_not_last_manager(db, dealership_id=sister_id, excluding_user_id=user.id)
            except BadRequestError:
                raise ConflictError(
                    "A user of this Betrieb is the last manager of another Betrieb — give that Betrieb "
                    "another manager first.",
                    details={"reason": "user_last_manager_of_sister"},
                ) from None


def _deactivate(db: Session, user: User, *, actor_id: uuid.UUID, now: dt.datetime) -> None:
    before = {"status": user.status.value}
    user.status = UserStatus.DEACTIVATED
    user.deactivated_at = now
    user.deactivation_reason = OFFBOARDING_REASON
    user.updated_by = actor_id
    user.version += 1
    text_delegation.end_all(db, user=user, actor_id=actor_id, reason="deactivated")
    connected_account.revoke_all(db, user=user, actor_id=actor_id, reason=connected_account.REASON_DEACTIVATED)
    user_service.end_sessions(db, user=user, reason=OFFBOARDING_REASON, actor_id=actor_id)
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="update",
        actor_id=actor_id,
        before=before,
        after={"status": UserStatus.DEACTIVATED.value},
        reason=OFFBOARDING_REASON,
    )


def _end_memberships_into(db: Session, *, dealership_id: uuid.UUID, actor_id: uuid.UUID, now: dt.datetime) -> int:
    rows = db.scalars(
        select(DealershipMembership)
        .where(DealershipMembership.dealership_id == dealership_id, membership_is_active())
        .with_for_update()
    ).all()
    for row in rows:
        row.valid_to = now
        member = db.get(User, row.user_id)
        if member is not None:
            user_service.end_sessions(db, user=member, reason=OFFBOARDING_REASON, actor_id=actor_id)
            record_audit_event(
                db,
                entity_type="user",
                entity_id=member.id,
                tenant_id=dealership_id,
                action="membership_end",
                actor_id=actor_id,
                after={"dealership_id": str(dealership_id), "valid_to": now.isoformat()},
                reason=OFFBOARDING_REASON,
            )
    return len(rows)


def offboard(db: Session, *, dealership: Dealership, actor_id: uuid.UUID, reason: str | None) -> Dealership:
    """FR-A-45 — the caller holds the dealership row locked and checked its
    version. Refused (409, a stable `details.reason`) while no acknowledged
    export produced after the last write exists."""

    state = readiness(db, dealership)
    if state.refusal == "already_offboarded":
        raise ConflictError("This Betrieb is offboarded already.", details={"reason": "already_offboarded"})
    if state.refusal == "export_not_acknowledged":
        raise ConflictError(
            "Offboarding needs a final export the manager has acknowledged.",
            details={"reason": "export_not_acknowledged"},
        )
    if state.refusal == "export_out_of_date":
        assert state.export is not None and state.last_write_at is not None
        raise ConflictError(
            "The Betrieb's data changed after its acknowledged export — the manager exports and acknowledges again.",
            details={
                "reason": "export_out_of_date",
                "producedAt": state.export.produced_at.isoformat() if state.export.produced_at else None,
                "lastWriteAt": state.last_write_at.isoformat(),
            },
        )
    users = _home_users(db, dealership.id)
    _assert_no_sister_loses_its_last_manager(db, dealership_id=dealership.id, users=users)

    now = utcnow()
    deactivated = 0
    for user in users:
        if user.status != UserStatus.DEACTIVATED:
            _deactivate(db, user, actor_id=actor_id, now=now)
            deactivated += 1
    ended = _end_memberships_into(db, dealership_id=dealership.id, actor_id=actor_id, now=now)

    before_status = dealership.status.value
    dealership.status = DealershipStatus.OFFBOARDED
    dealership.offboarded_at = now
    dealership.retention_until = retention_end(now.date())
    dealership.updated_by = actor_id
    dealership.version += 1
    record_audit_event(
        db,
        entity_type="dealership",
        entity_id=dealership.id,
        tenant_id=dealership.id,
        action="offboarded",
        actor_id=actor_id,
        before={"status": before_status},
        after={
            "status": DealershipStatus.OFFBOARDED.value,
            "offboarded_at": now.isoformat(),
            "retention_until": dealership.retention_until.isoformat(),
            "export_id": str(state.export.id) if state.export else None,
            "users_deactivated": deactivated,
            "memberships_ended": ended,
        },
        reason=reason,
    )
    db.commit()
    db.refresh(dealership)
    return dealership


# --- the deletion after the retention period ----------------------------------


def _erasure_order(contexts: list[str]) -> list[str]:
    known = set(_FIRST) | set(_LAST)
    middle = sorted(context for context in contexts if context not in known)
    ordered = [c for c in _FIRST if c in contexts] + middle + [c for c in _LAST if c in contexts]
    return ordered


def _sole_in_group(db: Session, dealership: Dealership) -> bool:
    """No other Betrieb of the group still holds data (live, or archived and
    not yet erased): only then do group-owned records go (ADR-014)."""

    group = dealership_service.list_group_dealerships(db, dealer_group_id=dealership.dealer_group_id)
    return all(other.id == dealership.id or other.erased_at is not None for other in group)


def due_for_erasure(db: Session, *, today: dt.date) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(Dealership.id)
            .where(
                Dealership.status == DealershipStatus.OFFBOARDED,
                Dealership.erased_at.is_(None),
                Dealership.retention_until < today,
            )
            .order_by(Dealership.retention_until, Dealership.id)
        ).all()
    )


def erase_dealership(db: Session, session_factory: Callable[[], Session], *, dealership_id: uuid.UUID) -> bool:
    """Runs every context's erasure not yet done for this Betrieb. Returns
    True once the Betrieb is erased; a context that raises stops the run
    (logged) and the next run resumes with it."""

    dealership = db.get(Dealership, dealership_id)
    if dealership is None or dealership.erased_at is not None:
        return True
    scope = dataclasses.replace(scope_for(db, dealership), sole_in_group=_sole_in_group(db, dealership))
    registered = {exporter.context: exporter for exporter in exporters()}
    done: list[dict[str, Any]] = list(dealership.erasure_progress or [])
    done_contexts = {entry["context"] for entry in done}
    db.rollback()  # hold no snapshot or lock while the contexts work on their own sessions
    for context in _erasure_order(sorted(registered)):
        if context in done_contexts:
            continue
        try:
            result = registered[context].erase(session_factory, scope)
        except Exception:
            db.rollback()
            logger.exception(
                "platform.dealership_erasure: %s could not erase Betrieb %s; the next run resumes there",
                context,
                dealership_id,
                extra={"dealershipId": str(dealership_id), "context": context},
            )
            return False
        locked = db.scalar(select(Dealership).where(Dealership.id == dealership_id).with_for_update())
        assert locked is not None
        done.append({"context": context, "rows": result.rows_deleted})
        locked.erasure_progress = list(done)
        db.commit()
    locked = db.scalar(select(Dealership).where(Dealership.id == dealership_id).with_for_update())
    assert locked is not None
    locked.erased_at = utcnow()
    locked.version += 1
    # The deletion's own record, at platform level (FR-A-45): no tenant, so it
    # outlives the Betrieb's own redacted trail.
    record_audit_event(
        db,
        entity_type="dealership",
        entity_id=dealership_id,
        tenant_id=None,
        action="erased",
        actor_id=None,
        after={
            "retention_until": locked.retention_until.isoformat() if locked.retention_until else None,
            "sole_in_group": scope.sole_in_group,
            "contexts": {entry["context"]: entry["rows"] for entry in done},
        },
        reason=ERASURE_REASON,
    )
    db.commit()
    return True


def run_erasure(db: Session, session_factory: Callable[[], Session], *, today: dt.date) -> list[uuid.UUID]:
    """Every Betrieb past its retention, one at a time. Returns the ones
    that could not finish (the daily job then raises, so the scheduler
    retries and alarms)."""

    unfinished = []
    for dealership_id in due_for_erasure(db, today=today):
        if not erase_dealership(db, session_factory, dealership_id=dealership_id):
            unfinished.append(dealership_id)
    return unfinished
