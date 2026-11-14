"""Offboarding (KAN-218; Dealer Administration FR-A-45/46; Anto, 2026-11-13:
Q2 B, Q3 A; 2026-11-14: Q2 A, Q3 A, no automatic deletion).

`offboard` — platform staff move a Betrieb to `offboarded`, only after the
manager acknowledged an export produced after the Betrieb's last write (each
context's exporter reports its latest write). In one platform transaction:
the status, `offboarded_at`, the retention date (ten full years from the
offboarding date, for everything — the archive is kept through
`retention_until`), every user of the Betrieb deactivated (A-RULE-5: never
deleted) with their sessions, delegations and connected accounts ended, every
membership into it ended, the audit row, and the fact
`platform.dealership.offboarded` in the outbox. Operational access ends by
construction: the gate (`app/platform/session_guard.py::enforce_open_dealership`)
answers 404 to the Betrieb's principals everywhere, and sister Betriebe no
longer see it in their group reads (`services/group_read_dealerships.py`). A
former manager's copy of the archive is staff's archive export.

The fact is how the higher contexts end what the Betrieb still has live
(Anto, 2026-11-14, Q3 A): Stock withdraws every live marketplace listing and
Integration disables every connection (those Stock publishes through only on
Stock's own fact that it has withdrawn), each in its own consumer committing
its own work (rule 12, ADR-047) — platform is the lowest layer and calls
neither (ADR-131). Staging runs no worker (ADR-081): there the listings and
connections stay until a worker runs.

Nothing deletes automatically (Anto, 2026-11-14). Once `retention_until` has
passed, platform's nightly reconciliation reports the Betrieb as due for
manual deletion (`app/platform/reconciliation.py`); the deletion is a staff
step in an attended session. FR-A-46: an export is a copy, the archive the
original — nothing here touches the period lock or the gapless numbering.
"""

import dataclasses
import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError
from app.core.export import exporters
from app.core.outbox import OutboxEvent, publish
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.export_job import ExportJob, ExportJobKind
from app.platform.models.user import User, UserStatus
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.services import connected_account, text_delegation
from app.platform.services import user as user_service
from app.platform.services.data_export import scope_for
from app.platform.services.user_common import membership_is_active

RETENTION_YEARS = 10
OFFBOARDING_REASON = "dealership_offboarded"
# The fact the higher contexts end the Betrieb's live listings and
# connections from (Q3 A). Payload: dealershipId, offboardedAt.
OFFBOARDED_EVENT_TYPE = "platform.dealership.offboarded"


def retention_end(offboarded_on: dt.date) -> dt.date:
    """The last day of the archive: ten full years from the offboarding
    date (29 February keeps until 1 March)."""

    try:
        return offboarded_on.replace(year=offboarded_on.year + RETENTION_YEARS)
    except ValueError:
        return dt.date(offboarded_on.year + RETENTION_YEARS, 3, 1)


def retention_until_for(offboarded_at: dt.datetime) -> dt.date:
    """`retention_end` of the Swiss calendar day of `offboarded_at` — the day
    the nightly reconciliation judges by (`platform/reconciliation.py`), so
    an offboarding just after Swiss midnight is not dated a day early."""

    return retention_end(offboarded_at.astimezone(ZoneInfo(get_settings().session_timezone)).date())


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


def publish_offboarded(db: Session, *, dealership: Dealership, offboarded_at: dt.datetime) -> None:
    """The fact `platform.dealership.offboarded` in the caller's transaction
    (the offboarding's own): Stock withdraws the Betrieb's live listings and
    Integration disables its connections from it (Q3 A)."""

    publish(
        db,
        OutboxEvent(
            event_type=OFFBOARDED_EVENT_TYPE,
            tenant_id=dealership.id,
            producer="platform",
            aggregate_type="dealership",
            aggregate_id=dealership.id,
            payload={"dealershipId": str(dealership.id), "offboardedAt": offboarded_at.isoformat()},
            occurred_at=offboarded_at,
        ),
    )


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
    dealership.retention_until = retention_until_for(now)
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
    publish_offboarded(db, dealership=dealership, offboarded_at=now)
    db.commit()
    db.refresh(dealership)
    return dealership
