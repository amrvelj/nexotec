"""User service layer: create/read/update within a Dealership tenant, plus the
audit-logging and lifecycle rules the spec calls out (role/status changes,
especially `terminated`, are audit-logged for access-deprovisioning
accountability).
"""

import uuid
from typing import Any

from sqlalchemy import select, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import AccessRole
from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
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


def _assert_not_last_manager(
    db: Session, *, dealership_id: uuid.UUID, excluding_user_id: uuid.UUID, message: str | None = None
) -> None:
    """Roles & Permissions enforcement rule 7 / RP-1 (Dealer Administration
    FR-A-13): a dealership must always have at least one active manager.
    Checked against every OTHER active manager of the dealership — a user
    who is themselves the last one can't demote or deactivate themselves
    out of existence, nor can another manager do it to them. A dealership's
    managers are its home users holding User.is_dealer_manager plus anyone
    whose membership of it holds the flag (KAN-98, D-A-01).
    """

    other_home_manager = db.scalar(
        select(User.id)
        .where(
            User.tenant_id == dealership_id,
            User.id != excluding_user_id,
            User.is_dealer_manager.is_(True),
            User.status.in_(_ACTIVE_USER_STATUSES),
        )
        .limit(1)
    )
    other_manager_by_membership = db.scalar(
        select(User.id)
        .join(DealershipMembership, DealershipMembership.user_id == User.id)
        .where(
            DealershipMembership.dealership_id == dealership_id,
            DealershipMembership.is_dealer_manager.is_(True),
            User.id != excluding_user_id,
            User.status.in_(_ACTIVE_USER_STATUSES),
        )
        .limit(1)
    )
    if other_home_manager is None and other_manager_by_membership is None:
        raise BadRequestError(
            message
            or "This dealership must always have at least one active manager — "
            "cannot remove or deactivate its last one."
        )


def is_dealer_manager_in(db: Session, *, user: User, dealership_id: uuid.UUID) -> bool:
    """The user's manager flag in one dealership (KAN-98, D-A-01: held per
    dealership). The home dealership reads User.is_dealer_manager; any
    other dealership reads that membership's own flag, and no membership
    means no flag. Never the home flag carried into a sister dealership.
    """

    if dealership_id == user.tenant_id:
        return user.is_dealer_manager
    flag = db.scalar(
        select(DealershipMembership.is_dealer_manager).where(
            DealershipMembership.user_id == user.id, DealershipMembership.dealership_id == dealership_id
        )
    )
    return bool(flag)


def _assert_may_create_with_roles(*, roles: list[AccessRole], actor_roles: frozenset[AccessRole]) -> None:
    """KAN-97 (Dealer Administration: "`platform_admin` is Nexotec staff
    only ... no dealer user can obtain it"): a dealer manager passes
    require_write("dealership_users"), so this service — not the route — is
    what stops them minting a platform_admin. 403, not 422: the body is
    valid; who sent it is not allowed to.
    """

    if AccessRole.PLATFORM_ADMIN in actor_roles:
        return
    if AccessRole.PLATFORM_ADMIN.value in _role_values(roles):
        raise ForbiddenError("Only platform staff can grant the platform_admin role.")


def _assert_may_update(*, user: User, changes: dict[str, Any], actor_roles: frozenset[AccessRole]) -> None:
    """KAN-97: a non-platform_admin may neither grant platform_admin nor
    touch a user who already holds it — any field, not only the roles:
    rewriting a staff account's email or auth_identity_id is an account
    takeover, and deactivating it locks Nexotec staff out (Anto,
    2026-10-04, option 1).
    """

    if AccessRole.PLATFORM_ADMIN in actor_roles:
        return
    if AccessRole.PLATFORM_ADMIN.value in user.access_roles:
        raise ForbiddenError("Only platform staff can change a user who holds the platform_admin role.")
    if changes.get("access_roles") is not None:
        _assert_may_create_with_roles(roles=changes["access_roles"], actor_roles=actor_roles)


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

    home_managers = select(User.email).where(
        User.tenant_id == dealership_id, User.is_dealer_manager.is_(True), User.status == UserStatus.ACTIVE
    )
    # Managers by membership (KAN-98, D-A-01) are this dealership's
    # managers too, so its warnings reach them as well.
    managers_by_membership = (
        select(User.email)
        .join(DealershipMembership, DealershipMembership.user_id == User.id)
        .where(
            DealershipMembership.dealership_id == dealership_id,
            DealershipMembership.is_dealer_manager.is_(True),
            User.status == UserStatus.ACTIVE,
        )
    )
    return list(db.scalars(union(home_managers, managers_by_membership)).all())


def list_active_users(db: Session, *, dealership_id: uuid.UUID) -> list[User]:
    """Active users of a dealership, ordered by name — for a picker such as
    the customer record's advisor field (KAN-50). Deliberately narrower
    than list_users (no pagination, no role filter, active only): a picker
    wants the short, current list, and it is reachable through
    platform.public without the manager-only `dealership_users` read
    capability list_users sits behind.
    """

    stmt = (
        select(User)
        .where(User.tenant_id == dealership_id, User.status.in_(tuple(_ACTIVE_USER_STATUSES)))
        .order_by(User.last_name, User.first_name)
    )
    return list(db.scalars(stmt).all())


def create_user(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    data: UserCreate,
    actor_id: uuid.UUID,
    actor_roles: frozenset[AccessRole],
) -> User:
    _assert_may_create_with_roles(roles=data.access_roles, actor_roles=actor_roles)
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


def update_user(
    db: Session, *, user: User, data: UserUpdate, actor_id: uuid.UUID, actor_roles: frozenset[AccessRole]
) -> User:
    changes = data.model_dump(exclude_unset=True)
    _assert_may_update(user=user, changes=changes, actor_roles=actor_roles)

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
        _assert_not_last_manager(db, dealership_id=user.tenant_id, excluding_user_id=user.id)
    # The same rule for every sister dealership whose membership holds the
    # flag (KAN-98): deactivating the user takes them out of those too.
    if user.status in _ACTIVE_USER_STATUSES and resulting_status not in _ACTIVE_USER_STATUSES:
        for managed_dealership_id in db.scalars(
            select(DealershipMembership.dealership_id).where(
                DealershipMembership.user_id == user.id, DealershipMembership.is_dealer_manager.is_(True)
            )
        ).all():
            # No dealership id in the error: the caller administers the
            # user's home dealership and need not learn the sister's.
            _assert_not_last_manager(
                db,
                dealership_id=managed_dealership_id,
                excluding_user_id=user.id,
                message="This user is the last active manager of another dealership they are a member of — "
                "cannot deactivate them until that dealership has another manager.",
            )

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
