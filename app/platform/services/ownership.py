"""The deactivation step (KAN-200; Dealer Administration FR-A-37, D-A-35):
what a user still owns across every module, and the hand-over of each
group to a colleague or to nobody when they are deactivated.

Platform never names a module: it asks `app.core.ownership`'s registry,
which the composition root filled (`app.ownership_registry`). Each transfer
is the owning module's own write on a session of its own, committed there
(ADR-047); platform records one `ownership_transferred` audit row per group
with the actor, on its own session, after the module's commit. A module
that fails leaves its records with the user: the step answers after the
rows already moved are recorded, the user stays deactivated (access is cut
first — the safe direction), the answer names the failed group, and
`GET …/ownership` lists what is still theirs for
`POST …/ownership/transfers`, the retry. Nothing is rolled back across the
seam, which is the point.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import AppError, UnprocessableEntityError
from app.core.ownership import OwnedGroup, OwnershipTransfer, ownership_provider, ownership_providers
from app.platform.models.user import EmploymentStatus, User, UserStatus
from app.platform.schemas.user import OwnershipTransferFailure, OwnershipTransferRequest, OwnershipTransferResult
from app.platform.services import user as user_service

logger = logging.getLogger(__name__)

MODULE_FAILED = "module_failed"


def list_open(db: Session, *, user: User) -> list[OwnedGroup]:
    groups: list[OwnedGroup] = []
    for provider in ownership_providers():
        groups.extend(provider.list_open(db, dealership_id=user.tenant_id, user_id=user.id))
    return groups


def registered_modules() -> list[str]:
    return [provider.module for provider in ownership_providers()]


def resolve_transfers(
    db: Session, *, user: User, requested: list[OwnershipTransferRequest], require_every_group: bool
) -> list[OwnershipTransfer]:
    """Checks the manager's decisions against what the user owns now: every
    (module, group) must be one the user holds, decided at most once, and a
    colleague must be an active user of the same dealership who is not the
    leaver — and one the module admits (`refuses`: Sales says no to a
    colleague who cannot act in a sister Betrieb some of the records belong
    to). All of it before anything changes, so a wrong choice is a 422 with
    the user still active. With `require_every_group`, every open group
    needs a decision — the deactivation's rule: never silently with the
    leaver. The 422's `details.problems[]` carry a stable `reason` per group
    (the SPA translates it) beside the English `message`."""

    open_groups = {(g.module, g.group): g for g in list_open(db, user=user)}
    seen: set[tuple[str, str]] = set()
    resolved: list[OwnershipTransfer] = []
    problems: list[dict[str, Any]] = []

    def problem(module: str, group: str, reason: str, message: str, count: int | None = None) -> None:
        entry: dict[str, Any] = {
            "module": module,
            "group": group,
            "reason": reason,
            "message": f"{module}/{group}: {message}",
        }
        if count is not None:
            entry["count"] = count
        problems.append(entry)

    for item in requested:
        key = (item.module, item.group)
        if key in seen:
            problem(item.module, item.group, "decided_twice", "decided twice")
            continue
        seen.add(key)
        if key not in open_groups:
            problem(item.module, item.group, "not_owned", "not a group this user owns")
            continue
        to_label: str | None = None
        if item.to_user_id is not None:
            if item.to_user_id == user.id:
                problem(item.module, item.group, "self", "the user cannot receive their own records")
                continue
            colleague = db.get(User, item.to_user_id)
            # FR-A-37: "another active user" — an invited colleague has no
            # password yet and a suspended one cannot sign in.
            if colleague is None or colleague.tenant_id != user.tenant_id or colleague.status != UserStatus.ACTIVE:
                problem(
                    item.module, item.group, "colleague_not_active", "toUserId is not an active user of this dealership"
                )
                continue
            to_label = f"{colleague.first_name} {colleague.last_name}".strip()
        candidate = OwnershipTransfer(
            module=item.module, group=item.group, to_user_id=item.to_user_id, to_label=to_label
        )
        provider = ownership_provider(item.module)
        refusal = provider.refuses(db, from_user_id=user.id, transfer=candidate) if provider is not None else None
        if refusal is not None:
            problems.append(
                {
                    "module": item.module,
                    "group": item.group,
                    "reason": refusal.code,
                    "message": refusal.message,
                    "count": refusal.count,
                }
            )
            continue
        resolved.append(candidate)
    if require_every_group:
        for key in open_groups:
            if key not in seen:
                problem(key[0], key[1], "undecided", "needs a decision: a colleague, or null to leave it unassigned")
    if problems:
        raise UnprocessableEntityError(
            "The reassignment step is incomplete.",
            details={
                "problems": problems,
                "openGroups": [{"module": g.module, "group": g.group, "count": g.count} for g in open_groups.values()],
            },
        )
    return resolved


def apply_transfers(
    db: Session,
    session_factory: Callable[[], Session],
    *,
    user: User,
    transfers: list[OwnershipTransfer],
    actor_id: uuid.UUID | None,
    reason: str | None,
) -> tuple[list[OwnershipTransferResult], list[OwnershipTransferFailure]]:
    """Runs each transfer in its module (own session, own commit) and
    records it on platform's audit log, committed per group — so what has
    moved is recorded even when a later module fails. A module that raises
    is logged (the alarm) and reported in the second list; the next groups
    still run, and the failed group's records stay with the user for the
    retry — never a 500 after the status already changed (ADR-047: no
    shared transaction to undo, so the answer says what happened)."""

    results: list[OwnershipTransferResult] = []
    failed: list[OwnershipTransferFailure] = []
    for transfer in transfers:
        provider = ownership_provider(transfer.module)
        if provider is None:  # resolve_transfers admitted only registered modules
            raise UnprocessableEntityError(f"No module '{transfer.module}' is registered.")
        try:
            count = provider.transfer(
                session_factory,
                dealership_id=user.tenant_id,
                from_user_id=user.id,
                transfer=transfer,
                actor_id=actor_id,
                reason=reason,
            )
        except Exception as exc:
            logger.exception(
                "platform.ownership.transfer_failed module=%s group=%s user=%s",
                transfer.module,
                transfer.group,
                user.id,
                extra={"ownershipModule": transfer.module, "group": transfer.group, "userId": str(user.id)},
            )
            db.rollback()
            # The module's own refusal travels (its message is for the caller);
            # anything else is a fixed code — an exception's text can carry a
            # statement, its parameters and another Betrieb's ids. The log has it.
            if isinstance(exc, AppError):
                reason = str((exc.details or {}).get("reason") or exc.code)
                message = exc.message
            else:
                reason, message = MODULE_FAILED, "The module could not complete the hand-over; the error is in the log."
            failed.append(
                OwnershipTransferFailure(module=transfer.module, group=transfer.group, reason=reason, message=message)
            )
            continue
        record_audit_event(
            db,
            entity_type="user",
            entity_id=user.id,
            tenant_id=user.tenant_id,
            action="ownership_transferred",
            actor_id=actor_id,
            before={"module": transfer.module, "group": transfer.group, "owner_user_id": str(user.id)},
            after={
                "module": transfer.module,
                "group": transfer.group,
                "owner_user_id": str(transfer.to_user_id) if transfer.to_user_id is not None else None,
                "count": count,
            },
            reason=reason,
        )
        db.commit()
        results.append(
            OwnershipTransferResult(
                module=transfer.module, group=transfer.group, to_user_id=transfer.to_user_id, count=count
            )
        )
    return results, failed


def release_everything(
    db: Session, session_factory: Callable[[], Session], *, user: User, actor_id: uuid.UUID | None, reason: str
) -> tuple[list[OwnershipTransferResult], list[OwnershipTransferFailure]]:
    """The automatic deactivation's hand-over (FR-A-12): nobody is there
    to choose, so every open group goes to the module's own queue."""

    transfers = [
        OwnershipTransfer(module=g.module, group=g.group, to_user_id=None, to_label=None)
        for g in list_open(db, user=user)
    ]
    return apply_transfers(db, session_factory, user=user, transfers=transfers, actor_id=actor_id, reason=reason)


def deactivate_user(
    db: Session,
    session_factory: Callable[[], Session],
    *,
    user: User,
    reason: str,
    requested: list[OwnershipTransferRequest],
    actor_id: uuid.UUID,
    actor_roles: frozenset,
    employment_status: EmploymentStatus | None = None,
) -> tuple[User, list[OwnershipTransferResult], list[OwnershipTransferFailure]]:
    """Validates the whole step first (the last-manager rule, every group
    decided), then cuts access (platform's own commit: status, sessions,
    delegations, the stamps), then hands the records over module by module."""

    from app.platform.schemas.user import UserUpdate  # the PATCH shape, reused for the status change

    user_service.assert_may_touch(user=user, actor_roles=actor_roles)
    user_service.assert_user_is_not_deactivated(user)
    transfers = resolve_transfers(db, user=user, requested=requested, require_every_group=True)
    updated = user_service.update_user(
        db,
        user=user,
        data=(
            UserUpdate(status=UserStatus.DEACTIVATED)
            if employment_status is None  # the field is omitted, not null: a null is refused
            else UserUpdate(status=UserStatus.DEACTIVATED, employment_status=employment_status)
        ),
        actor_id=actor_id,
        actor_roles=actor_roles,
        reason=reason,
        allow_deactivation=True,
    )
    results, failed = apply_transfers(
        db, session_factory, user=updated, transfers=transfers, actor_id=actor_id, reason=reason
    )
    return updated, results, failed
