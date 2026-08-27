"""One-time staging demo seed: exactly one Dealership + one manager User,
so there's a login to hand a non-technical stakeholder directly. No
self-serve signup exists (or should exist) for a demo environment.
Idempotent — safe to run on every deploy, no-ops without changes if the
demo Dealership already exists.

render.yaml's startCommand runs this automatically on every deploy (piped
through `|| true` so a missing DMS_SEED_DEMO_AUTH_IDENTITY_ID never blocks
app startup — Render's free tier has no Shell to run this by hand, see the
render.yaml header comment). It exits 1 with no side effects if that var
isn't set yet; once it is, the next deploy actually seeds.

WP-4 (Zitadel replaces the interim password store): there is no local
password to hand out anymore. DMS_SEED_DEMO_AUTH_IDENTITY_ID must be the
real Zitadel `sub` for a demo account pre-provisioned once, by hand, in the
Zitadel console — this script only maps that already-real external
identity to a Nexotec User, the same manual bootstrap every real user
goes through (see app.platform.models.user::User.auth_identity_id's own
docstring). It cannot provision the Zitadel side itself.

Can also be run directly, e.g. against local dev for testing:

    DMS_SEED_DEMO_AUTH_IDENTITY_ID='<a-real-or-throwaway-sub-for-this-run>' \\
        python scripts/seed_staging_demo.py
"""

import os
import sys
import uuid

from sqlalchemy import select

from app.core.auth import AccessRole
from app.db import SessionLocal
from app.platform.models.dealership import Dealership
from app.platform.models.user import UserRole
from app.platform.schemas.dealership import DealershipAddress, DealershipCreate
from app.platform.schemas.user import UserCreate
from app.platform.services import dealership as dealership_service
from app.platform.services import user as user_service

DEMO_LEGAL_NAME = "Demo Garage AG"
DEMO_EMAIL = "demo@nexotec-staging.example"


def main() -> None:
    auth_identity_id = os.environ.get("DMS_SEED_DEMO_AUTH_IDENTITY_ID")
    if not auth_identity_id:
        print("DMS_SEED_DEMO_AUTH_IDENTITY_ID is required — the demo account's real Zitadel sub.")
        sys.exit(1)

    db = SessionLocal()
    try:
        existing = db.scalar(select(Dealership).where(Dealership.legal_name == DEMO_LEGAL_NAME))
        if existing is not None:
            print(f"Demo dealership already exists (id={existing.id}) — nothing to do.")
            print(f"Sign in via Zitadel as {DEMO_EMAIL} using the pre-provisioned demo account.")
            return

        # No platform_admin User row exists in this schema (platform_admin
        # is a JWT claim, not a tenant-owned User) — a synthetic actor id
        # stands in for "the platform" on this seed's own audit trail, same
        # as the acceptance tests do for platform_admin-attributed actions.
        seed_actor_id = uuid.uuid4()

        dealership = dealership_service.create_dealership(
            db,
            data=DealershipCreate(
                legal_name=DEMO_LEGAL_NAME,
                dealer_license_number="DEMO-0001",
                license_state="ZH",
                franchise_type="independent",
                address=DealershipAddress(
                    street="Bahnhofstrasse",
                    house_number="1",
                    postal_code="8001",
                    locality="Zürich",
                    canton="ZH",
                ),
                phone="+41441234567",
                tax_id="CHE-000.000.000",
            ),
            actor_id=seed_actor_id,
        )

        user_service.create_user(
            db,
            dealership_id=dealership.id,
            data=UserCreate(
                first_name="Demo",
                last_name="Admin",
                email=DEMO_EMAIL,
                role=UserRole.GM,
                access_roles=[AccessRole.SALES],
                is_dealer_manager=True,
                auth_identity_id=auth_identity_id,
            ),
            actor_id=seed_actor_id,
        )

        print(f"Seeded demo dealership '{DEMO_LEGAL_NAME}' (id={dealership.id}) and admin user {DEMO_EMAIL}.")
        print("Sign in via Zitadel as that user using the pre-provisioned demo account.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
