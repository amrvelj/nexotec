"""KAN-142: scripts/seed_staging_demo.py runs on every deploy, wrapped in
`|| true` (render.yaml, docker-compose.yml), so a failure there brings the
stack up with no demo user and no error. KAN-97 made it raise TypeError on
every fresh stack and only the review noticed. These tests run the real
main() against the test database: create, then no-op, then reconcile.

KAN-551: the seed has two paths. The tests up to the KAN-551 section run the
local one (`DMS_ENVIRONMENT` development: the password demo account); the
KAN-551 section runs the deployed one (staging: an invited first manager,
MFA enforced, no shared credential) and scripts/retire_staging_shared_demo_user.py.
"""

import uuid

import pyotp
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage
from app.core.permissions import ModuleCode
from app.customer.models.customer import Customer
from app.platform import reconciliation as platform_reconciliation
from app.platform.models.auth_event import AuthEvent
from app.platform.models.auth_session import AuthSession
from app.platform.models.auth_token import AuthToken
from app.platform.models.credential import Credential
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType, Location
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.user import User, UserRole, UserStatus
from app.platform.schemas.dealership import DealershipAddress, DealershipCreate
from app.platform.schemas.user import UserCreate
from app.platform.services import dealership as dealership_service
from app.platform.services import user as user_service
from scripts import retire_staging_shared_demo_user, seed_staging_demo
from tests.identity_support import (
    STRONG_PASSWORD,
    RecordingMailer,
    cookie_headers,
    create_dealership,
    create_user,
    last_mail_to,
    token_in,
)
from tests.token_support import dealer_group_of

PASSWORD = "staging-horse-secret-2026"
OTHER_PASSWORD = "rotated-horse-secret-2026"


@pytest.fixture()
def seed(engine, monkeypatch):
    """main() opens its own app.db.SessionLocal; point it at the test engine."""
    monkeypatch.setattr(
        seed_staging_demo,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False),
    )

    monkeypatch.setattr(seed_staging_demo, "_environment", lambda: "development")

    def run(password: str = PASSWORD) -> None:
        monkeypatch.setenv("DMS_SEED_DEMO_PASSWORD", password)
        seed_staging_demo.main()

    return run


def _login(client, password: str):
    return client.post("/v1/auth/login", json={"email": seed_staging_demo.DEMO_EMAIL, "password": password})


def _counts(db_session) -> tuple[int, int, int, int]:
    return (
        db_session.scalar(select(func.count()).select_from(Dealership)),
        db_session.scalar(select(func.count()).select_from(User)),
        db_session.scalar(select(func.count()).select_from(AuditEvent)),
        db_session.scalar(select(func.count()).select_from(OutboxMessage)),
    )


def test_first_run_creates_the_demo_dealership_and_user_who_can_sign_in(seed, db_session, client):
    seed()

    dealership = db_session.scalar(select(Dealership).where(Dealership.legal_name == seed_staging_demo.DEMO_LEGAL_NAME))
    assert dealership is not None
    user = db_session.scalar(select(User).where(User.email == seed_staging_demo.DEMO_EMAIL))
    assert user is not None
    assert user.tenant_id == dealership.id
    assert user.status == UserStatus.ACTIVE
    assert user.auth_identity_id == f"local:{user.id}"
    # KAN-196: a real local-password login, and no MFA enrolment demanded of
    # the shared demo account.
    response = _login(client, PASSWORD)
    assert response.status_code == 200, response.text
    assert response.json()["mfaRequired"] is False
    assert response.json()["session"]["mfaEnrolmentRequired"] is False
    assert _login(client, OTHER_PASSWORD).status_code == 401


def _demo_user(db_session) -> User:
    user = db_session.scalar(select(User).where(User.email == seed_staging_demo.DEMO_EMAIL))
    assert user is not None
    return user


def test_second_run_with_the_same_password_writes_nothing(seed, db_session):
    seed()
    before = _counts(db_session)
    user = _demo_user(db_session)
    user_before = (user.version, user.updated_at)
    db_session.rollback()

    seed()

    assert _counts(db_session) == before
    # Not just no new rows: the existing user is not rewritten either.
    user = _demo_user(db_session)
    db_session.refresh(user)
    assert (user.version, user.updated_at) == user_before


def test_a_rotated_password_is_reconciled_onto_the_existing_user(seed, db_session, client):
    seed(PASSWORD)
    dealerships_before, users_before, _, _ = _counts(db_session)
    db_session.rollback()

    seed(OTHER_PASSWORD)

    dealerships_after, users_after, _, _ = _counts(db_session)
    assert (dealerships_after, users_after) == (dealerships_before, users_before)
    assert _login(client, OTHER_PASSWORD).status_code == 200
    assert _login(client, PASSWORD).status_code == 401


def test_without_the_password_it_exits_1_and_writes_nothing(seed, monkeypatch, db_session):
    # Through the seed fixture, so a write before the check would land in the
    # test database and be counted, not in app.db's own database.
    monkeypatch.delenv("DMS_SEED_DEMO_PASSWORD", raising=False)
    before = _counts(db_session)
    db_session.rollback()

    with pytest.raises(SystemExit) as exited:
        seed_staging_demo.main()

    assert exited.value.code == 1
    assert _counts(db_session) == before


@pytest.mark.parametrize("password", ["short-1234", "demo-garage-is-the-best-2026", "the-admin-password-2026"])
def test_a_password_the_policy_refuses_exits_1_before_anything_is_written(seed, db_session, password):
    """FR-AU-04/06 apply to the demo account too: too short, or carrying the
    Betrieb's or the user's name. Checked before the first write, so a wrong
    env var leaves no half-seeded environment."""

    before = _counts(db_session)
    db_session.rollback()

    with pytest.raises(SystemExit) as exited:
        seed(password)

    assert exited.value.code == 1
    assert _counts(db_session) == before


# --- KAN-318: the rows the seed creates name a real actor ------------------------


def _seeded_rows(db_session) -> tuple[DealerGroup, Dealership, User]:
    dealership = db_session.scalar(select(Dealership).where(Dealership.legal_name == seed_staging_demo.DEMO_LEGAL_NAME))
    assert dealership is not None
    group = db_session.get(DealerGroup, dealership.dealer_group_id)
    assert group is not None
    return group, dealership, _demo_user(db_session)


def test_the_seed_stamps_the_demo_user_as_the_actor_on_everything_it_created(seed, db_session):
    """KAN-318 — the seed used to write a fresh uuid4 as created_by / updated_by,
    which named no user. The demo manager is the only user a fresh
    environment has, so it is the actor of its own group, dealership and row."""

    seed()

    group, dealership, user = _seeded_rows(db_session)
    for row in (group, dealership, user):
        assert (row.created_by, row.updated_by) == (user.id, user.id), row


def test_the_seed_licenses_the_demo_dealership_for_every_module_as_the_demo_user(seed, db_session):
    """KAN-198 — a new Betrieb starts unlicensed and the migration backfill
    only reaches dealerships that already exist, so a fresh environment would
    lose sales, stock and valuations without this step. The granter names the
    demo manager (KAN-318), and a second run grants nothing again."""

    seed()
    _, dealership, user = _seeded_rows(db_session)
    rows = db_session.scalars(select(ModuleEntitlement).where(ModuleEntitlement.dealership_id == dealership.id)).all()
    assert sorted(row.module.value for row in rows) == sorted(m.value for m in ModuleCode)
    assert {row.granted_by for row in rows} == {user.id}
    assert all(row.valid_to is None for row in rows)
    assert dealership.entitlements_version == 1

    seed()
    db_session.expire_all()
    assert dealership.entitlements_version == 1


def test_the_seeded_rows_pass_platform_reconciliation(seed, db_session):
    """The nightly job KAN-318 adds must find nothing on a freshly seeded
    environment — otherwise it alarms from its first night."""

    seed()

    assert platform_reconciliation.run(db_session).orphans_found == 0


def test_a_rerun_restamps_an_actor_that_names_no_user_and_leaves_a_real_one_alone(seed, db_session):
    """An environment seeded before KAN-318 holds the random actor; the seed
    runs on every deploy, so the next run replaces it. A value that names a
    real user — another user's later edit — is not the seed's to overwrite."""

    seed()
    group, dealership, user = _seeded_rows(db_session)
    other = user_service.create_user(
        db_session,
        dealership_id=dealership.id,
        data=UserCreate(
            first_name="Other",
            last_name="Manager",
            email="other@nexotec-staging.example",
            role=UserRole.GM,
            access_roles=[AccessRole.SALES],
            is_dealer_manager=True,
        ),
        actor_id=user.id,
        actor_roles=frozenset({AccessRole.PLATFORM_ADMIN}),
    )
    pre_kan318_actor = uuid.uuid4()
    db_session.execute(update(Dealership).where(Dealership.id == dealership.id).values(created_by=pre_kan318_actor))
    db_session.execute(update(DealerGroup).where(DealerGroup.id == group.id).values(updated_by=other.id))
    db_session.commit()
    db_session.expire_all()

    seed()

    group, dealership, user = _seeded_rows(db_session)
    assert (dealership.created_by, dealership.updated_by) == (user.id, user.id)
    assert (group.created_by, group.updated_by) == (user.id, other.id)
    assert platform_reconciliation.run(db_session).orphans_found == 0


# --- KAN-345: the seed's audit rows name no placeholder actor -------------------


def _seed_audit_rows(db_session, dealership: Dealership) -> list[tuple[str, str, uuid.UUID | None, str | None]]:
    rows = db_session.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == dealership.id).order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    return [(a.entity_type, a.action, a.actor_id, a.reason) for a in rows]


def test_the_local_seeds_create_audit_rows_are_system_writes(seed, db_session):
    """KAN-345 (Anto, 2026-11-03, option A): the dealership `create`, the user
    `create` and the activation `update` used to carry a random uuid4 that
    named nobody, kept forever by the append-only log. They now name no actor
    and give the seed as the reason; the rows themselves still name the demo
    manager (KAN-318)."""

    seed()

    group, dealership, user = _seeded_rows(db_session)
    audit = _seed_audit_rows(db_session, dealership)
    assert ("dealership", "create", None, seed_staging_demo.AUDIT_REASON) in audit
    assert ("user", "create", None, seed_staging_demo.AUDIT_REASON) in audit
    assert ("user", "update", None, seed_staging_demo.AUDIT_REASON) in audit
    # The first location create_dealership writes names the seed too (review).
    [location_create] = [(actor, reason) for entity, action, actor, reason in audit if (entity, action) == ("location", "create")]
    assert location_create[0] is None and location_create[1].startswith(f"{seed_staging_demo.AUDIT_REASON} — FR-A-03")
    named = {actor for _, _, actor, _ in audit if actor is not None}
    assert named <= {user.id}, "an audit actor names someone who is not a user"
    for row in (group, dealership, user):
        assert (row.created_by, row.updated_by) == (user.id, user.id), row
    assert platform_reconciliation.run(db_session).orphans_found == 0


def test_the_create_services_refuse_a_write_with_no_actor_and_no_reason(db_session):
    """The system path is explicit: no actor without a reason, no reason
    beside an actor — a programming error, never a silent system write."""

    dealership_id = uuid.uuid4()  # refused before anything is read or written
    data = UserCreate(
        first_name="No",
        last_name="Actor",
        email="no.actor@example.com",
        role=UserRole.GM,
        access_roles=[AccessRole.SALES],
    )
    with pytest.raises(ValueError, match="system_reason"):
        user_service.create_user(
            db_session, dealership_id=dealership_id, data=data, actor_id=None, actor_roles=frozenset()
        )
    with pytest.raises(ValueError, match="system_reason"):
        user_service.create_user(
            db_session,
            dealership_id=dealership_id,
            data=data,
            actor_id=uuid.uuid4(),
            actor_roles=frozenset(),
            system_reason="seed_staging_demo",
        )
    with pytest.raises(ValueError, match="system_reason"):
        dealership_service.create_dealership(
            db_session,
            data=DealershipCreate(
                legal_name="Ohne Akteur AG",
                franchise_type=FranchiseType.INDEPENDENT,
                address=DealershipAddress(
                    street="Bahnhofstrasse", house_number="2", postal_code="8001", locality="Zürich", canton="ZH"
                ),
                phone="+41441234567",
                uid="CHE-000.000.000",
            ),
            actor_id=None,
            system_reason="  ",
        )
    assert db_session.scalar(select(func.count()).select_from(User)) == 0
    assert db_session.scalar(select(func.count()).select_from(Dealership)) == 0


# --- KAN-551: a deployed environment has no shared login ------------------------

MANAGER_EMAIL = "first.manager@nexotec-staging.example"


def _enrol_totp(client, cookie: str) -> None:
    """Enrols and confirms an authenticator in the session `cookie` names —
    the step the enrolment screen asks for."""

    begun = client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(cookie))
    assert begun.status_code == 200, begun.text
    confirmed = client.post(
        "/v1/auth/mfa/totp/confirm",
        json={"code": pyotp.TOTP(begun.json()["secret"]).now()},
        headers=cookie_headers(cookie),
    )
    assert confirmed.status_code == 200, confirmed.text


@pytest.fixture()
def deployed_seed(engine, monkeypatch, mailbox):
    """The deployed path (DMS_ENVIRONMENT staging) against the test engine,
    its invitation recorded in the same `mailbox` the API's mails land in."""

    monkeypatch.setattr(
        seed_staging_demo,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False),
    )
    monkeypatch.setattr(seed_staging_demo, "get_mailer", lambda: RecordingMailer(sent=mailbox))
    monkeypatch.delenv("DMS_SEED_DEMO_PASSWORD", raising=False)

    def run(email: str | None = MANAGER_EMAIL, environment: str = "staging") -> None:
        monkeypatch.setattr(seed_staging_demo, "_environment", lambda: environment)
        if email is None:
            monkeypatch.delenv(seed_staging_demo.FIRST_MANAGER_EMAIL_VAR, raising=False)
        else:
            monkeypatch.setenv(seed_staging_demo.FIRST_MANAGER_EMAIL_VAR, email)
        seed_staging_demo.main()

    return run


def _demo_dealership(db_session) -> Dealership:
    dealership = db_session.scalar(select(Dealership).where(Dealership.legal_name == seed_staging_demo.DEMO_LEGAL_NAME))
    assert dealership is not None
    return dealership


def _mfa_enforced(db_session, dealership: Dealership) -> bool:
    config = db_session.scalar(select(DealershipAuthConfig).where(DealershipAuthConfig.dealership_id == dealership.id))
    assert config is not None
    return config.mfa_enforced


def _all_counts(db_session) -> tuple[int, ...]:
    return _counts(db_session) + (
        db_session.scalar(select(func.count()).select_from(AuthToken)),
        db_session.scalar(select(func.count()).select_from(Credential)),
        db_session.scalar(select(func.count()).select_from(AuthEvent)),
    )


def test_a_deployed_first_run_invites_the_first_manager_and_enforces_mfa(deployed_seed, db_session, mailbox):
    """No shared credential anywhere: the only user is the invited first
    manager, holding no password, and the Betrieb enforces MFA."""

    deployed_seed()

    dealership = _demo_dealership(db_session)
    users = db_session.scalars(select(User).where(User.tenant_id == dealership.id)).all()
    assert [(u.email, u.status, u.is_dealer_manager) for u in users] == [(MANAGER_EMAIL, UserStatus.INVITED, True)]
    assert db_session.scalar(select(func.count()).select_from(Credential)) == 0
    assert db_session.scalar(select(User).where(User.email == seed_staging_demo.DEMO_EMAIL)) is None
    assert _mfa_enforced(db_session, dealership) is True
    assert [mail.to for mail in mailbox] == [MANAGER_EMAIL]
    assert "/invitations/accept?token=" in mailbox[0].text
    # The first manager is the actor of what the seed created and the granter
    # of the licences (KAN-318, KAN-198), so reconciliation finds nothing.
    manager = users[0]
    granted_by = db_session.scalars(
        select(ModuleEntitlement.granted_by).where(ModuleEntitlement.dealership_id == dealership.id)
    ).all()
    assert set(granted_by) == {manager.id}
    assert (dealership.created_by, manager.created_by) == (manager.id, manager.id)
    assert platform_reconciliation.run(db_session).orphans_found == 0
    # KAN-345: the two `create` audit rows are system writes, not a placeholder.
    audit = _seed_audit_rows(db_session, dealership)
    assert ("dealership", "create", None, seed_staging_demo.AUDIT_REASON) in audit
    assert ("user", "create", None, seed_staging_demo.AUDIT_REASON) in audit
    assert {actor for _, _, actor, _ in audit if actor is not None} <= {manager.id}


def test_a_second_deployed_run_writes_nothing_and_mails_nobody(deployed_seed, db_session, mailbox):
    deployed_seed()
    before = _all_counts(db_session)
    manager = db_session.scalar(select(User).where(User.email == MANAGER_EMAIL))
    assert manager is not None
    manager_before = (manager.version, manager.updated_at, manager.invited_at)
    db_session.rollback()

    deployed_seed()

    assert _all_counts(db_session) == before
    assert len(mailbox) == 1
    db_session.expire_all()
    manager = db_session.scalar(select(User).where(User.email == MANAGER_EMAIL))
    assert (manager.version, manager.updated_at, manager.invited_at) == manager_before


def test_an_accepted_first_manager_is_never_reinvited_or_reset_by_a_deploy(deployed_seed, db_session, client, mailbox):
    """The lock-out the ticket warns about: a deploy after the manager set a
    password must leave the account working exactly as it is."""

    deployed_seed()
    accepted = client.post(
        "/v1/auth/invitations/accept", json={"token": token_in(mailbox[0]), "password": STRONG_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    credential = db_session.scalar(select(Credential))
    hash_before = credential.password_hash
    tokens_before = db_session.scalar(select(func.count()).select_from(AuthToken))
    db_session.rollback()

    deployed_seed()

    db_session.expire_all()
    manager = db_session.scalar(select(User).where(User.email == MANAGER_EMAIL))
    assert manager.status == UserStatus.ACTIVE
    assert db_session.scalar(select(Credential)).password_hash == hash_before
    assert db_session.scalar(select(func.count()).select_from(AuthToken)) == tokens_before
    assert len(mailbox) == 1
    login = client.post("/v1/auth/login", json={"email": MANAGER_EMAIL, "password": STRONG_PASSWORD})
    assert login.status_code == 200, login.text


def test_the_invited_manager_signs_in_and_is_sent_to_enrol_a_second_factor(deployed_seed, client, mailbox):
    """Exit criterion 1's flow, in the test client: the invitation's link,
    a password, then the MFA enrolment every screen waits for."""

    deployed_seed()

    accepted = client.post(
        "/v1/auth/invitations/accept", json={"token": token_in(mailbox[0]), "password": STRONG_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["session"]["mfaEnrolmentRequired"] is True
    cookie = accepted.cookies.get("dms_session")
    refused = client.get("/v1/me", headers=cookie_headers(cookie))
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["mfaEnrolmentRequired"] is True
    _enrol_totp(client, cookie)
    assert client.get("/v1/me", headers=cookie_headers(cookie)).status_code == 200


@pytest.mark.parametrize("email", [None, "", "not-an-address"])
def test_a_deployed_run_without_a_valid_first_manager_address_exits_1_and_writes_nothing(
    deployed_seed, db_session, mailbox, email
):
    before = _all_counts(db_session)
    db_session.rollback()

    with pytest.raises(SystemExit) as exited:
        deployed_seed(email=email)

    assert exited.value.code == 1
    assert _all_counts(db_session) == before
    assert mailbox == []


@pytest.mark.parametrize("environment", ["production", "prod-eu", "Staging"])
def test_any_environment_but_staging_and_the_local_ones_is_refused(deployed_seed, db_session, mailbox, environment):
    """No demo Betrieb outside staging and a developer's machine: production
    (or any unknown name) exits 1 before anything is written."""

    before = _all_counts(db_session)
    db_session.rollback()

    with pytest.raises(SystemExit) as exited:
        deployed_seed(environment=environment)

    assert exited.value.code == 1
    assert _all_counts(db_session) == before
    assert mailbox == []


def test_a_deployed_run_ignores_the_demo_password(deployed_seed, monkeypatch, db_session):
    monkeypatch.setenv("DMS_SEED_DEMO_PASSWORD", PASSWORD)

    deployed_seed()

    assert db_session.scalar(select(User).where(User.email == seed_staging_demo.DEMO_EMAIL)) is None
    assert db_session.scalar(select(func.count()).select_from(Credential)) == 0


def test_staging_seeded_before_kan551_gets_mfa_switched_on_and_its_shared_user_left_for_the_retire_script(
    seed, deployed_seed, db_session, client
):
    """The staging database as it is today: the shared demo user with a
    password and MFA off. The deploy switches MFA on (audited, a system
    write), invites the first manager and touches the shared user not at
    all — retiring it is the retire script's, once a personal login works."""

    seed()
    dealership = _demo_dealership(db_session)
    shared = _demo_user(db_session)
    shared_before = (shared.status, shared.version)
    assert _mfa_enforced(db_session, dealership) is False
    db_session.rollback()

    deployed_seed()

    db_session.expire_all()
    assert _mfa_enforced(db_session, dealership) is True
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "auth_config_set")).all()
    assert [(a.entity_id, a.actor_id, a.reason, a.before, a.after) for a in audit] == [
        (dealership.id, None, "seed_staging_demo", {"mfa_enforced": False}, {"mfa_enforced": True})
    ]
    shared = _demo_user(db_session)
    assert (shared.status, shared.version) == shared_before
    manager = db_session.scalar(select(User).where(User.email == MANAGER_EMAIL))
    assert manager is not None and manager.tenant_id == dealership.id
    # The rows the old seed stamped with the shared user keep it: it names a user.
    assert dealership.created_by == shared.id
    assert platform_reconciliation.run(db_session).orphans_found == 0
    # The shared login now owes a second factor before any screen opens.
    login = _login(client, PASSWORD)
    assert login.status_code == 200, login.text
    assert login.json()["session"]["mfaEnrolmentRequired"] is True

    # A manager who switches it off again gets it back on the next deploy.
    db_session.execute(
        update(DealershipAuthConfig)
        .where(DealershipAuthConfig.dealership_id == dealership.id)
        .values(mfa_enforced=False)
    )
    db_session.commit()
    deployed_seed()
    db_session.expire_all()
    assert _mfa_enforced(db_session, dealership) is True


def test_an_address_held_by_another_betriebs_user_is_left_alone(deployed_seed, db_session, client, mailbox):
    other_dealership = create_dealership(client)
    other = create_user(client, other_dealership, email=MANAGER_EMAIL)
    mails_before = len(mailbox)
    other_row = db_session.get(User, uuid.UUID(other["id"]))
    other_before = (other_row.tenant_id, other_row.version, other_row.invited_at)
    db_session.rollback()

    deployed_seed()

    db_session.expire_all()
    dealership = _demo_dealership(db_session)
    assert _mfa_enforced(db_session, dealership) is True
    assert db_session.scalars(select(User).where(User.tenant_id == dealership.id)).all() == []
    other_row = db_session.get(User, uuid.UUID(other["id"]))
    assert (other_row.tenant_id, other_row.version, other_row.invited_at) == other_before
    assert len(mailbox) == mails_before


def test_a_manager_invited_on_a_later_deploy_takes_over_the_rows_the_first_deploy_left(
    deployed_seed, db_session, client, mailbox
):
    """KAN-345 (review): a first deploy that finds the address held by another
    Betrieb's user creates the demo Betrieb as a system write and invites
    nobody; the deploy that does invite the first manager hands the Betrieb's
    rows to them, as a single first run would (KAN-318)."""

    other_dealership = create_dealership(client)
    create_user(client, other_dealership, email=MANAGER_EMAIL)

    deployed_seed()
    db_session.expire_all()
    dealership = _demo_dealership(db_session)
    assert (dealership.created_by, dealership.updated_by) == (None, None)

    deployed_seed(email="second.manager@nexotec-staging.example")

    db_session.expire_all()
    group, dealership = db_session.get(DealerGroup, dealership.dealer_group_id), _demo_dealership(db_session)
    manager = db_session.scalar(select(User).where(User.email == "second.manager@nexotec-staging.example"))
    assert manager is not None and manager.tenant_id == dealership.id
    for row in (group, dealership, manager):
        assert row.created_by == manager.id, row
    # (No reconciliation run here: the other Betrieb the API helpers made
    # names a token's random actor, which is the fixture's, not the seed's.)


def test_a_first_manager_invited_into_a_betrieb_the_seed_did_not_create_leaves_its_null_actors(
    deployed_seed, db_session, mailbox
):
    """KAN-345 (second review): staging's demo Betrieb predates the system
    path, and migrations wrote some of its rows with a null actor on purpose
    (KAN-204's first location). Inviting its first manager stamps the manager
    on their own row only, never on those nulls."""

    dealership = dealership_service.create_dealership(
        db_session,
        data=DealershipCreate(
            legal_name=seed_staging_demo.DEMO_LEGAL_NAME,
            franchise_type=FranchiseType.INDEPENDENT,
            address=DealershipAddress(
                street="Bahnhofstrasse", house_number="1", postal_code="8001", locality="Zürich", canton="ZH"
            ),
            phone="+41441234567",
            uid="CHE-000.000.000",
        ),
        actor_id=uuid.uuid4(),  # an earlier seed's placeholder: not this seed's system write
    )
    [location] = db_session.scalars(select(Location).where(Location.tenant_id == dealership.id)).all()
    location.created_by = location.updated_by = None  # as a migration's backfill writes it
    db_session.commit()

    deployed_seed()

    db_session.expire_all()
    manager = db_session.scalar(select(User).where(User.email == MANAGER_EMAIL))
    assert manager is not None and manager.created_by == manager.id
    location = db_session.get(Location, location.id)
    assert (location.created_by, location.updated_by) == (None, None)
    # The earlier placeholder on the Betrieb is still repaired (KAN-318's restamp).
    assert db_session.get(Dealership, dealership.id).created_by == manager.id


# --- KAN-551: retiring the shared demo login --------------------------------------


@pytest.fixture()
def retire(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def run(*, commit: bool) -> int:
        db = factory()
        try:
            return retire_staging_shared_demo_user.run(db, commit=commit, session_factory=factory)
        finally:
            db.close()

    return run


def test_the_retire_script_names_the_seeds_own_demo_betrieb_and_address():
    assert retire_staging_shared_demo_user.DEMO_LEGAL_NAME == seed_staging_demo.DEMO_LEGAL_NAME
    assert retire_staging_shared_demo_user.DEMO_EMAIL == seed_staging_demo.DEMO_EMAIL


def _personal_manager_signs_in_with_mfa(client, mailbox) -> None:
    accepted = client.post(
        "/v1/auth/invitations/accept",
        json={"token": token_in(last_mail_to(mailbox, MANAGER_EMAIL)), "password": STRONG_PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    _enrol_totp(client, accepted.cookies.get("dms_session"))


def test_the_shared_demo_user_stays_until_a_personal_manager_has_signed_in_with_a_second_factor(
    seed, deployed_seed, retire, db_session, client, mailbox
):
    seed()
    deployed_seed()
    before = _all_counts(db_session)
    db_session.rollback()

    # Invited, not yet accepted.
    assert retire(commit=True) == 0
    # Accepted with a password, no factor yet: still not proof enough.
    accepted = client.post(
        "/v1/auth/invitations/accept",
        json={"token": token_in(last_mail_to(mailbox, MANAGER_EMAIL)), "password": STRONG_PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    after_accept = _all_counts(db_session)
    db_session.rollback()
    assert retire(commit=True) == 0

    db_session.expire_all()
    assert _demo_user(db_session).status == UserStatus.ACTIVE
    assert _all_counts(db_session) == after_accept
    assert after_accept != before  # the acceptance itself wrote, the script did not


def test_a_dry_run_changes_nothing_even_when_it_could_retire(seed, deployed_seed, retire, db_session, client, mailbox):
    seed()
    deployed_seed()
    _personal_manager_signs_in_with_mfa(client, mailbox)
    before = _all_counts(db_session)
    db_session.rollback()

    assert retire(commit=False) == 0

    db_session.expire_all()
    assert _demo_user(db_session).status == UserStatus.ACTIVE
    assert _all_counts(db_session) == before


def test_commit_retires_the_shared_demo_user_audited_and_once(seed, deployed_seed, retire, db_session, client, mailbox):
    seed()
    deployed_seed()
    shared_session = _login(client, PASSWORD)
    assert shared_session.status_code == 200, shared_session.text
    _personal_manager_signs_in_with_mfa(client, mailbox)
    db_session.rollback()

    assert retire(commit=True) == 0

    db_session.expire_all()
    shared = _demo_user(db_session)
    assert shared.status == UserStatus.DEACTIVATED
    audit = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == shared.id, AuditEvent.reason == "kan551_shared_demo_login_retired"
        )
    ).all()
    assert [(a.action, a.actor_id, a.after.get("status")) for a in audit] == [("update", None, "deactivated")]
    # Its sessions are ended, and the password no longer signs in.
    live = db_session.scalars(
        select(AuthSession).where(AuthSession.user_id == shared.id, AuthSession.revoked_at.is_(None))
    ).all()
    assert live == []
    assert _login(client, PASSWORD).status_code == 401
    # The personal manager still signs in.
    assert client.post("/v1/auth/login", json={"email": MANAGER_EMAIL, "password": STRONG_PASSWORD}).status_code == 200

    before = _all_counts(db_session)
    db_session.rollback()
    assert retire(commit=True) == 0
    assert _all_counts(db_session) == before


def test_the_retire_script_is_a_no_op_where_no_shared_user_exists(deployed_seed, retire, db_session):
    assert retire(commit=True) == 0  # no demo Betrieb at all
    deployed_seed()
    before = _all_counts(db_session)
    db_session.rollback()
    assert retire(commit=True) == 0
    assert _all_counts(db_session) == before


def test_no_screen_or_readme_hints_a_demo_credential():
    """What-to-build item 3: the sign-in screen, the bundles and the README
    name no demo address or password (the README names the address only to
    say it is retired)."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sources = [
        root / "README.md",
        *sorted((root / "frontend/apps/dms/src").rglob("*.ts*")),
        *sorted((root / "frontend/apps/dms/src/i18n/locales").glob("*.json")),
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "DMS_SEED_DEMO_PASSWORD=" not in text, path
        if path.name == "README.md":
            continue  # the runbook names the address it retires
        assert seed_staging_demo.DEMO_EMAIL not in text, path


def test_a_shared_user_a_manager_deactivated_by_hand_is_left_to_that_manager(
    seed, deployed_seed, retire, db_session, client, mailbox
):
    """The retry hands over only what this script deactivated: a manager's
    own deactivation keeps the decisions the manager made."""

    seed()
    deployed_seed()
    _personal_manager_signs_in_with_mfa(client, mailbox)
    shared = _demo_user(db_session)
    shared.status = UserStatus.DEACTIVATED
    shared.deactivation_reason = "left the project"
    db_session.commit()
    before = _all_counts(db_session)
    db_session.rollback()

    assert retire(commit=True) == 0

    assert _all_counts(db_session) == before
    db_session.expire_all()
    assert _demo_user(db_session).deactivation_reason == "left the project"


def test_the_shared_users_records_go_to_the_unassigned_queue(seed, deployed_seed, retire, db_session, client, mailbox):
    """FR-A-37: nobody is there to choose, so the shared user's customers go
    to the Customers queue, audited (`ownership_transferred`)."""

    seed()
    shared = _demo_user(db_session)
    token = create_access_token(
        user_id=shared.id,
        tenant_id=shared.tenant_id,
        group_id=dealer_group_of(str(shared.tenant_id)),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )
    created = client.post(
        "/v1/customers",
        json={
            "firstName": "Ada",
            "lastName": "Byron",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": "ada@example.ch"}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["advisorId"] == str(shared.id)
    deployed_seed()
    _personal_manager_signs_in_with_mfa(client, mailbox)
    db_session.rollback()

    assert retire(commit=True) == 0

    db_session.expire_all()
    customer = db_session.get(Customer, uuid.UUID(created.json()["id"]))
    assert customer.advisor_id is None
    handed = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == shared.id, AuditEvent.action == "ownership_transferred")
    ).all()
    assert handed and all(a.reason == "kan551_shared_demo_login_retired" for a in handed)
