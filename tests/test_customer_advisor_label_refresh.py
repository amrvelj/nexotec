"""KAN-403 — the customer's advisor label (rule 2's copy of a platform user's
name) follows a rename within a day.

What is pinned here:
- the nightly `customer.advisor_labels` job rewrites `advisorLabel` and
  `advisorLabelRefreshedAt` after the user is renamed through the API, and
  moves `updatedAt` (what the API returns changed) without bumping `version`;
- a current label is only restamped, with `updatedAt` held;
- a deactivated user keeps the label they had (restamped, not rewritten);
- an advisor id that names no user is left alone, without failing the job;
- the sync-age alarm reads the stalest stamp, and the worker heartbeat
  records it as `customer.advisor`;
- the worker registers the job after the vehicle-party labels and before
  reconciliation;
- it skips a customer another writer holds and waits for nobody, so it
  cannot deadlock against a merge re-pointing earlier merges (KAN-383).
"""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.customer.models.customer import Customer
from app.customer.services import customer as customer_service
from app.customer.services.advisor_label import oldest_advisor_label_age_seconds, refresh_advisor_labels
from tests.membership_support import platform_admin
from tests.test_customer_fr17_fields import _bearer, _create_dealer, _create_user, _token
from tests.test_customer_vehicle_party_close_rule import _customer as _vp_customer
from tests.test_customer_vehicle_party_close_rule import _merge, _postgres_only, _run


def _customer_with_advisor(client, dealer_id: str, advisor: dict) -> dict:
    token = _token(tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(advisor["id"]))
    response = client.post(
        "/v1/customers",
        json={
            "firstName": "Ada",
            "lastName": "Byron",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": f"ada-{uuid.uuid4().hex[:8]}@example.ch"}],
        },
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["advisorId"] == advisor["id"]
    return created


def _patch_user(client, dealer_id: str, user: dict, body: dict) -> dict:
    response = client.patch(
        f"/v1/dealerships/{dealer_id}/users/{user['id']}",
        json=body,
        headers={**platform_admin(), "If-Match": str(user["version"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _row(db, customer_id: str) -> Customer:
    db.expire_all()
    return db.scalar(select(Customer).where(Customer.id == uuid.UUID(customer_id)))


def _backdate(db, customer_id: str, hours: int) -> dt.datetime:
    row = _row(db, customer_id)
    then = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    row.advisor_label_refreshed_at = then
    row.updated_at = then
    db.commit()
    return then


def test_a_renamed_advisor_reaches_the_customer_after_the_nightly_job(client, db_session):
    dealer_id = _create_dealer(client)
    advisor = _create_user(client, dealer_id, firstName="Lena", lastName="Meier")
    customer = _customer_with_advisor(client, dealer_id, advisor)
    assert customer["advisorLabel"] == "Lena Meier"
    then = _backdate(db_session, customer["id"], hours=30)

    _patch_user(client, dealer_id, advisor, {"lastName": "Keller"})
    assert _row(db_session, customer["id"]).advisor_label == "Lena Meier"  # a copy, until the job runs

    assert refresh_advisor_labels(db_session) == 1

    row = _row(db_session, customer["id"])
    assert row.advisor_label == "Lena Keller"
    assert row.advisor_label_refreshed_at > then
    assert row.updated_at > then
    assert row.version == customer["version"]

    token = _token(tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(advisor["id"]))
    read = client.get(f"/v1/customers/{customer['id']}", headers=_bearer(token)).json()
    assert read["advisorLabel"] == "Lena Keller"


def test_a_current_label_is_only_restamped(client, db_session):
    dealer_id = _create_dealer(client)
    advisor = _create_user(client, dealer_id, firstName="Rey", lastName="Ortiz")
    customer = _customer_with_advisor(client, dealer_id, advisor)
    then = _backdate(db_session, customer["id"], hours=30)

    assert refresh_advisor_labels(db_session) == 0

    row = _row(db_session, customer["id"])
    assert row.advisor_label == "Rey Ortiz"
    assert row.advisor_label_refreshed_at > then
    assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_a_deactivated_advisor_keeps_the_label_they_had(client, db_session):
    dealer_id = _create_dealer(client)
    advisor = _create_user(client, dealer_id, firstName="Mia", lastName="Frei")
    customer = _customer_with_advisor(client, dealer_id, advisor)
    then = _backdate(db_session, customer["id"], hours=30)

    renamed = _patch_user(client, dealer_id, advisor, {"lastName": "Graf"})
    _patch_user(client, dealer_id, renamed, {"status": "deactivated"})

    assert refresh_advisor_labels(db_session) == 0

    row = _row(db_session, customer["id"])
    assert row.advisor_label == "Mia Frei"
    assert row.advisor_label_refreshed_at > then


def test_an_advisor_id_naming_no_user_is_left_alone(client, db_session):
    dealer_id = _create_dealer(client)
    advisor = _create_user(client, dealer_id, firstName="Gone", lastName="User")
    dangling = _customer_with_advisor(client, dealer_id, advisor)
    renamed_advisor = _create_user(client, dealer_id, firstName="Noa", lastName="Huber")
    renamed = _customer_with_advisor(client, dealer_id, renamed_advisor)
    then = _backdate(db_session, dangling["id"], hours=30)

    row = _row(db_session, dangling["id"])
    row.advisor_id = uuid.uuid4()
    db_session.commit()
    _patch_user(client, dealer_id, renamed_advisor, {"firstName": "Noah"})

    assert refresh_advisor_labels(db_session) == 1

    assert _row(db_session, renamed["id"]).advisor_label == "Noah Huber"
    row = _row(db_session, dangling["id"])
    assert row.advisor_label == "Gone User"
    assert abs((row.advisor_label_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_the_job_walks_more_than_one_batch(client, db_session, monkeypatch):
    from app.customer.services import advisor_label

    monkeypatch.setattr(advisor_label, "_BATCH", 2)
    dealer_id = _create_dealer(client)
    advisor = _create_user(client, dealer_id, firstName="Ben", lastName="Roth")
    customers = [_customer_with_advisor(client, dealer_id, advisor) for _ in range(5)]
    _patch_user(client, dealer_id, advisor, {"firstName": "Benjamin"})

    assert refresh_advisor_labels(db_session) == 5
    assert {_row(db_session, c["id"]).advisor_label for c in customers} == {"Benjamin Roth"}


# --- the sync-age alarm (rule 10) --------------------------------------------------------


def test_label_age_is_none_without_an_advisor_then_the_stalest_age(client, db_session):
    assert oldest_advisor_label_age_seconds(db_session) is None

    dealer_id = _create_dealer(client)
    advisor = _create_user(client, dealer_id)
    customer = _customer_with_advisor(client, dealer_id, advisor)
    _customer_with_advisor(client, dealer_id, advisor)
    _backdate(db_session, customer["id"], hours=30)

    age = oldest_advisor_label_age_seconds(db_session)
    assert 30 * 3600 <= age < 31 * 3600


def test_the_worker_heartbeat_records_the_advisor_label_age(client, db_session, monkeypatch):
    recorded = {}
    monkeypatch.setattr(worker, "record_label_age_seconds", lambda label, s: recorded.__setitem__(label, s))
    dealer_id = _create_dealer(client)
    _customer_with_advisor(client, dealer_id, _create_user(client, dealer_id))

    worker._heartbeat(db_session, worker.InProcessTransport(lambda: db_session))

    assert recorded["customer.advisor"] is not None


# --- scheduling --------------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_the_advisor_label_refresh_before_reconciliation(_clean_registry):
    worker.register_daily_jobs()
    names = registered_job_names()
    assert names.index("customer.vehicle_party_labels") < names.index("customer.advisor_labels")
    assert names.index("customer.advisor_labels") < names.index("reconciliation.run_all")


# --- locking: the job waits for nobody --------------------------------------------------


def test_a_customer_another_writer_holds_is_skipped_until_the_next_night(engine, client, db_session):
    """FOR NO KEY UPDATE SKIP LOCKED: the job relabels what it can lock and
    leaves a held row (label and stamp) for the next run, never waits for it."""

    _postgres_only(engine)
    dealer_id = _create_dealer(client)
    advisor = _create_user(client, dealer_id, firstName="Eva", lastName="Lang")
    held_customer = _customer_with_advisor(client, dealer_id, advisor)
    free_customer = _customer_with_advisor(client, dealer_id, advisor)
    then = _backdate(db_session, held_customer["id"], hours=30)
    _patch_user(client, dealer_id, advisor, {"lastName": "Berg"})

    holder = sessionmaker(bind=engine)()
    try:
        holder.execute(
            text("SELECT id FROM customer WHERE id = :id FOR NO KEY UPDATE"), {"id": uuid.UUID(held_customer["id"])}
        )
        job_db = sessionmaker(bind=engine)()
        try:
            # A job that waited would fail here instead of hanging the test.
            job_db.execute(text("SET lock_timeout = '3s'"))
            assert refresh_advisor_labels(job_db) == 1
        finally:
            job_db.close()
    finally:
        holder.rollback()
        holder.close()

    assert _row(db_session, free_customer["id"]).advisor_label == "Eva Berg"
    held = _row(db_session, held_customer["id"])
    assert held.advisor_label == "Eva Lang"
    assert abs((held.advisor_label_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1

    assert refresh_advisor_labels(db_session) == 1  # the next night
    assert _row(db_session, held_customer["id"]).advisor_label == "Eva Berg"


def test_the_job_racing_a_merge_that_repoints_earlier_merges_is_no_deadlock(engine, db_session, monkeypatch):
    """A merge D→T holds D and T, then locks X, merged into D earlier
    (KAN-383). A job that held X and waited for D deadlocked with it — and
    after deadlock_timeout Postgres aborted the merge, a 500. The job now
    skips D and T, finishes while the merge is paused, and the merge then
    commits."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    x, d, t = (_vp_customer(db_session, group_id, name) for name in ("Xaver", "Dora", "Tina"))
    for c in (x, d, t):
        c.advisor_id = uuid.uuid4()
        c.advisor_label = "Old Name"
    db_session.commit()
    x_id, d_id, t_id = x.id, d.id, t.id
    assert x_id < d_id < t_id
    _merge(db_session, x_id, d_id, group_id)
    db_session.close()

    reached, release = threading.Event(), threading.Event()
    real_repoint = customer_service._repoint_earlier_merges

    def _paused(db, **kwargs):
        reached.set()
        release.wait(timeout=20)
        return real_repoint(db, **kwargs)

    monkeypatch.setattr(customer_service, "_repoint_earlier_merges", _paused)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    merge_db, job_db = factory(), factory()
    errors: list[Exception] = []
    try:
        merge = _run("merge", errors, lambda: (_merge(merge_db, d_id, t_id, group_id), merge_db.commit()))
        assert reached.wait(timeout=10), "the merge never reached its re-point of earlier merges"
        job = _run("job", errors, lambda: refresh_advisor_labels(job_db))
        job.join(timeout=10)
        assert not job.is_alive(), "the job waited on the merge's rows"
        release.set()
        merge.join(timeout=20)
        assert not merge.is_alive()
    finally:
        release.set()
        merge_db.rollback()
        merge_db.close()
        job_db.rollback()
        job_db.close()
    assert errors == []
    assert db_session.get(Customer, d_id).duplicate_of_customer_id == t_id
    assert db_session.get(Customer, x_id).duplicate_of_customer_id == t_id
