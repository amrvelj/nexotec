"""KAN-551: deactivates the shared demo login (`demo@nexotec-staging.example`)
an earlier staging seed created, once a personal dealer manager of the demo
Betrieb has signed in with a second factor — so retiring it can never leave
the Betrieb without a way in.

Dry run unless `--commit`. render.yaml's startCommand runs it with
`--commit` on every staging deploy, after the seed (Render's free tier has
no Shell to run it by hand): until the precondition holds it reports what it
is waiting for and changes nothing; once it holds, the next deploy
deactivates the shared user; after that it is a no-op.

The precondition, judged on the database: a user homed in the demo Betrieb,
not the shared one, `active`, holding the manager flag, with a session whose
`amr` carries `mfa` — a sign-in that proved a second factor, not merely an
enrolment. The deactivation is the platform's own write, as the nightly
`platform.user_expiry` job makes it (null actor, KAN-243; platform_admin
authority): `update_user` sets the status, ends every session and
delegation and writes the `update` audit row with the reason
`kan551_shared_demo_login_retired`; then every record the shared user owns
goes to its module's unassigned queue (FR-A-37 — nobody is there to choose),
one `ownership_transferred` audit row per group. A module that refuses the
hand-over is reported and the script exits 1; the records stay with the
deactivated user and the next run hands them over (`release_everything` is
a no-op for a user with nothing open). The last-manager rule (FR-A-13) still
applies and cannot refuse here: the precondition is another active manager.

    python scripts/retire_staging_shared_demo_user.py            # dry run
    python scripts/retire_staging_shared_demo_user.py --commit
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.ownership_registry  # noqa: F401 — registers every context's ownership provider
from app.core.auth import AccessRole
from app.db import SessionLocal
from app.platform.models.auth_session import AuthSession
from app.platform.models.dealership import Dealership
from app.platform.models.user import User, UserStatus
from app.platform.schemas.user import UserUpdate
from app.platform.services import ownership
from app.platform.services import user as user_service

# The seed's own values (scripts/seed_staging_demo.py), repeated because a
# script run as `python scripts/<name>.py` cannot import its neighbour as
# `scripts.…`; tests/test_seed_staging_demo.py keeps the two equal.
DEMO_LEGAL_NAME = "Demo Garage AG"
DEMO_EMAIL = "demo@nexotec-staging.example"

REASON = "kan551_shared_demo_login_retired"
_SYSTEM_ROLES = frozenset({AccessRole.PLATFORM_ADMIN})


def personal_manager_signed_in_with_mfa(db: Session, *, dealership_id, shared_user_id) -> User | None:
    """The first personal manager of the Betrieb who has a session that
    proved a second factor, or None."""

    managers = db.scalars(
        select(User)
        .where(
            User.tenant_id == dealership_id,
            User.id != shared_user_id,
            User.status == UserStatus.ACTIVE,
            User.is_dealer_manager.is_(True),
        )
        .order_by(User.created_at)
    ).all()
    for manager in managers:
        amrs = db.scalars(select(AuthSession.amr).where(AuthSession.user_id == manager.id)).all()
        if any("mfa" in (amr or []) for amr in amrs):
            return manager
    return None


def run(db: Session, *, commit: bool, session_factory=SessionLocal) -> int:
    dealership = db.scalar(select(Dealership).where(Dealership.legal_name == DEMO_LEGAL_NAME))
    if dealership is None:
        print("No demo dealership — nothing to retire.")
        return 0
    shared = db.scalar(select(User).where(User.tenant_id == dealership.id, User.email == DEMO_EMAIL))
    if shared is None:
        print("No shared demo user — nothing to retire.")
        return 0
    if shared.status == UserStatus.DEACTIVATED:
        # A hand-over that failed on an earlier run is retried here.
        if not commit:
            print("The shared demo user is already deactivated.")
            return 0
        return _hand_over(db, session_factory, shared)

    manager = personal_manager_signed_in_with_mfa(db, dealership_id=dealership.id, shared_user_id=shared.id)
    if manager is None:
        print(
            "Waiting: no personal dealer manager of the demo Betrieb has signed in with a second factor yet. "
            "The shared demo user stays as it is."
        )
        return 0
    if not commit:
        print(
            f"Dry run: would deactivate the shared demo user (id={shared.id}); a personal manager (id={manager.id}) "
            "has signed in with a second factor. Run with --commit."
        )
        return 0

    user = user_service.get_user_for_update_or_404(db, dealership.id, shared.id)  # KAN-380's lock order
    user_service.update_user(
        db,
        user=user,
        data=UserUpdate(status=UserStatus.DEACTIVATED),
        actor_id=None,  # a system write (KAN-243: null actor)
        actor_roles=_SYSTEM_ROLES,
        reason=REASON,
        allow_deactivation=True,
    )
    print(f"Deactivated the shared demo user (id={user.id}); its sessions are ended.")
    return _hand_over(db, session_factory, user)


def _hand_over(db: Session, session_factory, user: User) -> int:
    results, failed = ownership.release_everything(db, session_factory, user=user, actor_id=None, reason=REASON)
    for result in results:
        if result.count:
            print(f"Handed {result.count} record(s) of {result.module}/{result.group} to the unassigned queue.")
    if failed:
        for failure in failed:
            print(f"Hand-over refused by {failure.module}/{failure.group}: {failure.reason} — {failure.message}")
        print("The records stay with the deactivated user; the next run retries.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true", help="deactivate; without it, report only")
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        return run(db, commit=args.commit)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
