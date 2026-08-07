"""One-time staging demo seed: exactly one Dealer + one dealer_admin User +
a real Credential, so there's a login to hand a non-technical stakeholder
directly. No self-serve signup exists (or should exist) for a demo
environment. Idempotent — safe to run on every deploy, no-ops without
changes if the demo Dealer already exists.

render.yaml's startCommand runs this automatically on every deploy (piped
through `|| true` so a missing DMS_SEED_DEMO_PASSWORD never blocks app
startup — Render's free tier has no Shell to run this by hand, see the
render.yaml header comment). It exits 1 with no side effects if the
password isn't set yet; once it is, the next deploy actually seeds.

Can also be run directly, e.g. against local dev for testing:

    DMS_SEED_DEMO_PASSWORD='<a-password-you-choose-for-this-run>' \\
        python scripts/seed_staging_demo.py

No default password — same "no hardcoded fallback for secrets" discipline
as DMS_TAX_ID_ENCRYPTION_KEY (app/core/config.py). Whatever value ends up
in DMS_SEED_DEMO_PASSWORD becomes the login handed to the stakeholder;
choose it fresh, don't reuse anything from local dev or CI fixtures.
"""

import os
import sys
import uuid

from sqlalchemy import select

from app.core.auth import AccessRole
from app.db import SessionLocal
from app.models.dealer import Dealer
from app.models.user import UserRole
from app.schemas.dealer import DealerAddress, DealerCreate
from app.schemas.user import UserCreate
from app.services import auth as auth_service
from app.services import dealer as dealer_service
from app.services import user as user_service

DEMO_LEGAL_NAME = "Demo Garage AG"
DEMO_EMAIL = "demo@nexotec-staging.example"


def main() -> None:
    password = os.environ.get("DMS_SEED_DEMO_PASSWORD")
    if not password:
        print("DMS_SEED_DEMO_PASSWORD is required (no default — choose a fresh password for this run).")
        sys.exit(1)

    db = SessionLocal()
    try:
        existing = db.scalar(select(Dealer).where(Dealer.legal_name == DEMO_LEGAL_NAME))
        if existing is not None:
            print(f"Demo dealer already exists (id={existing.id}) — nothing to do.")
            print(f"Login: {DEMO_EMAIL} / <the password originally set>")
            return

        # No platform_admin User row exists in this schema (platform_admin
        # is a JWT claim, not a tenant-owned User) — a synthetic actor id
        # stands in for "the platform" on this seed's own audit trail, same
        # as the acceptance tests do for platform_admin-attributed actions.
        seed_actor_id = uuid.uuid4()

        dealer = dealer_service.create_dealer(
            db,
            data=DealerCreate(
                legal_name=DEMO_LEGAL_NAME,
                dealer_license_number="DEMO-0001",
                license_state="ZH",
                franchise_type="independent",
                address=DealerAddress(
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

        user = user_service.create_user(
            db,
            dealer_id=dealer.id,
            data=UserCreate(
                first_name="Demo",
                last_name="Admin",
                email=DEMO_EMAIL,
                role=UserRole.GM,
                access_role=AccessRole.DEALER_ADMIN,
                auth_identity_id="staging-seed",
            ),
            actor_id=seed_actor_id,
        )

        auth_service.set_credential(db, user=user, password=password, actor_id=seed_actor_id)

        print(f"Seeded demo dealer '{DEMO_LEGAL_NAME}' (id={dealer.id}) and admin user {DEMO_EMAIL}.")
        print("Hand this login to the stakeholder: email above + the password you just set.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
