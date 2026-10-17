"""Report every user who holds `platform_admin` while homed in a dealership
(KAN-199; Dealer Administration PRD: platform_admin is Nexotec staff, never a
member of a dealership — KAN-97 left the existing rows as an open data check).

Read-only. Prints one line per user (id, e-mail, home dealership) and exits 1
when any exist, 0 when none — a fix is a decision per row, never a silent
rewrite by a script.

    python scripts/report_platform_admins_homed_in_dealerships.py
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole
from app.db import SessionLocal
from app.platform.models.dealership import Dealership
from app.platform.models.user import User
from app.platform.services.user import list_membership_dealership_ids


def platform_admins_homed_in_dealerships(db: Session) -> list[tuple[User, Dealership | None, list[Dealership]]]:
    """Every user whose access_roles hold platform_admin, with the dealership
    that homes them (None when the tenant_id names no dealership row) and
    the sister dealerships they hold an active membership of — what KAN-199
    refuses from now on, so each existing case can be decided from here."""

    rows = db.scalars(select(User).order_by(User.created_at, User.id)).all()
    found = []
    for user in rows:
        if AccessRole.PLATFORM_ADMIN.value in (user.access_roles or []):
            sisters = [
                dealership
                for dealership_id in sorted(list_membership_dealership_ids(db, user_id=user.id))
                if (dealership := db.get(Dealership, dealership_id)) is not None
            ]
            found.append((user, db.get(Dealership, user.tenant_id), sisters))
    return found


def main() -> int:
    db = SessionLocal()
    try:
        found = platform_admins_homed_in_dealerships(db)
    finally:
        db.close()
    for user, dealership, sisters in found:
        home = f"{dealership.legal_name} ({dealership.id})" if dealership else f"<no dealership> ({user.tenant_id})"
        members = "; memberships: " + ", ".join(f"{d.legal_name} ({d.id})" for d in sisters) if sisters else ""
        print(f"KAN-199 REPORT: platform_admin {user.email} ({user.id}) is homed in {home}{members}")
    print(f"KAN-199: {len(found)} platform_admin user(s) homed in a dealership")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
