"""The environment seed: the demo Betrieb (Dealership) and its first dealer
manager, so a fresh environment has somebody who can sign in. No self-serve
signup exists (or should exist). Idempotent — safe to run on every deploy.

render.yaml's startCommand runs this on every staging deploy, docker
compose's on every local `make up` (both piped through `|| true`, so a
missing variable never blocks app startup — Render's free tier has no Shell
to run anything by hand, see the render.yaml header comment). A missing or
invalid variable exits 1 with no side effects; once it is right, the next
deploy seeds.

Two paths, chosen by `DMS_ENVIRONMENT` (KAN-551); any other name — production
included — is refused (exit 1, nothing written): no demo Betrieb is ever
created outside staging and a developer's own machine.

**Staging gets personal, invited users and enforced MFA.** With the identAPI sandbox
connection configured on staging (ADR-113), a shared login would hand
licensed auto-i-dat data to whoever holds it, which the test agreement's
§3/§8 exclude. So the seed creates no shared credential: the first dealer
manager is an **invitation** to `DMS_STAGING_FIRST_MANAGER_EMAIL` (mailed
through the platform sender like every invitation, FR-AU-13), and the demo
Betrieb's `dealership_auth_config.mfa_enforced` is switched on and kept on —
a deploy that finds it off switches it back, audited (`auth_config_set`,
reason `seed_staging_demo`). An existing user with that address is left
alone, never re-invited or reset: an invitation that expired or never
arrived is answered by «Passwort vergessen» with the same address (a reset
reaches an invited user too) — never by a second seed run. Further users
are invited by that manager from Dealer Administration (README › Staging
sign-in). The shared demo login an earlier seed created is retired by
`scripts/retire_staging_shared_demo_user.py` once a personal manager has
signed in with (or enrolled) a second factor. `DMS_SEED_DEMO_PASSWORD` is not
read here.

**A local environment (development, local, test — docker compose, the
desktop preview, cloud sessions) keeps the password demo account**
`demo@nexotec-staging.example`, set from `DMS_SEED_DEMO_PASSWORD` and
reconciled on every run (rotating it needs no manual DB fix), with MFA not
enforced on its Betrieb (KAN-196: a shared local account carries nobody's
authenticator). It reaches no licensed data a developer's own machine does
not already hold, and the screenshots every ticket needs sign in with it.
Nobody else ever sets a password for a user (FR-AU-10); the seed is the
environment's bootstrap, not a user, and writes the credential directly
through the same policy-checked service.

Can also be run directly, e.g. against local dev:

    DMS_SEED_DEMO_PASSWORD='<a-throwaway-password-for-this-run>' \\
        python scripts/seed_staging_demo.py
"""

import os
import sys

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import list_audit_events, record_audit_event
from app.core.auth import AccessRole
from app.core.config import get_settings
from app.core.errors import BadRequestError, UnprocessableEntityError
from app.core.permissions import ModuleCode
from app.db import SessionLocal
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.credential import Credential
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType, Location
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.dealership_settings import DealershipSettings
from app.platform.models.number_range import NumberRange
from app.platform.models.user import User, UserRole, UserStatus
from app.platform.schemas.dealership import DealershipAddress, DealershipCreate
from app.platform.schemas.user import UserCreate
from app.platform.services import dealership as dealership_service
from app.platform.services import entitlement as entitlement_service
from app.platform.services import identity, password_hashing
from app.platform.services import user as user_service
from app.platform.services.mailer import PlatformMailer, get_mailer
from app.platform.services.password_policy import validate_new_password

DEMO_LEGAL_NAME = "Demo Garage AG"
# The local path's shared account. A deployed environment never creates it;
# `retire_staging_shared_demo_user.py` deactivates the one an earlier seed made.
DEMO_EMAIL = "demo@nexotec-staging.example"
DEMO_FIRST_NAME = "Demo"
DEMO_LAST_NAME = "Admin"

# KAN-551: the deployed path's first dealer manager — a person, invited.
FIRST_MANAGER_EMAIL_VAR = "DMS_STAGING_FIRST_MANAGER_EMAIL"
FIRST_MANAGER_FIRST_NAME_VAR = "DMS_STAGING_FIRST_MANAGER_FIRST_NAME"
FIRST_MANAGER_LAST_NAME_VAR = "DMS_STAGING_FIRST_MANAGER_LAST_NAME"
# Used when the name variables are unset; the manager corrects their own
# name in My account › Profile (KAN-201).
DEFAULT_FIRST_MANAGER_FIRST_NAME = "Erster"
DEFAULT_FIRST_MANAGER_LAST_NAME = "Manager"

# The environments that keep the local password account, and the one
# deployment that gets the invitation path. Any other name is refused — an
# unset DMS_ENVIRONMENT reads as `development`, so a deployment must set it,
# as render.yaml does.
LOCAL_ENVIRONMENTS = frozenset({"development", "local", "test"})
STAGING_ENVIRONMENT = "staging"

AUDIT_REASON = "seed_staging_demo"

_ACTOR_COLUMNS = ("created_by", "updated_by")

_ActorRow = DealerGroup | Dealership | User | DealershipSettings | Location | NumberRange


def _environment() -> str:
    return get_settings().environment


def _stamp_actor(
    db: Session,
    *,
    actor: User,
    rows: tuple[_ActorRow, ...],
) -> list[str]:
    """KAN-318: the first manager is the actor of its own group, dealership
    and user row. The first user an environment has cannot exist before its
    dealership does (create_user needs the dealership's id), so the creates
    are system writes (KAN-345) and _claim_system_created hands this run's
    rows to the manager; created_by has no service path at all, so both are
    direct writes. This one repairs what earlier seeds left: every value that names
    no `user` row is replaced, on every run: an environment seeded before
    KAN-318 still holds the random uuid4 the seed used to write, and the
    nightly platform reconciliation (app.platform.reconciliation) would
    report it each night. A value that does name a user — another user's
    later edit, or the shared demo user an earlier seed stamped — is left
    alone; so is null, which names nobody on purpose
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


def _created_with(db: Session, dealership: Dealership) -> tuple[_ActorRow, ...]:
    """The rows `create_dealership` writes with a new Betrieb: its group, the
    Betrieb, its settings, its first location and its number ranges."""

    group = db.get(DealerGroup, dealership.dealer_group_id)
    assert group is not None, "create_dealership always creates or names a dealer group"
    settings_row = db.scalar(select(DealershipSettings).where(DealershipSettings.dealership_id == dealership.id))
    locations = tuple(db.scalars(select(Location).where(Location.tenant_id == dealership.id)).all())
    ranges = tuple(db.scalars(select(NumberRange).where(NumberRange.tenant_id == dealership.id)).all())
    return (group, dealership) + ((settings_row,) if settings_row is not None else ()) + locations + ranges


def _seed_created(db: Session, dealership: Dealership) -> bool:
    """Whether this seed created the Betrieb as a system write (KAN-345): its
    `create` audit row names no actor and gives this seed as the reason. Only
    then are its rows' nulls the seed's own — a Betrieb that existed before
    (staging's, from earlier seeds) also holds rows migrations wrote with a
    null actor on purpose (KAN-204's first locations, KAN-207's ranges)."""

    return any(
        event.action == "create" and event.actor_id is None and event.reason == AUDIT_REASON
        for event in list_audit_events(db, entity_type="dealership", entity_id=dealership.id, tenant_id=dealership.id)
    )


def _claim_system_created(db: Session, *, actor: User, rows: tuple[_ActorRow, ...]) -> list[str]:
    """KAN-345 beside KAN-318: rows this run created as a system write carry
    a null actor, and the first manager takes them over — the same outcome
    KAN-318 gave them, without a placeholder id the audit log would keep.
    Only the rows this run created: a null on any other row names nobody on
    purpose and stays (_stamp_actor). The audit rows keep their null actor
    and the seed's reason; the audit log is append-only. Returns what
    changed."""

    changed: list[str] = []
    for row in rows:
        for column in _ACTOR_COLUMNS:
            if getattr(row, column) is None:
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


def _first_manager_data() -> UserCreate | str:
    """The deployed path's first manager from the environment, validated
    before anything is written; a string is the refusal to print."""

    email = (os.environ.get(FIRST_MANAGER_EMAIL_VAR) or "").strip()
    if not email:
        return (
            f"{FIRST_MANAGER_EMAIL_VAR} is required on staging — the e-mail address the first "
            "dealer manager's invitation goes to (KAN-551). No shared demo login is created any more."
        )
    try:
        return UserCreate(
            first_name=(os.environ.get(FIRST_MANAGER_FIRST_NAME_VAR) or "").strip() or DEFAULT_FIRST_MANAGER_FIRST_NAME,
            last_name=(os.environ.get(FIRST_MANAGER_LAST_NAME_VAR) or "").strip() or DEFAULT_FIRST_MANAGER_LAST_NAME,
            email=email,
            role=UserRole.GM,
            access_roles=[AccessRole.SALES],
            is_dealer_manager=True,
        )
    except ValidationError as exc:
        fields = ", ".join(sorted({str(error["loc"][0]) for error in exc.errors() if error.get("loc")}))
        return f"The first manager's variables are not valid ({fields}); nothing was written."


def _ensure_dealership(db: Session) -> tuple[Dealership, bool]:
    """The demo Betrieb, and whether this run created it. Created as a system
    write (KAN-345): no user exists yet, so the rows and the `create` audit
    row carry no actor, and the audit row's reason names this seed."""

    dealership = db.scalar(select(Dealership).where(Dealership.legal_name == DEMO_LEGAL_NAME))
    if dealership is not None:
        print(f"Demo dealership already exists (id={dealership.id}).")
        return dealership, False
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
        actor_id=None,
        system_reason=AUDIT_REASON,
    )
    print(f"Created demo dealership '{DEMO_LEGAL_NAME}' (id={dealership.id}).")
    return dealership, True


def _finish(db: Session, *, dealership: Dealership, actor: User, created: tuple[_ActorRow, ...] = ()) -> None:
    """The steps both paths share once the Betrieb has its manager.
    `created` are the rows this run wrote as a system write (KAN-345):
    their null actor is the seed's, so the manager takes them over."""

    # KAN-198 (D-A-04): a new Betrieb starts unlicensed; the demo one holds
    # every module so nothing disappears on a fresh environment (the
    # migration backfill only reaches dealerships that already exist).
    # Granted as the first manager — `granted_by` names a real user
    # (KAN-318). Idempotent: an already-licensed Betrieb is left alone.
    if not entitlement_service.list_active_modules(db, dealership.id):
        entitlement_service.set_entitlements(db, dealership=dealership, modules=set(ModuleCode), actor_id=actor.id)
        print(f"Licensed demo dealership for every module: {sorted(m.value for m in ModuleCode)}.")

    group = db.get(DealerGroup, dealership.dealer_group_id)
    assert group is not None, "create_dealership always creates or names a dealer group"
    # KAN-203: create_dealership writes the settings row with the same
    # actor as the Betrieb, so it is stamped with the rest.
    settings_row = db.scalar(select(DealershipSettings).where(DealershipSettings.dealership_id == dealership.id))
    # KAN-204: so does the first location (FR-A-03), made from the seat.
    locations = tuple(db.scalars(select(Location).where(Location.tenant_id == dealership.id)).all())
    # KAN-207: and its number ranges, seeded with it.
    ranges = tuple(db.scalars(select(NumberRange).where(NumberRange.tenant_id == dealership.id)).all())
    rows = (group, dealership, actor) + ((settings_row,) if settings_row is not None else ()) + locations + ranges
    claimed = _claim_system_created(db, actor=actor, rows=created)
    if claimed:
        print(f"Stamped the first manager (id={actor.id}) as the actor on {', '.join(claimed)}.")
    stamped = _stamp_actor(db, actor=actor, rows=rows)
    if stamped:
        print(f"Stamped the first manager (id={actor.id}) as the actor on {', '.join(stamped)}.")


# --- the staging path (KAN-551) ----------------------------------------------------------------


def _enforce_mfa(db: Session, *, dealership: Dealership) -> None:
    """The demo Betrieb enforces a second factor for every user, on every
    deploy: the test agreement's §3/§8 do not leave it to whoever manages
    the demo Betrieb. A system write (null actor, KAN-243), audited like the
    manager's own switch."""

    config = db.scalar(select(DealershipAuthConfig).where(DealershipAuthConfig.dealership_id == dealership.id))
    if config is None:
        db.add(DealershipAuthConfig(dealership_id=dealership.id, mfa_enforced=True))
        db.commit()
        print("Created the demo dealership's auth config with MFA enforced.")
        return
    if config.mfa_enforced:
        print("The demo dealership already enforces MFA.")
        return
    record_audit_event(
        db,
        entity_type="dealership",
        entity_id=dealership.id,
        tenant_id=dealership.id,
        action="auth_config_set",
        actor_id=None,
        before={"mfa_enforced": False},
        after={"mfa_enforced": True},
        reason=AUDIT_REASON,
    )
    config.mfa_enforced = True
    config.updated_by = None
    config.version += 1
    db.commit()
    print("Switched MFA enforcement on for the demo dealership (it was off).")


def _ensure_first_manager(
    db: Session,
    mailer: PlatformMailer,
    *,
    dealership: Dealership,
    data: UserCreate,
) -> tuple[User | None, bool]:
    """Invites the first manager once. Whoever already holds the address is
    left exactly as they are — never re-invited, never reset — so a deploy
    can never take away an account that works. Returns the manager when they
    are homed in the demo Betrieb, else None, and whether this run created
    them."""

    existing = identity.find_user_by_email(db, str(data.email))
    if existing is not None:
        if existing.tenant_id != dealership.id:
            print(
                f"{FIRST_MANAGER_EMAIL_VAR} names a user of another Betrieb; nothing was changed for them. "
                "Give them a membership of the demo Betrieb from Dealer Administration, or set another address."
            )
            return None, False
        print(f"The first manager already exists (status {existing.status.value}); left as they are.")
        return existing, False

    # The user and the invitation token in one transaction, as the Users
    # screen's POST writes them (FR-A-09): create_user flushes, the
    # invitation adds the token, the commit below is the only one. The seed
    # acts as the platform (KAN-97), as a system write (KAN-345): the
    # `create` audit row names no actor and gives this seed as its reason;
    # _finish stamps the manager on the row itself (KAN-318).
    user = user_service.create_user(
        db,
        dealership_id=dealership.id,
        data=data,
        actor_id=None,
        actor_roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        commit=False,
        system_reason=AUDIT_REASON,
    )
    identity.issue_invitation(db, mailer, user=user, actor_id=None, ip_address="")
    db.commit()
    db.refresh(user)
    print(
        f"Invited the first manager (id={user.id}); the invitation went to the address in "
        f"{FIRST_MANAGER_EMAIL_VAR}. A failed delivery is a mail_delivery_failed auth event."
    )
    return user, True


def _main_deployed() -> None:
    data = _first_manager_data()
    if isinstance(data, str):
        print(data)
        sys.exit(1)
    if os.environ.get("DMS_SEED_DEMO_PASSWORD"):
        print("DMS_SEED_DEMO_PASSWORD is set but not read on staging (KAN-551); delete it.")
    mailer = get_mailer()

    db = SessionLocal()
    try:
        dealership, dealership_created = _ensure_dealership(db)
        _enforce_mfa(db, dealership=dealership)
        manager, manager_created = _ensure_first_manager(db, mailer, dealership=dealership, data=data)
        if manager is None:
            print(
                "No first manager in the demo Betrieb: entitlements wait for one, and the Betrieb's rows stay "
                "system writes until the run that invites one."
            )
            return
        # The Betrieb's own rows are claimed when this run created it, or
        # invited its first manager into a Betrieb an earlier run of this seed
        # created as a system write (one that found no manager to invite).
        claim = dealership_created or (manager_created and _seed_created(db, dealership))
        created = _created_with(db, dealership) if claim else ()
        _finish(db, dealership=dealership, actor=manager, created=created + ((manager,) if manager_created else ()))
        print("Sign-in: the first manager accepts the mailed invitation and enrols a second factor.")
    finally:
        db.close()


# --- the local path ----------------------------------------------------------------------------


def _main_local() -> None:
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
        # changes to anyone else (KAN-97). No user exists before the demo
        # manager, so the creates are system writes (KAN-345, Anto
        # 2026-11-03, option A): their audit rows name no actor and give
        # this seed as the reason; _finish stamps the manager on the rows
        # themselves once they exist (KAN-318).
        seed_actor_roles = frozenset({AccessRole.PLATFORM_ADMIN})

        dealership, dealership_created = _ensure_dealership(db)
        user_created = False

        # KAN-196: the local demo Betrieb starts with MFA not enforced (see
        # the module docstring). Only when no auth config exists yet — a
        # later choice in Dealer Administration is the developer's.
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
                actor_id=None,
                actor_roles=seed_actor_roles,
                system_reason=AUDIT_REASON,
            )
            # No invitation is mailed for the demo account (there is nobody to
            # mail); the seed activates it in the invitation's place — on the
            # row, as the one bootstrap exception: `update_user` refuses
            # `invited → active` for everyone else (KAN-200, Users → Lifecycle),
            # since accepting the invitation is the only way in. Audited like
            # the status change the acceptance would have made.
            record_audit_event(
                db,
                entity_type="user",
                entity_id=user.id,
                tenant_id=user.tenant_id,
                action="update",
                actor_id=None,
                before={"status": user.status.value},
                after={"status": UserStatus.ACTIVE.value},
                reason=AUDIT_REASON,
            )
            user.status = UserStatus.ACTIVE
            user.updated_by = None
            user.version += 1
            db.commit()
            db.refresh(user)
            user_created = True
            print(f"Created demo user {DEMO_EMAIL}.")
        else:
            print(f"Demo user {DEMO_EMAIL} already exists.")

        credential = db.scalar(select(Credential).where(Credential.user_id == user.id))
        if credential is not None and password_hashing.verify_password(credential.password_hash, password):
            print(f"Demo user {DEMO_EMAIL}'s password already matches DMS_SEED_DEMO_PASSWORD — nothing to do.")
        else:
            # The env var is the source of truth: a rotated demo password takes
            # effect on the next run. The same policy-checked path a user's
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

        # As on the deployed path: claimed when this run created the Betrieb,
        # or created its manager in a Betrieb this seed created earlier.
        claim = dealership_created or (user_created and _seed_created(db, dealership))
        created: tuple[_ActorRow, ...] = (_created_with(db, dealership) if claim else ()) + (
            (user,) if user_created else ()
        )
        _finish(db, dealership=dealership, actor=user, created=created)
        print(f"Sign in as {DEMO_EMAIL} with the password in DMS_SEED_DEMO_PASSWORD.")
    finally:
        db.close()


def main() -> None:
    environment = _environment()
    if environment in LOCAL_ENVIRONMENTS:
        _main_local()
    elif environment == STAGING_ENVIRONMENT:
        _main_deployed()
    else:
        print(f"DMS_ENVIRONMENT={environment}: this seed runs on staging and local environments only; nothing written.")
        sys.exit(1)


if __name__ == "__main__":
    main()
