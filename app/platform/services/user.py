"""User service layer: create/read/update within a Dealership tenant, plus the
audit-logging and lifecycle rules the spec calls out (role/status changes,
especially `terminated`, are audit-logged for access-deprovisioning
accountability).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.pagination import PageParams, build_page, paginate_query
from app.core.tenancy import get_or_404
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.user import EmploymentStatus, User, UserStatus
from app.platform.schemas.user import UserCreate, UserUpdate

# access_roles/is_dealer_manager are audited alongside role/status even
# though the spec text only says "role and status" — they gate
# authorization, which is exactly the "auth deprovisioning" concern the
# spec cites as the reason to audit in the first place. Flagged as an
# added-safety interpretation in the PR.
_AUDITED_FIELDS = {"role", "access_roles", "is_dealer_manager", "status", "employment_status"}
_TERMINAL_EMPLOYMENT_STATUSES = {EmploymentStatus.TERMINATED}
_TERMINAL_USER_STATUSES = {UserStatus.DEACTIVATED}
_ACTIVE_USER_STATUSES = {UserStatus.INVITED, UserStatus.ACTIVE}


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _role_values(roles) -> list[str]:
    """AccessRole members (or already-plain strings) -> a stable-sorted
    list of their string values. Sorted so two requests for the same role
    set never register as a "change" just because the client sent them in
    a different order (access_roles is a set, stored as JSON — SQL list
    equality would otherwise be order-sensitive where the domain isn't).
    """

    return sorted(_plain(role) for role in roles)


def _assert_not_last_manager(db: Session, *, tenant_id: uuid.UUID, excluding_user_id: uuid.UUID) -> None:
    """Roles & Permissions enforcement rule 7 / RP-1: a dealership must
    always have at least one active manager. Checked against every OTHER
    active manager in the tenant — a user who is themselves the last one
    can't demote or deactivate themselves out of existence, nor can another
    manager do it to them.
    """

    other_active_managers = db.scalar(
        select(User.id)
        .where(
            User.tenant_id == tenant_id,
            User.id != excluding_user_id,
            User.is_dealer_manager.is_(True),
            User.status.in_(_ACTIVE_USER_STATUSES),
        )
        .limit(1)
    )
    if other_active_managers is None:
        raise BadRequestError(
            "This dealership must always have at least one active manager — "
            "cannot remove or deactivate its last one."
        )


def get_user_or_404(db: Session, dealership_id: uuid.UUID, user_id: uuid.UUID) -> User:
    return get_or_404(db, User, user_id, dealership_id)


def get_own_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    """Dealership-agnostic — for a principal fetching THEIR OWN row by the
    user_id a signed token's `sub` claim already vouches for (WP-3 PR-3:
    /auth/me and switch-dealership need this because the caller's *active*
    dealership, principal.tenant_id, may no longer equal User.tenant_id —
    their fixed home — once they've switched away from it). Safe precisely
    because it's always "give me myself," never an open cross-tenant lookup.
    """

    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} was not found.")
    return user


def list_users(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    role: str | None,
    status: UserStatus | None,
    params: PageParams,
) -> tuple[list[User], str | None]:
    stmt = select(User).where(User.tenant_id == dealership_id)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if status is not None:
        stmt = stmt.where(User.status == status)
    stmt = paginate_query(stmt, model=User, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def list_dealer_manager_emails(db: Session, *, dealership_id: uuid.UUID) -> list[str]:
    """WP-6 PR-6 — who ADR-025's expiry warnings and break-glass-access
    notifications actually go to. Active managers only: a suspended or
    deactivated manager's inbox is not where a live operational warning
    should land, even if their `is_dealer_manager` flag was never
    cleared.
    """

    stmt = select(User.email).where(
        User.tenant_id == dealership_id, User.is_dealer_manager.is_(True), User.status == UserStatus.ACTIVE
    )
    return list(db.scalars(stmt).all())


def create_user(db: Session, *, dealership_id: uuid.UUID, data: UserCreate, actor_id: uuid.UUID) -> User:
    user = User(
        tenant_id=dealership_id,
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        phone=data.phone,
        role=data.role,
        access_roles=_role_values(data.access_roles),
        is_dealer_manager=data.is_dealer_manager,
        employment_status=data.employment_status,
        auth_identity_id=data.auth_identity_id,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "A user with this email address already exists.", details={"email": data.email}
        ) from exc

    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after={
            "role": _plain(user.role),
            "access_roles": user.access_roles,
            "is_dealer_manager": user.is_dealer_manager,
            "status": _plain(user.status),
            "employment_status": _plain(user.employment_status),
        },
    )
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, *, user: User, data: UserUpdate, actor_id: uuid.UUID) -> User:
    changes = data.model_dump(exclude_unset=True)

    if "employment_status" in changes and changes["employment_status"] is not None:
        new_employment_status = changes["employment_status"]
        if (
            user.employment_status in _TERMINAL_EMPLOYMENT_STATUSES
            and new_employment_status != user.employment_status
        ):
            raise ConflictError(
                f"Employment status '{user.employment_status.value}' is terminal and cannot be changed.",
                details={"currentEmploymentStatus": user.employment_status.value},
            )

    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        if user.status in _TERMINAL_USER_STATUSES and new_status != user.status:
            raise ConflictError(
                f"User status '{user.status.value}' is terminal and cannot be changed.",
                details={"currentStatus": user.status.value},
            )

    # Terminating employment revokes access — spec: "access is revoked, not
    # the record deleted." Assumption, flagged for PM/CTO sign-off: this
    # auto-transition isn't spelled out explicitly in the spec text, only
    # implied by that sentence plus the audit requirement.
    if changes.get("employment_status") == EmploymentStatus.TERMINATED:
        changes.setdefault("status", UserStatus.DEACTIVATED)

    # Roles & Permissions rule 7 / RP-1: a dealership must always have at
    # least one active manager. Checked against the FINAL resulting state
    # (after the termination auto-transition above), not just the raw
    # request body, so demoting a manager via employment_status alone can't
    # slip past this the way it could slip past a check on `changes` only.
    resulting_is_manager = changes.get("is_dealer_manager", user.is_dealer_manager)
    resulting_status = changes.get("status", user.status)
    was_active_manager = user.is_dealer_manager and user.status in _ACTIVE_USER_STATUSES
    will_be_active_manager = resulting_is_manager and resulting_status in _ACTIVE_USER_STATUSES
    if was_active_manager and not will_be_active_manager:
        _assert_not_last_manager(db, tenant_id=user.tenant_id, excluding_user_id=user.id)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        if field == "access_roles":
            value = _role_values(value)
        current = getattr(user, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _plain(current)
            after[field] = _plain(value)
        setattr(user, field, value)

    user.updated_by = actor_id
    user.version += 1

    if before or after:
        record_audit_event(
            db,
            entity_type="user",
            entity_id=user.id,
            tenant_id=user.tenant_id,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "A user with this email address already exists.", details={"email": changes.get("email")}
        ) from exc
    db.refresh(user)
    return user


def list_membership_dealership_ids(db: Session, *, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """Every dealership_id a dealership_membership row grants this user,
    beyond their home tenant_id (WP-3 PR-3) — the caller adds the home
    dealership itself; see app.core.auth.create_access_token's own default.
    """

    rows = db.scalars(
        select(DealershipMembership.dealership_id).where(DealershipMembership.user_id == user_id)
    ).all()
    return frozenset(rows)
