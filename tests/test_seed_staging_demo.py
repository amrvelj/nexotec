"""KAN-142: scripts/seed_staging_demo.py runs on every deploy, wrapped in
`|| true` (render.yaml, docker-compose.yml), so a failure there brings the
stack up with no demo user and no error. KAN-97 made it raise TypeError on
every fresh stack and only the review noticed. These tests run the real
main() against the test database: create, then no-op, then reconcile.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.platform.models.dealership import Dealership
from app.platform.models.user import User
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


def test_second_run_with_the_same_identity_writes_nothing(seed, db_session):
    seed("zitadel-sub-1")
    before = _counts(db_session)
    db_session.rollback()

    seed("zitadel-sub-1")

    assert _counts(db_session) == before


def test_a_changed_identity_is_reconciled_onto_the_existing_user(seed, db_session):
    seed("placeholder-sub")
    dealerships_before, users_before, _, _ = _counts(db_session)
    db_session.rollback()

    seed("zitadel-sub-real")

    dealerships_after, users_after, _, _ = _counts(db_session)
    assert (dealerships_after, users_after) == (dealerships_before, users_before)
    user = db_session.scalar(select(User).where(User.email == seed_staging_demo.DEMO_EMAIL))
    assert user is not None
    assert user.auth_identity_id == "zitadel-sub-real"


def test_without_the_identity_it_exits_1_and_writes_nothing(engine, monkeypatch, db_session):
    monkeypatch.delenv("DMS_SEED_DEMO_AUTH_IDENTITY_ID", raising=False)
    before = _counts(db_session)
    db_session.rollback()

    with pytest.raises(SystemExit) as exited:
        seed_staging_demo.main()

    assert exited.value.code == 1
    assert _counts(db_session) == before
