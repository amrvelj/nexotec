"""User reads, split from `user.py` (KAN-669, a pure move): the row lookups
(`get_user_for_update_or_404` is the user-row lock every user-administration
write takes first), the listings, where a user may act (home Betrieb and
active memberships), display labels and the `UserAdminRead` projection.

The lifecycle rules and the writes (`create_user`, `update_user`) stay in
`user.py`, which imports this module; this one imports only `user_common.py`.
"""

import datetime as dt
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from sqlalchemy import or_, select, union
from sqlalchemy.orm import Session

from app.core.auth import AccessRole
from app.core.errors import NotFoundError
from app.core.pagination import SortPageParams, build_sorted_page, paginate_query_sorted
from app.core.tenancy import get_or_404
from app.platform.identity.models.auth_token import AuthToken, AuthTokenKind
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.identity.models.user import User, UserStatus
from app.platform.identity.schemas.user import UserAdminRead
from app.platform.identity.services import user_location
from app.platform.identity.services.user_common import (
    _ACTIVE_USER_STATUSES,
    _TERMINAL_USER_STATUSES,
    is_active_user,
    membership_is_active,
)
from app.platform.organisation.models.dealership import Dealership


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
    a row that only references the user (a session, a token) is not held up —
    except while an e-mail change holds it (an update of a unique column takes
    the full lock), which is why every writer of such a row takes this user
    row first (KAN-411) and waits there instead;
    a login itself takes this lock before it writes the credential and
    `last_login_at` (`identity.lock_user_for_login`, KAN-200). The same pattern as `get_dealership_for_update_or_404`."""

    user = db.scalar(
        select(User)
        .where(User.id == user_id, User.tenant_id == dealership_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise NotFoundError(f"User {user_id} was not found.")
    return user


def get_own_user_for_update_or_404(db: Session, user_id: uuid.UUID) -> User:
    """The caller's own row, locked FOR NO KEY UPDATE and re-read — My
    account's writes (KAN-201) take the user row first like every other
    writer of it (KAN-380). Dealership-agnostic for the reason
    `get_own_user_or_404` gives. The account's security routes take it too,
    before they write a row the user owns — a second factor, a recovery
    code, a passkey, a passkey-registration token or the user's sessions
    (KAN-411) — as the login (`identity.lock_user_for_login`) and every
    user-administration route (`get_user_for_update_or_404`) do: two
    requests for one user run one after the other instead of deadlocking,
    whatever order each then writes its child rows in."""

    db.flush()
    user = db.scalar(
        select(User).where(User.id == user_id).with_for_update(key_share=True).execution_options(populate_existing=True)
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
    params: SortPageParams,
    q: str | None = None,
) -> tuple[list[User], str | None]:
    """The Users screen's list (KAN-200): filtered by role, status and the
    search box, sorted server-side by the route's allow-list (UI/UX Core
    Principles: every column sortable), cursor-paginated."""

    stmt = select(User).where(User.tenant_id == dealership_id)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if status is not None:
        stmt = stmt.where(User.status == status)
    if q:
        # KAN-200: the Users screen's search box — name or address.
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.first_name.ilike(like), User.last_name.ilike(like), User.email.ilike(like)))
    stmt = paginate_query_sorted(stmt, model=User, params=params)
    rows = list(db.scalars(stmt).all())
    return build_sorted_page(rows, params)


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


def list_dealer_manager_ids(db: Session, *, dealership_id: uuid.UUID) -> list[uuid.UUID]:
    """KAN-222 — who the `administration` notifications of a Betrieb go to
    (FR-A-22): its active managers, homed there or by an active membership,
    as `list_dealer_manager_emails` counts them. Ordered for a stable fan-out."""

    home_managers = select(User.id).where(
        User.tenant_id == dealership_id, User.is_dealer_manager.is_(True), User.status == UserStatus.ACTIVE
    )
    managers_by_membership = (
        select(User.id)
        .join(DealershipMembership, DealershipMembership.user_id == User.id)
        .where(
            DealershipMembership.dealership_id == dealership_id,
            DealershipMembership.is_dealer_manager.is_(True),
            membership_is_active(),
            User.status == UserStatus.ACTIVE,
        )
    )
    return sorted(set(db.scalars(union(home_managers, managers_by_membership)).all()))


def list_active_users_acting_in(db: Session, *, dealership_id: uuid.UUID) -> list[User]:
    """The users `get_active_user_acting_in` admits for a dealership — homed
    there or an active member, active or invited — ordered by name, for a
    picker such as the customer record's advisor field (KAN-50; members
    since KAN-410). Deliberately narrower than list_users (no pagination, no
    role filter): a picker wants the short, current list, and it is reachable
    through platform.public without the manager-only `dealership_users` read
    capability list_users sits behind.
    """

    members = select(DealershipMembership.user_id).where(
        DealershipMembership.dealership_id == dealership_id, membership_is_active()
    )
    stmt = (
        select(User)
        .where(
            (User.tenant_id == dealership_id) | User.id.in_(members),
            User.status.in_(tuple(_ACTIVE_USER_STATUSES)),
        )
        .order_by(User.last_name, User.first_name, User.id)
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
    serves a daily job, never a request — a route resolves a seller's or an
    advisor's label through `get_active_user_acting_in` in the caller's
    dealership. An id that names no user is absent from the result."""

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


def list_user_dealership_ids(db: Session, *, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """Every dealership this user may act in: the home plus the active
    memberships (what `_mint_session` authorises a switch to). Empty for an
    id that names no user."""

    home = db.scalar(select(User.tenant_id).where(User.id == user_id))
    if home is None:
        return frozenset()
    return frozenset({home}) | list_membership_dealership_ids(db, user_id=user_id)


def list_user_dealer_group_ids(db: Session, *, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """The dealer group of every dealership this user may act in
    (`list_user_dealership_ids`) — what a group-scoped record (a customer,
    ADR-014) asks of a colleague it is handed to: they must work in a
    Betrieb of its group (KAN-423). Empty for an id that names no user."""

    dealership_ids = list_user_dealership_ids(db, user_id=user_id)
    if not dealership_ids:
        return frozenset()
    return frozenset(db.scalars(select(Dealership.dealer_group_id).where(Dealership.id.in_(dealership_ids))).all())


def get_user_acting_in(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    """The user when they may act in this dealership — homed there, or
    holding an active membership — else None, whatever their status (the
    caller judges the status: a seller must be active, a label may name a
    suspended one). KAN-200: how Sales resolves a responsible seller, so a
    sister-Betrieb member owns the offers they create there."""

    user = db.get(User, user_id)
    if user is None:
        return None
    if user.tenant_id == dealership_id or dealership_id in list_membership_dealership_ids(db, user_id=user_id):
        return user
    return None


def get_active_user_acting_in(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    """The user when they may be responsible for an open record in this
    dealership — Sales' responsible seller (KAN-200) or a customer's advisor
    (KAN-410; Anto, 2026-10-23: the same rule for both) — else None: they act
    in it (`get_user_acting_in`: homed there or an active member, so a
    sister-Betrieb member owns what they take on there) and are active or
    invited. A suspended or deactivated user owns nothing open (FR-A-37,
    D-A-35: a leaver's records are handed over, "never silently with the
    deactivated user"), and no write may put them back."""

    user = get_user_acting_in(db, dealership_id=dealership_id, user_id=user_id)
    if user is None or not is_active_user(user):
        return None
    return user


def invitation_expiries(db: Session, *, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, dt.datetime]:
    """The live (unconsumed) invitation's expiry per user, for the Users
    list's "invitation expired" state (FR-A-11). One active token per
    (user, kind): the newest unconsumed row is it."""

    ids = list(user_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(AuthToken.user_id, AuthToken.expires_at)
        .where(
            AuthToken.user_id.in_(ids),
            AuthToken.kind == AuthTokenKind.INVITATION,
            AuthToken.consumed_at.is_(None),
        )
        .order_by(AuthToken.user_id, AuthToken.created_at.desc())
    ).all()
    result: dict[uuid.UUID, dt.datetime] = {}
    for user_id, expires_at in rows:
        result.setdefault(user_id, expires_at)
    return result


def user_reads(
    db: Session, users: list[User], *, viewer_id: uuid.UUID | None, viewer_roles: frozenset[AccessRole]
) -> list[UserAdminRead]:
    """The Users screen's read shape: the row plus its sites, the sister
    Betriebe the viewer may see it in (KAN-407) and, for an invited user,
    when the invitation runs out — read in a fixed number of queries for the
    whole page, never one per row."""

    ids = [user.id for user in users]
    locations = user_location.locations_of(db, user_ids=ids)
    expiries = invitation_expiries(db, user_ids=[user.id for user in users if user.status == UserStatus.INVITED])
    other_names = _other_dealership_names(db, user_ids=ids, viewer_id=viewer_id, viewer_roles=viewer_roles)
    reads: list[UserAdminRead] = []
    for user in users:
        primary, additional = locations[user.id]
        reads.append(
            UserAdminRead.model_validate(user, from_attributes=True).model_copy(
                update={
                    "has_signature_image": user.signature_image_ref is not None,
                    "has_photo": user.has_photo,
                    "invitation_expires_at": expiries.get(user.id),
                    "primary_location_id": primary,
                    "additional_location_ids": additional,
                    "other_dealership_names": other_names.get(user.id, []),
                }
            )
        )
    return reads


def user_read(
    db: Session, user: User, *, viewer_id: uuid.UUID | None, viewer_roles: frozenset[AccessRole]
) -> UserAdminRead:
    return user_reads(db, [user], viewer_id=viewer_id, viewer_roles=viewer_roles)[0]


def _other_dealership_names(
    db: Session, *, user_ids: list[uuid.UUID], viewer_id: uuid.UUID | None, viewer_roles: frozenset[AccessRole]
) -> dict[uuid.UUID, list[str]]:
    """KAN-407: each user's active sister memberships by Betrieb name, with
    the visibility `membership.list_memberships` applies — platform staff
    see all; a dealer manager only the Betriebe they hold the flag in
    themselves (the home through `User.is_dealer_manager`, a sister through
    its membership's own flag), so a manager of one side learns nothing
    about the other, not even its name (KAN-199)."""

    if not user_ids:
        return {}
    rows = db.execute(
        select(DealershipMembership.user_id, DealershipMembership.dealership_id, Dealership.legal_name)
        .join(Dealership, Dealership.id == DealershipMembership.dealership_id)
        .where(DealershipMembership.user_id.in_(user_ids), membership_is_active())
        .order_by(Dealership.legal_name, Dealership.id)
    ).all()
    if AccessRole.PLATFORM_ADMIN not in viewer_roles:
        viewer = db.get(User, viewer_id) if viewer_id is not None else None
        managed: set[uuid.UUID] = set()
        if viewer is not None:
            if viewer.is_dealer_manager:
                managed.add(viewer.tenant_id)
            managed |= set(
                db.scalars(
                    select(DealershipMembership.dealership_id).where(
                        DealershipMembership.user_id == viewer.id,
                        DealershipMembership.is_dealer_manager.is_(True),
                        membership_is_active(),
                    )
                ).all()
            )
        rows = [row for row in rows if row.dealership_id in managed]
    names: dict[uuid.UUID, list[str]] = {}
    for user_id, _dealership_id, name in rows:
        names.setdefault(user_id, []).append(name)
    return names
