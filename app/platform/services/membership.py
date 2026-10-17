"""Dealership memberships and the manager flag (KAN-199, D-A-01, FR-A-13).

A membership lets a user switch their active dealership to a sister Betrieb
(WP-3); its `is_dealer_manager` is the manager flag held in THAT dealership.
Granting the flag is the privilege-escalation path of the product, so every
write here is audit-logged with before/after and the granter, on the user's
home tenant and mirrored into the target dealership's tenant so its manager
sees it in their own audit log.

Who may write (constraints of the ticket): platform staff, or a manager of
the home dealership who is ALSO a manager in the target — a manager who
holds the flag on one side only gets 404 for the target, never a hint that
it exists. The target is never the home dealership (422), never a user who
holds platform_admin (422: platform staff are never members of a
dealership).

A removed access keeps its row with `valid_to` (Anto, 2026-10-17); a
re-grant reopens it. The last-active-manager rule (FR-A-13) counts active
memberships only and refuses the flag's removal or the membership's end
when nobody else manages the target.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import AccessRole
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.platform.models.dealership import Dealership
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.user import User
from app.platform.schemas.membership import MembershipCreate, MembershipRead
from app.platform.services import user as user_service

_ACTOR_HOLDS_NO_FLAG = "Dealership {dealership_id} was not found."


def read_membership(db: Session, row: DealershipMembership, *, label: str | None = None) -> MembershipRead:
    if label is None:
        dealership = db.get(Dealership, row.dealership_id)
        label = dealership.legal_name if dealership is not None else ""
    return MembershipRead(
        dealership_id=row.dealership_id,
        dealership_label=label,
        is_dealer_manager=row.is_dealer_manager,
        granted_at=row.granted_at,
        granted_by=row.granted_by,
        valid_to=row.valid_to,
        active=row.is_active(utcnow()),
    )


def list_memberships(
    db: Session, *, user: User, actor_id: uuid.UUID, actor_roles: frozenset[AccessRole]
) -> list[MembershipRead]:
    """Platform staff see every membership the user ever held. A dealer
    manager sees only the sister dealerships they manage themselves — a
    manager who holds the flag on one side only learns nothing about the
    other, not even its name (ticket constraint: 404, never a hint)."""

    rows: list[tuple[DealershipMembership, str]] = [
        (row, label)
        for row, label in db.execute(
            select(DealershipMembership, Dealership.legal_name)
            .join(Dealership, Dealership.id == DealershipMembership.dealership_id)
            .where(DealershipMembership.user_id == user.id)
            .order_by(DealershipMembership.granted_at, DealershipMembership.id)
        ).all()
    ]
    if AccessRole.PLATFORM_ADMIN not in actor_roles:
        actor = db.get(User, actor_id)
        rows = [
            (row, label)
            for row, label in rows
            if actor is not None and user_service.is_dealer_manager_in(db, user=actor, dealership_id=row.dealership_id)
        ]
    return [read_membership(db, row, label=label) for row, label in rows]


def _active_row(db: Session, *, user: User, dealership_id: uuid.UUID) -> DealershipMembership | None:
    row = db.scalar(
        select(DealershipMembership).where(
            DealershipMembership.user_id == user.id, DealershipMembership.dealership_id == dealership_id
        )
    )
    if row is None or not row.is_active(utcnow()):
        return None
    return row


def _assert_actor_manages_target(
    db: Session, *, actor_id: uuid.UUID, actor_roles: frozenset[AccessRole], target_dealership_id: uuid.UUID
) -> None:
    """Platform staff pass. A dealer manager must hold the flag in the
    target too; otherwise the target is 404 — the caller learns nothing
    about a dealership they do not manage (tenant rule 7)."""

    if AccessRole.PLATFORM_ADMIN in actor_roles:
        return
    actor = db.get(User, actor_id)
    if actor is None or not user_service.is_dealer_manager_in(db, user=actor, dealership_id=target_dealership_id):
        raise NotFoundError(_ACTOR_HOLDS_NO_FLAG.format(dealership_id=target_dealership_id))


def _audit_both_sides(
    db: Session,
    *,
    user: User,
    target_dealership_id: uuid.UUID,
    action: str,
    actor_id: uuid.UUID,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    for tenant_id in (user.tenant_id, target_dealership_id):
        record_audit_event(
            db,
            entity_type="user",
            entity_id=user.id,
            tenant_id=tenant_id,
            action=action,
            actor_id=actor_id,
            before=before,
            after=after,
        )


def _membership_state(row: DealershipMembership) -> dict[str, Any]:
    return {
        "dealership_id": str(row.dealership_id),
        "is_dealer_manager": row.is_dealer_manager,
        "active": row.is_active(utcnow()),
    }


def _touch(user: User, actor_id: uuid.UUID) -> None:
    user.updated_by = actor_id
    user.version += 1


def grant_membership(
    db: Session, *, user: User, data: MembershipCreate, actor_id: uuid.UUID, actor_roles: frozenset[AccessRole]
) -> DealershipMembership:
    if data.dealership_id == user.tenant_id:
        raise UnprocessableEntityError("The home dealership is always a membership — grant a sister dealership.")
    if AccessRole.PLATFORM_ADMIN.value in user.access_roles:
        raise UnprocessableEntityError("platform_admin is never a member of a dealership.")
    user_service.assert_user_is_not_deactivated(user)
    _assert_actor_manages_target(
        db, actor_id=actor_id, actor_roles=actor_roles, target_dealership_id=data.dealership_id
    )
    if db.get(Dealership, data.dealership_id) is None:
        raise NotFoundError(_ACTOR_HOLDS_NO_FLAG.format(dealership_id=data.dealership_id))

    now = utcnow()
    row = db.scalar(
        select(DealershipMembership).where(
            DealershipMembership.user_id == user.id, DealershipMembership.dealership_id == data.dealership_id
        )
    )
    if row is not None and row.is_active(now):
        raise ConflictError(
            "This user already holds a membership of that dealership.",
            details={"dealershipId": str(data.dealership_id)},
        )
    before = _membership_state(row) if row is not None else None
    if row is None:
        row = DealershipMembership(user_id=user.id, dealership_id=data.dealership_id)
        db.add(row)
    row.granted_at = now
    row.granted_by = actor_id
    row.valid_to = None
    row.is_dealer_manager = data.is_dealer_manager
    db.flush()
    _audit_both_sides(
        db,
        user=user,
        target_dealership_id=data.dealership_id,
        action="membership_grant",
        actor_id=actor_id,
        before=before,
        after=_membership_state(row),
    )
    if data.is_dealer_manager:
        user_service.record_manager_flag_event(
            db, user=user, dealership_id=data.dealership_id, before=False, after=True, actor_id=actor_id
        )
    db.commit()
    db.refresh(row)
    return row


def end_membership(
    db: Session, *, user: User, dealership_id: uuid.UUID, actor_id: uuid.UUID, actor_roles: frozenset[AccessRole]
) -> None:
    _assert_actor_manages_target(db, actor_id=actor_id, actor_roles=actor_roles, target_dealership_id=dealership_id)
    user_service.assert_may_touch(user=user, actor_roles=actor_roles)  # KAN-97: never a staff account
    row = _active_row(db, user=user, dealership_id=dealership_id)
    if row is None:
        raise NotFoundError(f"User {user.id} holds no membership of dealership {dealership_id}.")
    if row.is_dealer_manager and user_service.is_active_user(user):
        user_service.assert_not_last_manager(
            db,
            dealership_id=dealership_id,
            excluding_user_id=user.id,
            message="This user is the last active manager of that dealership — "
            "cannot end their membership until it has another manager.",
        )
    before = _membership_state(row)
    row.valid_to = utcnow()
    db.flush()
    _audit_both_sides(
        db,
        user=user,
        target_dealership_id=dealership_id,
        action="membership_end",
        actor_id=actor_id,
        before=before,
        after=_membership_state(row),
    )
    _touch(user, actor_id)
    db.commit()


def set_membership_manager_flag(
    db: Session,
    *,
    user: User,
    dealership_id: uuid.UUID,
    is_dealer_manager: bool,
    actor_id: uuid.UUID,
    actor_roles: frozenset[AccessRole],
) -> DealershipMembership:
    _assert_actor_manages_target(db, actor_id=actor_id, actor_roles=actor_roles, target_dealership_id=dealership_id)
    user_service.assert_may_touch(user=user, actor_roles=actor_roles)  # KAN-97: never a staff account
    row = _active_row(db, user=user, dealership_id=dealership_id)
    if row is None:
        raise NotFoundError(f"User {user.id} holds no membership of dealership {dealership_id}.")
    if row.is_dealer_manager == is_dealer_manager:
        return row
    user_service.assert_user_is_not_deactivated(user)
    if not is_dealer_manager and user_service.is_active_user(user):
        user_service.assert_not_last_manager(db, dealership_id=dealership_id, excluding_user_id=user.id)
    row.is_dealer_manager = is_dealer_manager
    db.flush()
    user_service.record_manager_flag_event(
        db,
        user=user,
        dealership_id=dealership_id,
        before=not is_dealer_manager,
        after=is_dealer_manager,
        actor_id=actor_id,
    )
    _touch(user, actor_id)
    db.commit()
    db.refresh(row)
    return row


def set_home_manager_flag(
    db: Session, *, user: User, is_dealer_manager: bool, actor_id: uuid.UUID, actor_roles: frozenset[AccessRole]
) -> User:
    """The flag on the user row — the home dealership's (D-A-01). Same guards
    as PATCH isDealerManager (KAN-97: a dealer manager cannot touch a staff
    account; FR-A-13: never the last active manager; frozen on a deactivated
    row), same audit rows."""

    user_service.assert_may_touch(user=user, actor_roles=actor_roles)
    if user.is_dealer_manager == is_dealer_manager:
        return user
    user_service.assert_user_is_not_deactivated(user)
    if not is_dealer_manager and user_service.is_active_user(user):
        user_service.assert_not_last_manager(db, dealership_id=user.tenant_id, excluding_user_id=user.id)
    before = user.is_dealer_manager
    user.is_dealer_manager = is_dealer_manager
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action="update",
        actor_id=actor_id,
        before={"is_dealer_manager": before},
        after={"is_dealer_manager": is_dealer_manager},
    )
    user_service.record_manager_flag_event(
        db, user=user, dealership_id=user.tenant_id, before=before, after=is_dealer_manager, actor_id=actor_id
    )
    _touch(user, actor_id)
    db.commit()
    db.refresh(user)
    return user
