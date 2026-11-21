"""The Platform users panel (KAN-221 PR-6; Dealer Administration D-A-05,
FR-A-15; ADR-188): Nexotec staff, read and administered by staff.

- **The list** — every user holding `platform_admin`: the staff homed in the
  platform tenant, and the legacy holders still homed in a dealer's Betrieb
  (KAN-199's report; moved only by `scripts/move_platform_admins_to_platform_tenant.py`,
  never here), each flagged. With the second-factor state and the running
  support session, if any.
- **Grant** — `platform_admin` is held only in the platform tenant and by
  everyone there (ADR-188), so granting it is creating a staff account there:
  the user and the invitation in one transaction, as on the Users screen.
- **Revoke** — for the same reason, a staff account in the platform tenant
  loses the role by being deactivated (the account stays; A-RULE-5). A legacy
  holder loses the role from their roles and keeps their Betrieb account;
  one whose only role it is is refused (`staff_role_is_the_only_role`) — the
  Betrieb's Users screen decides that account. Never oneself
  (`platform_admin_self_revoke`) and never the last active holder
  (`last_platform_admin`): either would leave Nexotec without staff access.
  Every active holder is locked in id order before the count, so two staff
  revoking each other at once cannot both pass.
"""

import dataclasses
import uuid
from collections.abc import Callable

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal
from app.core.base import utcnow
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.pagination import PageParams, build_page, paginate_query
from app.platform.identity.models.support_session import SupportSession
from app.platform.identity.models.user import User, UserRole, UserStatus
from app.platform.identity.schemas.platform_staff import PlatformStaffCreate, PlatformStaffEntry
from app.platform.identity.schemas.user import UserCreate, UserUpdate
from app.platform.identity.services import mfa
from app.platform.identity.services import ownership as ownership_service
from app.platform.identity.services import user as user_service
from app.platform.organisation.models.dealership import PLATFORM_DEALERSHIP_ID, Dealership

STAFF_ROLE = AccessRole.PLATFORM_ADMIN.value


def holds_platform_admin():
    """`access_roles` is a JSON list of fixed tokens: the quoted token in its
    text is the portable containment test (as `platform/reconciliation.py`)."""

    return cast(User.access_roles, String).like(f'%"{STAFF_ROLE}"%')


@dataclasses.dataclass(frozen=True)
class RevokeOutcome:
    user: User
    deactivated: bool


def list_staff(db: Session, *, params: PageParams) -> tuple[list[PlatformStaffEntry], str | None]:
    rows, next_cursor = build_page(
        list(db.scalars(paginate_query(select(User).where(holds_platform_admin()), model=User, params=params)).all()),
        params,
    )
    return entries_for(db, rows), next_cursor


def entries_for(db: Session, rows: list[User]) -> list[PlatformStaffEntry]:
    """The panel's lines for these users, in a fixed number of queries."""

    ids = [row.id for row in rows]
    with_factor = mfa.users_with_factor(db, ids)
    homes = {row.tenant_id for row in rows}
    home_names = {
        d.id: d.trade_name or d.legal_name
        for d in (db.scalars(select(Dealership).where(Dealership.id.in_(homes))).all() if homes else ())
    }
    now = utcnow()
    running = {
        s.staff_user_id: s.id
        for s in (
            db.scalars(
                select(SupportSession).where(
                    SupportSession.staff_user_id.in_(ids),
                    SupportSession.ended_at.is_(None),
                    SupportSession.expires_at > now,
                )
            ).all()
            if ids
            else ()
        )
    }
    return [
        PlatformStaffEntry(
            id=row.id,
            first_name=row.first_name,
            last_name=row.last_name,
            email=row.email,
            status=row.status,
            version=row.version,
            home_dealership_id=row.tenant_id,
            home_dealership_name=home_names.get(row.tenant_id, ""),
            in_platform_tenant=row.tenant_id == PLATFORM_DEALERSHIP_ID,
            mfa_enrolled=row.id in with_factor,
            running_support_session_id=running.get(row.id),
        )
        for row in rows
    ]


def create_staff(db: Session, *, data: PlatformStaffCreate, principal: Principal) -> User:
    """A new staff account in the platform tenant, holding `platform_admin`.
    Flushed, not committed: the route issues the invitation and commits once."""

    if db.get(Dealership, PLATFORM_DEALERSHIP_ID) is None:
        raise NotFoundError("The Nexotec platform tenant does not exist.")
    return user_service.create_user(
        db,
        dealership_id=PLATFORM_DEALERSHIP_ID,
        data=UserCreate(
            first_name=data.first_name,
            last_name=data.last_name,
            email=data.email,
            role=UserRole.ADMIN,
            access_roles=[AccessRole.PLATFORM_ADMIN],
        ),
        actor_id=principal.user_id,
        actor_roles=principal.roles,
        commit=False,
    )


def _refuse(message: str, reason: str) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details={"reason": reason})


def revoke_staff(
    db: Session,
    session_factory: Callable[[], Session],
    *,
    user_id: uuid.UUID,
    if_match: int,
    reason: str,
    principal: Principal,
) -> RevokeOutcome:
    from app.core.concurrency import check_version

    # Every active holder, locked in id order (the target among them when it
    # is active), before anything is counted or written.
    active_holders = list(
        db.scalars(
            select(User)
            .where(holds_platform_admin(), User.status == UserStatus.ACTIVE)
            .order_by(User.id)
            .with_for_update(key_share=False)
        ).all()
    )
    target = next((u for u in active_holders if u.id == user_id), None)
    if target is None:
        target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None or STAFF_ROLE not in (target.access_roles or []):
        raise NotFoundError("Staff member not found.")
    check_version(target.version, if_match, entity_name="User")
    if target.id == principal.user_id:
        raise _refuse("A staff member cannot revoke their own staff access.", "platform_admin_self_revoke")
    if target.status == UserStatus.ACTIVE and len(active_holders) <= 1:
        raise _refuse("Nexotec would be left without an active staff member.", "last_platform_admin")

    if target.tenant_id == PLATFORM_DEALERSHIP_ID:
        updated, _results, _failed = ownership_service.deactivate_user(
            db,
            session_factory,
            user=target,
            reason=reason,
            requested=[],
            actor_id=principal.user_id,
            actor_roles=principal.roles,
        )
        return RevokeOutcome(user=updated, deactivated=True)

    remaining = [AccessRole(role) for role in target.access_roles if role != STAFF_ROLE]
    if not remaining:
        raise _refuse(
            "platform_admin is this account's only role; decide the account on its business's Users screen.",
            "staff_role_is_the_only_role",
        )
    updated = user_service.update_user(
        db,
        user=target,
        data=UserUpdate(access_roles=remaining),
        actor_id=principal.user_id,
        actor_roles=principal.roles,
        reason=reason,
    )
    return RevokeOutcome(user=updated, deactivated=False)
