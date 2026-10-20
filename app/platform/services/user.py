"""User service layer: create/read/update within a Dealership tenant, plus the
audit-logging and lifecycle rules the spec calls out (role/status changes,
especially `terminated`, are audit-logged for access-deprovisioning
accountability).
"""

import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import AccessRole
from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from app.core.pagination import PageParams, build_page, paginate_query
from app.core.tenancy import get_or_404
from app.core.uuid7 import uuid7
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.user import EmploymentStatus, User, UserStatus
from app.platform.schemas.user import UserCreate, UserUpdate
from app.platform.services import auth_events, auth_sessions, text_delegation

# access_roles/is_dealer_manager are audited alongside role/status even
# though the spec text only says "role and status" — they gate
# authorization, which is exactly the "auth deprovisioning" concern the
# spec cites as the reason to audit in the first place. Flagged as an
# added-safety interpretation in the PR.
_AUDITED_FIELDS = {"role", "access_roles", "is_dealer_manager", "status", "employment_status"}
_TERMINAL_EMPLOYMENT_STATUSES = {EmploymentStatus.TERMINATED}
_TERMINAL_USER_STATUSES = {UserStatus.DEACTIVATED}
_ACTIVE_USER_STATUSES = {UserStatus.INVITED, UserStatus.ACTIVE}


def assert_user_is_not_deactivated(user: User) -> None:
    """Nothing is granted to a deactivated user and no flag of theirs changes
    (its status is terminal): a membership or delegation grant and every
    manager-flag change — the dedicated endpoints and PATCH alike — refuse
    with 409, like a status change on such a row. Ending a deactivated
    user's membership stays possible: cutting access is the safe direction."""

    if user.status in _TERMINAL_USER_STATUSES:
        raise ConflictError(
            f"User status '{user.status.value}' is terminal — this user's access can no longer be changed.",
            details={"currentStatus": user.status.value},
        )


def is_active_user(user: User) -> bool:
    return user.status in _ACTIVE_USER_STATUSES


def assert_may_touch(*, user: User, actor_roles: frozenset[AccessRole]) -> None:
    """KAN-97 for the sibling services: a dealer manager never touches a
    user who holds platform_admin (403)."""

    _assert_may_update(user=user, changes={}, actor_roles=actor_roles)


def assert_not_last_manager(
    db: Session, *, dealership_id: uuid.UUID, excluding_user_id: uuid.UUID, message: str | None = None
) -> None:
    _assert_not_last_manager(db, dealership_id=dealership_id, excluding_user_id=excluding_user_id, message=message)


def membership_is_active():
    """KAN-199: a membership counts only while it has no end date, or one
    still in the future — an ended sister-Betrieb access keeps its row."""

    now = utcnow()
    return (DealershipMembership.valid_to.is_(None)) | (DealershipMembership.valid_to > now)


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
            membership_is_active(),
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
            DealershipMembership.user_id == user.id,
            DealershipMembership.dealership_id == dealership_id,
            membership_is_active(),
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


def _assert_credential_channel_rules(*, user: User, changes: dict[str, Any], actor_roles: frozenset[AccessRole]) -> None:
    """KAN-196 (D-A-16): the status `invited` is where an account starts,
    never where it returns to — otherwise a manager could re-open the
    onboarding path (re-invite) on an active colleague. And the e-mail
    address is the sign-in and recovery channel: while the user is
    `invited` the manager corrects it (onboarding); once the account is
    active, changing it is account recovery — a platform_admin's alone."""

    if changes.get("status") == UserStatus.INVITED and user.status != UserStatus.INVITED:
        raise ConflictError(
            "An account is invited once, when it is created; it cannot return to 'invited'.",
            details={"currentStatus": user.status.value},
        )
    if (
        isinstance(changes.get("email"), str)
        and changes["email"] != user.email
        and user.status != UserStatus.INVITED
        and AccessRole.PLATFORM_ADMIN not in actor_roles
    ):
        raise ForbiddenError(
            "Changing an active user's e-mail address is account recovery, which platform staff handle."
        )


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


def _assert_platform_admin_holds_no_membership(db: Session, *, user: User, changes: dict[str, Any]) -> None:
    """KAN-199: platform_admin is Nexotec staff, never a member of a
    dealership beyond the row that homes it today (KAN-97's open debt). A
    user who holds an active sister-Betrieb membership cannot be given the
    role — 422, the body is wrong for this user — and the membership service
    refuses the mirror case."""

    roles = changes.get("access_roles")
    if roles is None or AccessRole.PLATFORM_ADMIN.value not in _role_values(roles):
        return
    if list_membership_dealership_ids(db, user_id=user.id):
        raise UnprocessableEntityError(
            "platform_admin is never a member of a dealership — end this user's memberships first."
        )


def get_user_or_404(db: Session, dealership_id: uuid.UUID, user_id: uuid.UUID) -> User:
    return get_or_404(db, User, user_id, dealership_id)


def get_user_for_update_or_404(db: Session, dealership_id: uuid.UUID, user_id: uuid.UUID) -> User:
    """The user row locked (FOR NO KEY UPDATE) for the rest of the transaction
    — for every user-administration route that writes the user's memberships,
    text delegations or the user row itself (KAN-380; the front door's
    invitation and reset routes take the same lock before their token, KAN-400). Two first grants at once both found no row
    and both inserted; the second hit the unique constraint at an unguarded
    flush, a 500. Locked, the second waits for the first, then reads its
    committed row and the user's new version, and is refused like any later
    grant (409). Every such route takes this lock before it touches a child
    row: one that locked child rows first and the user row last (at its
    version bump) would deadlock against one that did the opposite. NO KEY:
    a row that only references the user (a login's session, a token) is not
    held up. The same pattern as `get_dealership_for_update_or_404`."""

    user = db.scalar(
        select(User)
        .where(User.id == user_id, User.tenant_id == dealership_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise NotFoundError(f"User {user_id} was not found.")
    return user


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
            membership_is_active(),
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


@dataclass(frozen=True)
class UserDisplayLabel:
    label: str
    deactivated: bool


def get_user_display_labels(db: Session, user_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, UserDisplayLabel]:
    """KAN-403 — each user's `First Last` (the label another context copies
    beside a user id, CLAUDE.md rule 2) and whether the account is
    deactivated, for the nightly label refreshes. Not scoped to a tenant: it
    serves a daily job, never a request — a route resolves a label through
    get_user_or_404 in the caller's dealership. An id that names no user is
    absent from the result."""

    if not user_ids:
        return {}
    rows = db.execute(
        select(User.id, User.first_name, User.last_name, User.status).where(User.id.in_(set(user_ids)))
    )
    return {
        user_id: UserDisplayLabel(
            label=f"{first_name} {last_name}".strip(), deactivated=status in _TERMINAL_USER_STATUSES
        )
        for user_id, first_name, last_name, status in rows
    }


def create_user(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    data: UserCreate,
    actor_id: uuid.UUID,
    actor_roles: frozenset[AccessRole],
) -> User:
    _assert_may_create_with_roles(roles=data.access_roles, actor_roles=actor_roles)
    user_id = uuid7()
    user = User(
        id=user_id,
        tenant_id=dealership_id,
        first_name=data.first_name,
        last_name=data.last_name,
        # KAN-196: stored lower-cased — the login resolves the address
        # case-insensitively, and the unique index must not admit a second
        # spelling of one mailbox.
        email=data.email.lower(),
        phone=data.phone,
        role=data.role,
        access_roles=_role_values(data.access_roles),
        is_dealer_manager=data.is_dealer_manager,
        employment_status=data.employment_status,
        # KAN-196: a local-password user is resolved by e-mail; the subject
        # column stays unique and non-null for the federation seam (D-A-11).
        auth_identity_id=data.auth_identity_id or f"local:{user_id}",
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("A user with this email address already exists.", details={"email": data.email}) from exc

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


def end_sessions(db: Session, *, user: User, reason: str, actor_id: uuid.UUID) -> int:
    """KAN-196: ends every live session of the user and records it — a
    status change, a change of roles, flag or membership, platform staff's
    MFA reset. Returns how many ended."""

    ended = auth_sessions.revoke_all_for_user(db, user_id=user.id, reason=reason)
    if ended:
        auth_events.record(
            db,
            AuthEventKind.SESSION_REVOKED,
            user_id=user.id,
            dealership_id=user.tenant_id,
            actor_id=actor_id,
            details={"count": ended, "reason": reason},
        )
    return ended


def update_user(
    db: Session,
    *,
    user: User,
    data: UserUpdate,
    actor_id: uuid.UUID,
    actor_roles: frozenset[AccessRole],
    on_email_change: Callable[[User, str], None] | None = None,
) -> User:
    """`on_email_change(user, old_email)` runs inside this transaction when
    the address changes (KAN-196: the route hands in `identity.email_changed`,
    so the new address, the ended tokens and sessions and the notice to the
    old address commit together, or not at all)."""

    changes = data.model_dump(exclude_unset=True)
    if isinstance(changes.get("email"), str):
        changes["email"] = changes["email"].lower()
    _assert_may_update(user=user, changes=changes, actor_roles=actor_roles)
    _assert_platform_admin_holds_no_membership(db, user=user, changes=changes)
    _assert_credential_channel_rules(user=user, changes=changes, actor_roles=actor_roles)
    old_email = user.email

    if "employment_status" in changes and changes["employment_status"] is not None:
        new_employment_status = changes["employment_status"]
        if user.employment_status in _TERMINAL_EMPLOYMENT_STATUSES and new_employment_status != user.employment_status:
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
    if resulting_is_manager != user.is_dealer_manager and resulting_status in _TERMINAL_USER_STATUSES:
        # KAN-199: the flag never changes on a deactivated row — the row as
        # it will be after this write, like the last-manager check below —
        # same answer as the dedicated manager-flag endpoints.
        raise ConflictError(
            f"User status '{resulting_status.value}' is terminal — this user's access can no longer be changed.",
            details={"currentStatus": user.status.value},
        )
    was_active_manager = user.is_dealer_manager and user.status in _ACTIVE_USER_STATUSES
    will_be_active_manager = resulting_is_manager and resulting_status in _ACTIVE_USER_STATUSES
    if was_active_manager and not will_be_active_manager:
        _assert_not_last_manager(db, dealership_id=user.tenant_id, excluding_user_id=user.id)
    # The same rule for every sister dealership whose membership holds the
    # flag (KAN-98): deactivating the user takes them out of those too.
    if user.status in _ACTIVE_USER_STATUSES and resulting_status not in _ACTIVE_USER_STATUSES:
        for managed_dealership_id in db.scalars(
            select(DealershipMembership.dealership_id).where(
                DealershipMembership.user_id == user.id,
                DealershipMembership.is_dealer_manager.is_(True),
                membership_is_active(),
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
    if "is_dealer_manager" in after:
        # KAN-199: the manager flag is the privilege-escalation path of the
        # product; its grant or removal is its own audit row naming the
        # granter, the same action the dedicated manager-flag endpoint writes.
        record_manager_flag_event(
            db,
            user=user,
            dealership_id=user.tenant_id,
            before=before["is_dealer_manager"],
            after=after["is_dealer_manager"],
            actor_id=actor_id,
        )
    if user.email != old_email and on_email_change is not None:
        try:
            db.flush()  # a taken address fails here, as a 409 — not inside the consequences
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError(
                "A user with this email address already exists.", details={"email": changes.get("email")}
            ) from exc
        on_email_change(user, old_email)
    if "status" in after and user.status == UserStatus.DEACTIVATED:
        # FR-A-28: delegations end with deactivation — the terminal status,
        # not a suspension, which keeps them for the user's return.
        text_delegation.end_all(db, user=user, actor_id=actor_id, reason="deactivated")
    if "status" in after and user.status in (UserStatus.DEACTIVATED, UserStatus.SUSPENDED):
        # FR-AU-17 (KAN-196): a user who may no longer hold a session loses
        # every live one now, not when the cookie expires.
        end_sessions(db, user=user, reason=str(_plain(user.status)), actor_id=actor_id)
    elif "access_roles" in after or "is_dealer_manager" in after:
        # The roles and the manager flag are claims baked into the token for
        # the session's whole day (KAN-196): a change ends the sessions, so
        # the next sign-in carries the new set — the KAN-198 precedent for
        # entitlements.
        end_sessions(db, user=user, reason="access_changed", actor_id=actor_id)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "A user with this email address already exists.", details={"email": changes.get("email")}
        ) from exc
    db.refresh(user)
    return user


def record_manager_flag_event(
    db: Session,
    *,
    user: User,
    dealership_id: uuid.UUID,
    before: bool,
    after: bool,
    actor_id: uuid.UUID,
) -> None:
    """One `manager_flag_set` audit row per flag change (KAN-199, matrix row
    "Granting is_dealer_manager"): the user entity, the dealership the flag
    is held in, before/after and the granter as actor."""

    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=dealership_id,
        action="manager_flag_set",
        actor_id=actor_id,
        before={"dealership_id": str(dealership_id), "is_dealer_manager": before},
        after={"dealership_id": str(dealership_id), "is_dealer_manager": after},
    )


def list_membership_dealership_ids(db: Session, *, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """Every dealership_id a dealership_membership row grants this user,
    beyond their home tenant_id (WP-3 PR-3) — the caller adds the home
    dealership itself; see app.core.auth.create_access_token's own default.
    """

    rows = db.scalars(
        select(DealershipMembership.dealership_id).where(
            DealershipMembership.user_id == user_id, membership_is_active()
        )
    ).all()
    return frozenset(rows)
