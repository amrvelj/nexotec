"""Users allocated to labour-rate categories (KAN-806; D-A-19: «technicians
are allocated to one or more categories, so a job line proposes the right
rate from who did the work»).

A user who may act in the Betrieb — homed there or an active member, active
or invited (`get_active_user_acting_in`) — can be allocated to any of its
categories, to several at once. An allocation exists or it does not: it is
added and removed, never edited. It is kept when the user is deactivated or
the category is deactivated (reads mark the member inactive), so a
reactivation brings it back.

Every write locks the user row first (FOR NO KEY UPDATE, the KAN-380/411
lock order of `platform-users.md`), then the category; the second of two
equal allocations at once waits, reads the first and is refused (409), never
a 500. The caller checks the role and the Betrieb and commits; every write is
audited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.text_collation import TextSortKey
from app.platform.identity.models.user import User
from app.platform.identity.models.user_labour_category import UserLabourCategory
from app.platform.identity.services.user_reads import get_active_user_acting_in, list_active_users_acting_in
from app.platform.organisation.models.labour_rate import LabourRateCategory
from app.platform.organisation.services.labour_rates import get_category_or_404


@dataclass(frozen=True)
class LabourCategoryMember:
    user_id: uuid.UUID
    display_name: str
    # False: the user can no longer act in the Betrieb (deactivated, suspended,
    # membership ended); the allocation is kept.
    is_active: bool


def _lock_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.scalar(
        select(User).where(User.id == user_id).with_for_update(key_share=True).execution_options(populate_existing=True)
    )


def allocate_user(
    db: Session, *, dealership_id: uuid.UUID, category_id: uuid.UUID, user_id: uuid.UUID, actor_id: uuid.UUID | None
) -> UserLabourCategory:
    if (
        _lock_user(db, user_id) is None
        or get_active_user_acting_in(db, dealership_id=dealership_id, user_id=user_id) is None
    ):
        raise UnprocessableEntityError(
            "Only a user who works in this business can be allocated to its labour category.",
            details={"reason": "labour_category_user_cannot_act_here"},
        )
    get_category_or_404(db, dealership_id=dealership_id, category_id=category_id, lock=True)
    existing = db.scalar(
        select(UserLabourCategory.id).where(
            UserLabourCategory.user_id == user_id, UserLabourCategory.category_id == category_id
        )
    )
    if existing is not None:
        raise ConflictError(
            "The user is already in this labour category.", details={"reason": "labour_category_member_exists"}
        )
    row = UserLabourCategory(
        dealership_id=dealership_id, user_id=user_id, category_id=category_id, created_by=actor_id, updated_by=actor_id
    )
    db.add(row)
    db.flush()
    record_audit_event(
        db,
        entity_type="user_labour_category",
        entity_id=row.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after={"userId": str(user_id), "categoryId": str(category_id)},
    )
    return row


def remove_user(
    db: Session, *, dealership_id: uuid.UUID, category_id: uuid.UUID, user_id: uuid.UUID, actor_id: uuid.UUID | None
) -> None:
    _lock_user(db, user_id)
    get_category_or_404(db, dealership_id=dealership_id, category_id=category_id)
    row = db.scalar(
        select(UserLabourCategory).where(
            UserLabourCategory.dealership_id == dealership_id,
            UserLabourCategory.user_id == user_id,
            UserLabourCategory.category_id == category_id,
        )
    )
    if row is None:
        raise NotFoundError("The user is not in this labour category.")
    record_audit_event(
        db,
        entity_type="user_labour_category",
        entity_id=row.id,
        tenant_id=dealership_id,
        action="delete",
        actor_id=actor_id,
        before={"userId": str(user_id), "categoryId": str(category_id)},
    )
    db.delete(row)
    db.flush()


def members_of(
    db: Session, *, dealership_id: uuid.UUID, category_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[LabourCategoryMember]]:
    """The users of each category, by name; `is_active` says whether each can
    still act in the Betrieb."""

    result: dict[uuid.UUID, list[LabourCategoryMember]] = {category_id: [] for category_id in category_ids}
    if not category_ids:
        return result
    rows = db.execute(
        select(UserLabourCategory.category_id, User)
        .join(User, User.id == UserLabourCategory.user_id)
        .where(UserLabourCategory.dealership_id == dealership_id, UserLabourCategory.category_id.in_(category_ids))
        .order_by(TextSortKey(User.last_name), TextSortKey(User.first_name), User.id)
    )
    acting_ids = {user.id for user in list_active_users_acting_in(db, dealership_id=dealership_id)}
    for category_id, user in rows:
        acting = user.id in acting_ids
        result[category_id].append(
            LabourCategoryMember(
                user_id=user.id, display_name=f"{user.first_name} {user.last_name}".strip(), is_active=acting
            )
        )
    return result


def labour_categories_of_user(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    """The active categories a user is allocated to in this Betrieb — for the
    proposal «the right rate from who did the work» (D-A-19), KAN-810."""

    return tuple(
        db.scalars(
            select(UserLabourCategory.category_id)
            .join(LabourRateCategory, LabourRateCategory.id == UserLabourCategory.category_id)
            .where(
                UserLabourCategory.dealership_id == dealership_id,
                UserLabourCategory.user_id == user_id,
                LabourRateCategory.is_active.is_(True),
            )
            .order_by(TextSortKey(LabourRateCategory.name), LabourRateCategory.id)
        )
    )
