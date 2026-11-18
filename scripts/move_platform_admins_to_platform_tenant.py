"""Move confirmed platform_admin holders into the platform tenant (KAN-221 PR-1).

Anto's rulings: Q2 A (2026-11-14) — each staff member who holds
`platform_admin` while homed in a dealer's Betrieb gets a **new** account in
the platform tenant, and the Betrieb account loses `platform_admin`; Anto
confirms the list row by row. Option 1 (2026-11-17) — the new account signs
in with a **separate Nexotec address** (e-mail is unique platform-wide,
`uq_user_email`); the Betrieb account keeps its own address, its history and
its sign-in.

`scripts/report_platform_admins_homed_in_dealerships.py` prints the list.
This script acts only on the rows of a confirmation file, one per line:

    <user id>,<the new Nexotec address>

Lines that are empty or start with `#` are skipped. **Dry run by default**:
it reads, checks every row and prints what it would do, and writes nothing.
`--apply` writes, one transaction per row: the new account (invited, the
invitation mailed to the new address by the platform sender), then the
Betrieb account without `platform_admin` — both audited with the confirming
staff member (`--actor`) as actor. A row that does not hold is refused and
reported, and the other rows go on. Never run from a migration.

    python scripts/move_platform_admins_to_platform_tenant.py --actor <staff user id> confirmed.csv
    python scripts/move_platform_admins_to_platform_tenant.py --actor <staff user id> confirmed.csv --apply

Exit status: 0 when every row held (or would hold), 1 when a row was
refused, 2 when the run cannot start (no platform tenant, no such actor,
an unreadable file).
"""

import argparse
import dataclasses
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole
from app.core.errors import AppError
from app.db import SessionLocal
from app.platform.identity.models.user import User, UserStatus
from app.platform.identity.schemas.user import UserCreate, UserUpdate
from app.platform.identity.services import identity
from app.platform.identity.services import user as user_service
from app.platform.organisation.models.dealership import PLATFORM_DEALERSHIP_ID, Dealership, DealershipKind
from app.platform.reference.services.mailer import PlatformMailer, get_mailer

MOVE_REASON = "KAN-221: platform_admin moved to the platform tenant, row confirmed by Anto"
_STAFF = frozenset({AccessRole.PLATFORM_ADMIN})
_EMAIL = TypeAdapter(EmailStr)


@dataclasses.dataclass(frozen=True)
class Row:
    line: int
    user_id: uuid.UUID | None
    new_email: str | None
    problem: str | None = None


@dataclasses.dataclass(frozen=True)
class Outcome:
    line: int
    user_id: str
    moved: bool
    message: str
    new_user_id: uuid.UUID | None = None


class CannotStart(Exception):
    pass


def parse_confirmation_file(text: str) -> list[Row]:
    rows = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        try:
            user_id = uuid.UUID(parts[0])
        except ValueError:
            rows.append(Row(number, None, None, f"not a user id: {parts[0]!r}"))
            continue
        if len(parts) < 2 or not parts[1]:
            rows.append(Row(number, user_id, None, "no new address — option 1 needs a separate Nexotec address"))
            continue
        if len(parts) > 2:
            rows.append(Row(number, user_id, None, "more than two columns"))
            continue
        try:
            new_email = str(_EMAIL.validate_python(parts[1])).lower()
        except ValidationError:
            rows.append(Row(number, user_id, None, f"not an e-mail address: {parts[1]!r}"))
            continue
        rows.append(Row(number, user_id, new_email))
    return rows


def _check_row(db: Session, row: Row) -> tuple[User | None, str | None]:
    """The Betrieb account the row names, or why the row does not hold."""

    if row.problem is not None:
        return None, row.problem
    user = db.get(User, row.user_id, with_for_update=True)
    if user is None:
        return None, "no such user"
    if AccessRole.PLATFORM_ADMIN.value not in (user.access_roles or []):
        return None, "does not hold platform_admin"
    home = db.get(Dealership, user.tenant_id)
    if home is None or home.kind != DealershipKind.DEALER:
        return None, "is not homed in a dealer's Betrieb"
    if user.status == UserStatus.DEACTIVATED:
        return None, "is deactivated"
    if not [role for role in user.access_roles if role != AccessRole.PLATFORM_ADMIN.value]:
        return None, (
            "holds no role besides platform_admin — give the Betrieb account its roles in that Betrieb first"
        )
    if db.scalar(select(User.id).where(func.lower(User.email) == row.new_email)) is not None:
        return None, f"the address {row.new_email} is already in use (uq_user_email)"
    return user, None


def _move(db: Session, *, user: User, new_email: str, actor_id: uuid.UUID, mailer: PlatformMailer) -> User:
    new_account = user_service.create_user(
        db,
        dealership_id=PLATFORM_DEALERSHIP_ID,
        data=UserCreate(
            first_name=user.first_name,
            last_name=user.last_name,
            email=new_email,
            role=user.role,
            access_roles=[AccessRole.PLATFORM_ADMIN],
            is_dealer_manager=False,
        ),
        actor_id=actor_id,
        actor_roles=_STAFF,
        commit=False,
    )
    identity.issue_invitation(db, mailer, user=new_account, actor_id=actor_id, ip_address="script")
    remaining = [AccessRole(role) for role in user.access_roles if role != AccessRole.PLATFORM_ADMIN.value]
    user_service.update_user(
        db,
        user=user,
        data=UserUpdate(access_roles=remaining),
        actor_id=actor_id,
        actor_roles=_STAFF,
        reason=MOVE_REASON,
    )
    return new_account


def run(
    db: Session, rows: list[Row], *, actor_id: uuid.UUID, apply: bool, mailer: PlatformMailer
) -> list[Outcome]:
    if db.get(Dealership, PLATFORM_DEALERSHIP_ID) is None:
        raise CannotStart("the platform tenant does not exist — run `alembic upgrade heads` first")
    actor = db.get(User, actor_id)
    if actor is None or AccessRole.PLATFORM_ADMIN.value not in (actor.access_roles or []):
        raise CannotStart("--actor must name a platform_admin user")
    if actor.status != UserStatus.ACTIVE:
        raise CannotStart("--actor must name an active user")

    outcomes = []
    seen: set[str] = set()
    for row in rows:
        label = str(row.user_id) if row.user_id else "?"
        user, problem = _check_row(db, row)
        if problem is None and row.new_email in seen:
            problem = f"the address {row.new_email} is named twice in the file"
        if problem is None and user is not None and user.id == actor_id:
            problem = "the actor cannot move their own account in the same run"
        if problem is not None or user is None:
            outcomes.append(Outcome(row.line, label, False, f"refused: {problem}"))
            continue
        assert row.new_email is not None
        seen.add(row.new_email)
        if not apply:
            outcomes.append(Outcome(row.line, label, False, f"would move: new platform account {row.new_email}"))
            continue
        try:
            new_account = _move(db, user=user, new_email=row.new_email, actor_id=actor_id, mailer=mailer)
        except AppError as exc:
            db.rollback()
            outcomes.append(Outcome(row.line, label, False, f"refused: {exc.message}"))
            continue
        outcomes.append(
            Outcome(row.line, label, True, f"moved: new platform account {row.new_email}", new_account.id)
        )
    return outcomes


def main(argv: list[str] | None = None, *, session_factory: Callable[[], Session] = SessionLocal) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("confirmation_file", type=Path)
    parser.add_argument("--actor", required=True, type=uuid.UUID, help="the confirming staff member's user id")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    try:
        rows = parse_confirmation_file(args.confirmation_file.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"KAN-221: cannot read {args.confirmation_file}: {exc.strerror}")
        return 2
    db = session_factory()
    try:
        outcomes = run(db, rows, actor_id=args.actor, apply=args.apply, mailer=get_mailer())
    except CannotStart as exc:
        print(f"KAN-221: {exc}")
        return 2
    finally:
        db.close()
    for outcome in outcomes:
        print(f"KAN-221 line {outcome.line}: {outcome.user_id} {outcome.message}")
    mode = "applied" if args.apply else "dry run, nothing written"
    print(f"KAN-221: {len(outcomes)} row(s), {sum(o.moved for o in outcomes)} moved ({mode})")
    return 1 if any(o.message.startswith("refused") for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
