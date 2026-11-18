"""KAN-402 (Anto, 2026-11-15, option A; ADR-081 as amended): each start of the
web service checks that every object key a platform row holds names a stored
object, and records a missing one in the reconciliation findings with the
nightly run's alarm. A start-up run never makes the nightly job look fresh.

On Postgres.
"""

import datetime as dt
import uuid

import pytest

from app.core.object_store import LocalDirectoryObjectStore
from app.core.reconciliation import (
    ReconciliationAlarm,
    ReconciliationOrphan,
    ReconciliationRun,
    seconds_since_last_reconciliation,
)
from app.platform.administration.services import stored_objects_check
from app.platform.identity.models.user import User, UserRole, UserStatus
from tests.platform.test_document_render import _make_dealership


def _user(db_session, tenant_id: uuid.UUID, *, signature: str | None = None, photo: str | None = None) -> User:
    user = User(
        tenant_id=tenant_id, first_name="Vera", last_name="Verkauf", email=f"{uuid.uuid4().hex}@example.test",
        role=UserRole.SALES, access_roles=[], is_dealer_manager=False, status=UserStatus.ACTIVE,
        auth_identity_id=str(uuid.uuid4()), signature_image_ref=signature, photo_ref=photo,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_a_signature_whose_object_is_missing_is_a_finding_with_the_nightly_alarm(db_session, tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    kept = _user(db_session, dealership.id, signature=f"dealerships/{dealership.id}/users/a/signature")
    store.put(kept.signature_image_ref or "", b"\x89PNG", content_type="image/png")
    lost = _user(db_session, dealership.id, signature=f"dealerships/{dealership.id}/users/b/signature")
    _user(db_session, dealership.id)  # no signature: nothing to check

    with pytest.raises(ReconciliationAlarm) as alarm:
        stored_objects_check.run(db_session, store)

    assert [(o.check_label, o.source_table, o.source_row_id) for o in alarm.value.orphans] == [
        ("user.signature_image_ref names no stored object", "user", lost.id)
    ]
    db_session.expire_all()
    (run,) = db_session.query(ReconciliationRun).all()
    assert (run.context, run.orphans_found, run.checks_run) == (
        "startup.platform.objects",
        1,
        len(stored_objects_check.KEY_COLUMNS),
    )
    (orphan,) = db_session.query(ReconciliationOrphan).all()
    assert (orphan.target_table, orphan.dangling_value) == ("object_store", lost.id)
    # Reports, never repairs: the row keeps its key.
    db_session.refresh(lost)
    assert lost.signature_image_ref is not None


def test_a_photo_is_checked_too(db_session, tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    lost = _user(db_session, dealership.id, photo=f"dealerships/{dealership.id}/users/c/photo")

    with pytest.raises(ReconciliationAlarm) as alarm:
        stored_objects_check.run(db_session, store)

    assert [(o.check_label, o.source_row_id) for o in alarm.value.orphans] == [("user.photo_ref names no stored object", lost.id)]


def test_every_object_there_is_a_clean_run_recorded(db_session, tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    user = _user(db_session, dealership.id, signature=f"dealerships/{dealership.id}/users/d/signature")
    store.put(user.signature_image_ref or "", b"\x89PNG", content_type="image/png")

    run = stored_objects_check.run(db_session, store)

    assert (run.context, run.orphans_found) == ("startup.platform.objects", 0)
    assert run.finished_at is not None


def test_a_start_up_run_never_makes_the_nightly_reconciliation_look_fresh(db_session, tmp_path):
    nightly = ReconciliationRun(
        context="platform", started_at=dt.datetime(2026, 11, 1, 3, tzinfo=dt.UTC), checks_run=3, orphans_found=0
    )
    nightly.finished_at = dt.datetime(2026, 11, 1, 3, 1, tzinfo=dt.UTC)
    db_session.add(nightly)
    db_session.commit()
    before = seconds_since_last_reconciliation(db_session)

    stored_objects_check.run(db_session, LocalDirectoryObjectStore(tmp_path / "objects"))

    after = seconds_since_last_reconciliation(db_session)
    assert before is not None and after is not None
    assert after >= before  # still the nightly run's age, not the start-up run's seconds


def test_only_start_up_runs_is_still_never_ran(db_session, tmp_path):
    stored_objects_check.run(db_session, LocalDirectoryObjectStore(tmp_path / "objects"))

    assert seconds_since_last_reconciliation(db_session) is None


def test_the_start_up_script_exits_1_on_a_finding_and_0_when_clean(db_session, engine, tmp_path, monkeypatch):
    """render.yaml runs it before uvicorn with `|| true`: its exit code never
    holds the start up; the record and the ERROR line are what count."""

    from sqlalchemy.orm import sessionmaker

    from scripts import check_stored_objects as script

    store = LocalDirectoryObjectStore(tmp_path / "objects")
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script, "get_object_store", lambda: store)
    monkeypatch.setattr(script, "configure_logging", lambda: None)
    monkeypatch.setattr(script, "configure_sentry", lambda: None)

    assert script.main() == 0
    dealership = _make_dealership(db_session)
    _user(db_session, dealership.id, signature=f"dealerships/{dealership.id}/users/e/signature")
    assert script.main() == 1
