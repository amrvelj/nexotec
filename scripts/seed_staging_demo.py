"""One-time staging demo seed: exactly one Dealership + one manager User,
so there's a login to hand a non-technical stakeholder directly. No
self-serve signup exists (or should exist) for a demo environment.
Idempotent — safe to run on every deploy: creates the demo dealership/user
once, and on every later run reconciles the demo user's password against
whatever DMS_SEED_DEMO_PASSWORD currently holds. That reconciliation matters
in practice — rotating the demo password must not require a manual DB fix
to take effect on the next deploy.

render.yaml's startCommand runs this automatically on every deploy (piped
through `|| true` so a missing DMS_SEED_DEMO_PASSWORD never blocks app
startup — Render's free tier has no Shell to run this by hand, see the
render.yaml header comment). It exits 1 with no side effects if that var
isn't set, or if the password fails the platform policy (FR-AU-04…06: at
least 12 characters, not the user's or the Betrieb's name); once it is
right, the next deploy actually seeds.

KAN-196 (D-A-07: Nexotec owns authentication): the demo account is a local
password user like every other, with one exception — the demo Betrieb's
auth config is created with MFA **not** enforced, because a shared demo
account cannot carry a stakeholder's authenticator. The demo manager may
switch it on in Dealer Administration; this seed never switches it back.
Nobody else ever sets a password for a user (FR-AU-10); the seed is the
environment's bootstrap, not a user, and writes the credential directly
through the same policy-checked service.

Can also be run directly, e.g. against local dev for testing:

    DMS_SEED_DEMO_PASSWORD='<a-throwaway-password-for-this-run>' \\
        python scripts/seed_staging_demo.py
"""

import os
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole
from app.core.errors import BadRequestError, UnprocessableEntityError
from app.core.permissions import ModuleCode
from app.db import SessionLocal
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.credential import Credential
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.user import User, UserRole, UserStatus
from app.platform.schemas.dealership import DealershipAddress, DealershipCreate
from app.platform.schemas.user import UserCreate, UserUpdate
from app.platform.services import dealership as dealership_service
from app.platform.services import entitlement as entitlement_service
from app.platform.services import identity, password_hashing
from app.platform.services import user as user_service
from app.platform.services.password_policy import validate_new_password

DEMO_LEGAL_NAME = "Demo Garage AG"
DEMO_EMAIL = "demo@nexotec-staging.example"
DEMO_FIRST_NAME = "Demo"
DEMO_LAST_NAME = "Admin"

_ACTOR_COLUMNS = ("created_by", "updated_by")


def _stamp_actor(db: Session, *, actor: User, rows: tuple[DealerGroup, Dealership, User]) -> list[str]:
    """KAN-318: the demo manager is the actor of its own group, dealership
    and user row. The services stamp whatever actor_id they are given, and
    the only user this environment has cannot exist before its dealership
    does (create_user needs the dealership's id), so the creates above run
    with a placeholder and the rows are stamped afterwards — created_by has
    no service path at all, so this is a direct write. Every value that
    names no `user` row is replaced, on every run: an environment seeded
    before KAN-318 still holds the random uuid4 the seed used to write, and
    the nightly platform reconciliation (app.platform.reconciliation) would
    report it each night. A value that does name a user — another user's
    later edit — is left alone; so is null, which names nobody on purpose
    (app.platform.services.actor_reference). Returns what changed."""

    named = {value for row in rows for column in _ACTOR_COLUMNS if (value := getattr(row, column)) is not None}
    existing = set(db.scalars(select(User.id).where(User.id.in_(named))).all()) if named else set()
    changed: list[str] = []
    for row in rows:
        for column in _ACTOR_COLUMNS:
            value = getattr(row, column)
            if value is not None and value not in existing:
                setattr(row, column, actor.id)
                changed.append(f"{row.__tablename__}.{column}")
    if changed:
        db.commit()
    return changed


def _policy_refusal(password: str) -> str | None:
    """The platform password policy, checked before anything is written so a
    refused password leaves no half-seeded environment behind."""

    try:
        validate_new_password(
            password,
            dealership_names=(DEMO_LEGAL_NAME,),
            first_name=DEMO_FIRST_NAME,
            last_name=DEMO_LAST_NAME,
            email=DEMO_EMAIL,
        )
    except UnprocessableEntityError as exc:
        return exc.message
    return None


def main() -> None:
    password = os.environ.get("DMS_SEED_DEMO_PASSWORD")
    if not password:
        print(
            "DMS_SEED_DEMO_PASSWORD is required — the demo account's password (never printed, never stored in clear)."
        )
        sys.exit(1)
    refusal = _policy_refusal(password)
    if refusal is not None:
        print(f"DMS_SEED_DEMO_PASSWORD fails the password policy: {refusal}")
        sys.exit(1)

    db = SessionLocal()
    try:
        # This seed acts as the platform, and calls the user service with
        # platform_admin authority — the service refuses platform_admin-only
        # changes to anyone else (KAN-97). The services require an actor id
        # and the demo manager does not exist yet, so the creates run with a
        # placeholder that _stamp_actor replaces on the rows once the
        # manager exists (KAN-318). The two `create` audit rows keep it:
        # the audit log is append-only.
        seed_actor_id = uuid.uuid4()
        seed_actor_roles = frozenset({AccessRole.PLATFORM_ADMIN})

        dealership = db.scalar(select(Dealership).where(Dealership.legal_name == DEMO_LEGAL_NAME))
        if dealership is None:
            dealership = dealership_service.create_dealership(
                db,
                data=DealershipCreate(
                    legal_name=DEMO_LEGAL_NAME,
                    franchise_type=FranchiseType.INDEPENDENT,
                    address=DealershipAddress(
                        street="Bahnhofstrasse",
                        house_number="1",
                        postal_code="8001",
                        locality="Zürich",
                        canton="ZH",
                    ),
                    phone="+41441234567",
                    uid="CHE-000.000.000",
                ),
                actor_id=seed_actor_id,
            )
            print(f"Created demo dealership '{DEMO_LEGAL_NAME}' (id={dealership.id}).")
        else:
            print(f"Demo dealership already exists (id={dealership.id}).")

        # KAN-196: the demo Betrieb starts with MFA not enforced (see the
        # module docstring). Only when no auth config exists yet — a later
        # choice by the demo manager is theirs.
        if db.scalar(select(DealershipAuthConfig).where(DealershipAuthConfig.dealership_id == dealership.id)) is None:
            db.add(DealershipAuthConfig(dealership_id=dealership.id, mfa_enforced=False))
            db.commit()
            print("Created the demo dealership's auth config with MFA not enforced.")

        user = db.scalar(select(User).where(User.tenant_id == dealership.id, User.email == DEMO_EMAIL))
        if user is None:
            user = user_service.create_user(
                db,
                dealership_id=dealership.id,
                data=UserCreate(
                    first_name=DEMO_FIRST_NAME,
                    last_name=DEMO_LAST_NAME,
                    email=DEMO_EMAIL,
                    role=UserRole.GM,
                    access_roles=[AccessRole.SALES],
                    is_dealer_manager=True,
                ),
                actor_id=seed_actor_id,
                actor_roles=seed_actor_roles,
            )
            # No invitation is mailed for the demo account (there is nobody to
            # mail); the seed activates it in the invitation's place.
            user = user_service.update_user(
                db,
                user=user,
                data=UserUpdate(status=UserStatus.ACTIVE),
                actor_id=seed_actor_id,
                actor_roles=seed_actor_roles,
            )
            print(f"Created demo user {DEMO_EMAIL}.")
        else:
            print(f"Demo user {DEMO_EMAIL} already exists.")

        credential = db.scalar(select(Credential).where(Credential.user_id == user.id))
        if credential is not None and password_hashing.verify_password(credential.password_hash, password):
            print(f"Demo user {DEMO_EMAIL}'s password already matches DMS_SEED_DEMO_PASSWORD — nothing to do.")
        else:
            # The env var is the source of truth: a rotated demo password takes
            # effect on the next deploy. The same policy-checked path a user's
            # own reset takes; the last five passwords are refused like theirs.
            try:
                identity.set_password(
                    db,
                    user=user,
                    password=password,
                    actor_id=user.id,
                    ip_address="",
                    event=AuthEventKind.PASSWORD_SET,
                )
            except BadRequestError as exc:
                db.rollback()
                print(f"DMS_SEED_DEMO_PASSWORD was not applied: {exc.message}")
                sys.exit(1)
            db.commit()
            print(f"Set demo user {DEMO_EMAIL}'s password from DMS_SEED_DEMO_PASSWORD.")

        # KAN-198 (D-A-04): a new Betrieb starts unlicensed; the demo one
        # holds every module so nothing disappears on a fresh environment
        # (the migration backfill only reaches dealerships that already
        # exist). Granted once the demo manager exists, as that manager —
        # `granted_by` names a real user (KAN-318). Idempotent: an
        # already-licensed Betrieb is left alone.
        if not entitlement_service.list_active_modules(db, dealership.id):
            entitlement_service.set_entitlements(db, dealership=dealership, modules=set(ModuleCode), actor_id=user.id)
            print(f"Licensed demo dealership for every module: {sorted(m.value for m in ModuleCode)}.")

        group = db.get(DealerGroup, dealership.dealer_group_id)
        assert group is not None, "create_dealership always creates or names a dealer group"
        stamped = _stamp_actor(db, actor=user, rows=(group, dealership, user))
        if stamped:
            print(f"Stamped {DEMO_EMAIL} (id={user.id}) as the actor on {', '.join(stamped)}.")

        print(f"Sign in as {DEMO_EMAIL} with the password in DMS_SEED_DEMO_PASSWORD.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
