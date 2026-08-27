"""One-time staging demo seed: exactly one Dealership + one manager User,
so there's a login to hand a non-technical stakeholder directly. No
self-serve signup exists (or should exist) for a demo environment.
Idempotent — safe to run on every deploy: creates the demo dealership/user
once, and on every later run reconciles the existing user's
auth_identity_id against whatever DMS_SEED_DEMO_AUTH_IDENTITY_ID currently
holds. That reconciliation matters in practice — correcting a wrong value
in the Zitadel `sub` env var (a typo, or the placeholder used before the
real demo account existed) must not require a manual DB fix to take
effect on the next deploy.

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
from app.platform.models.user import User, UserRole
from app.platform.schemas.dealership import DealershipAddress, DealershipCreate
from app.platform.schemas.user import UserCreate, UserUpdate
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
        # No platform_admin User row exists in this schema (platform_admin
        # is a JWT claim, not a tenant-owned User) — a synthetic actor id
        # stands in for "the platform" on this seed's own audit trail, same
        # as the acceptance tests do for platform_admin-attributed actions.
        seed_actor_id = uuid.uuid4()

        dealership = db.scalar(select(Dealership).where(Dealership.legal_name == DEMO_LEGAL_NAME))
        if dealership is None:
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
            print(f"Created demo dealership '{DEMO_LEGAL_NAME}' (id={dealership.id}).")
        else:
            print(f"Demo dealership already exists (id={dealership.id}).")

        user = db.scalar(
            select(User).where(User.tenant_id == dealership.id, User.email == DEMO_EMAIL)
        )
        if user is None:
            user = user_service.create_user(
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
            print(f"Created demo user {DEMO_EMAIL} with auth_identity_id={auth_identity_id}.")
        elif user.auth_identity_id != auth_identity_id:
            # The env var is the source of truth. A stale mapping here isn't
            # a hypothetical — it's exactly what happens if this script's
            # first successful run ever used a placeholder/wrong sub before
            # the real Zitadel demo account existed, and would otherwise
            # require a manual DB fix to correct on every later redeploy.
            user_service.update_user(
                db,
                user=user,
                data=UserUpdate(auth_identity_id=auth_identity_id),
                actor_id=seed_actor_id,
            )
            print(f"Updated demo user {DEMO_EMAIL}'s auth_identity_id to {auth_identity_id}.")
        else:
            print(f"Demo user {DEMO_EMAIL} already mapped to auth_identity_id={auth_identity_id} — nothing to do.")

        print(f"Sign in via Zitadel as {DEMO_EMAIL} using the pre-provisioned demo account.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
