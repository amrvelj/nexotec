"""KAN-142: scripts/seed_staging_demo.py runs on every deploy, wrapped in
`|| true` (render.yaml, docker-compose.yml), so a failure there brings the
stack up with no demo user and no error. KAN-97 made it raise TypeError on
every fresh stack and only the review noticed. These tests run the real
main() against the test database: create, then no-op, then reconcile.
"""

import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.outbox_model import OutboxMessage
from app.core.permissions import ModuleCode
from app.platform import reconciliation as platform_reconciliation
from app.platform.models.dealership import DealerGroup, Dealership
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.user import User, UserRole, UserStatus
from app.platform.schemas.user import UserCreate
from app.platform.services import user as user_service
from scripts import seed_staging_demo

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
