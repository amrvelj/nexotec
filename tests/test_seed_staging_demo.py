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
from app.platform import reconciliation as platform_reconciliation
from app.platform.models.dealership import DealerGroup, Dealership
from app.platform.models.user import User, UserRole
from app.platform.schemas.user import UserCreate
from app.platform.services import user as user_service
from scripts import seed_staging_demo


@pytest.fixture()
def seed(engine, monkeypatch):
    """main() opens its own app.db.SessionLocal; point it at the test engine."""
    monkeypatch.setattr(
        seed_staging_demo,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False),
    )

    def run(auth_identity_id: str) -> None:
        monkeypatch.setenv("DMS_SEED_DEMO_AUTH_IDENTITY_ID", auth_identity_id)
        seed_staging_demo.main()

    return run


def _counts(db_session) -> tuple[int, int, int, int]:
    return (
        db_session.scalar(select(func.count()).select_from(Dealership)),
        db_session.scalar(select(func.count()).select_from(User)),
        db_session.scalar(select(func.count()).select_from(AuditEvent)),
        db_session.scalar(select(func.count()).select_from(OutboxMessage)),
    )


def test_first_run_creates_the_demo_dealership_and_user(seed, db_session):
    seed("zitadel-sub-1")

    dealership = db_session.scalar(select(Dealership).where(Dealership.legal_name == seed_staging_demo.DEMO_LEGAL_NAME))
    assert dealership is not None
    user = db_session.scalar(select(User).where(User.email == seed_staging_demo.DEMO_EMAIL))
    assert user is not None
    assert user.tenant_id == dealership.id
    assert user.auth_identity_id == "zitadel-sub-1"


def _demo_user(db_session) -> User:
    user = db_session.scalar(select(User).where(User.email == seed_staging_demo.DEMO_EMAIL))
    assert user is not None
    return user


def test_second_run_with_the_same_identity_writes_nothing(seed, db_session):
    seed("zitadel-sub-1")
    before = _counts(db_session)
    user = _demo_user(db_session)
    user_before = (user.version, user.updated_at)
    db_session.rollback()

    seed("zitadel-sub-1")

    assert _counts(db_session) == before
    # Not just no new rows: the existing user is not rewritten either.
    user = _demo_user(db_session)
    db_session.refresh(user)
    assert (user.version, user.updated_at) == user_before


def test_a_changed_identity_is_reconciled_onto_the_existing_user(seed, db_session):
    seed("placeholder-sub")
    dealerships_before, users_before, _, _ = _counts(db_session)
    db_session.rollback()

    seed("zitadel-sub-real")

    dealerships_after, users_after, _, _ = _counts(db_session)
    assert (dealerships_after, users_after) == (dealerships_before, users_before)
    assert _demo_user(db_session).auth_identity_id == "zitadel-sub-real"


def test_without_the_identity_it_exits_1_and_writes_nothing(seed, monkeypatch, db_session):
    # Through the seed fixture, so a write before the check would land in the
    # test database and be counted, not in app.db's own database.
    monkeypatch.delenv("DMS_SEED_DEMO_AUTH_IDENTITY_ID", raising=False)
    before = _counts(db_session)
    db_session.rollback()

    with pytest.raises(SystemExit) as exited:
        seed_staging_demo.main()

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

    seed("zitadel-sub-1")

    group, dealership, user = _seeded_rows(db_session)
    for row in (group, dealership, user):
        assert (row.created_by, row.updated_by) == (user.id, user.id), row


def test_the_seeded_rows_pass_platform_reconciliation(seed, db_session):
    """The nightly job KAN-318 adds must find nothing on a freshly seeded
    environment — otherwise it alarms from its first night."""

    seed("zitadel-sub-1")

    assert platform_reconciliation.run(db_session).orphans_found == 0


def test_a_rerun_restamps_an_actor_that_names_no_user_and_leaves_a_real_one_alone(seed, db_session):
    """An environment seeded before KAN-318 holds the random actor; the seed
    runs on every deploy, so the next run replaces it. A value that names a
    real user — another user's later edit — is not the seed's to overwrite."""

    seed("zitadel-sub-1")
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
            auth_identity_id="zitadel-sub-other",
        ),
        actor_id=user.id,
        actor_roles=frozenset({AccessRole.PLATFORM_ADMIN}),
    )
    pre_kan318_actor = uuid.uuid4()
    db_session.execute(update(Dealership).where(Dealership.id == dealership.id).values(created_by=pre_kan318_actor))
    db_session.execute(update(DealerGroup).where(DealerGroup.id == group.id).values(updated_by=other.id))
    db_session.commit()
    db_session.expire_all()

    seed("zitadel-sub-1")

    group, dealership, user = _seeded_rows(db_session)
    assert (dealership.created_by, dealership.updated_by) == (user.id, user.id)
    assert (group.created_by, group.updated_by) == (user.id, other.id)
    assert platform_reconciliation.run(db_session).orphans_found == 0
