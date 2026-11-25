"""KAN-808 PR-1b (ADR-221 (4), T5): a planner resource's copies of
platform's names — the person's user label and every resource's location
label — follow a rename within a day.

What is pinned here, the shared rules of the label refreshes
(`.claude/rules/events-worker.md`):
- the nightly `planning.person_labels` and `planning.location_labels`
  rewrite a renamed user's or location's label and its stamp, move
  `updatedAt` and never bump `version`; the API returns the new name;
- a current label is only restamped, `updatedAt` held;
- a deactivated user keeps the label they had (restamped, not rewritten);
- a user id naming nothing, and another Betrieb's location, are left alone,
  label and stamp, and reported;
- the jobs walk more than one batch and skip a resource another writer holds;
- the label ages read the stalest stamp, and the worker heartbeat records
  them as `planning.person` and `planning.location`;
- the worker registers both jobs before `reconciliation.run_all`.
On Postgres.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.planning.models.resource import Resource
from app.planning.public import (
    oldest_location_label_age_seconds,
    oldest_person_label_age_seconds,
    refresh_location_labels,
    refresh_person_labels,
)
from app.planning.services import labels, resources
from app.platform.identity.models.user import User, UserStatus
from app.platform.organisation.models.dealership import Location
from tests import setup_checklist_support as s
from tests.planning.test_kan808_resources_api import _base, _betrieb, _headers, _seat
from tests.postgres_only import ROW_LOCKS

_LONG_AGO = dt.timedelta(hours=30)


@pytest.fixture()
def planned(client, db_session):
    """A Betrieb holding aftersales with a person (Lea Meier) and a workplace at its seat."""

    dealership_id = _betrieb(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    colleague = s.manager(client, db_session, dealership_id, isDealerManager=False, firstName="Lea", lastName="Meier")
    seat = _seat(db_session, dealership_id)
    person = resources.create_person(
        db_session,
        dealership_id=dealership_id,
        actor_id=uuid.UUID(boss["id"]),
        user_id=uuid.UUID(colleague["id"]),
        location_id=seat,
    )
    workplace = resources.create_workplace(
        db_session, dealership_id=dealership_id, actor_id=uuid.UUID(boss["id"]), name="Bühne 1", location_id=seat
    )
    db_session.commit()
    headers = _headers(client, db_session, dealership_id, manager=True, user=boss)
    return dealership_id, person.id, workplace.id, uuid.UUID(colleague["id"]), seat, headers


def _row(db, resource_id: uuid.UUID) -> Resource:
    db.expire_all()
    row = db.get(Resource, resource_id)
    assert row is not None
    return row


def _backdate(db, *resource_ids: uuid.UUID) -> dt.datetime:
    then = dt.datetime.now(dt.UTC) - _LONG_AGO
    for resource_id in resource_ids:
        row = _row(db, resource_id)
        row.location_label_refreshed_at = then
        if row.user_id is not None:
            row.user_label_refreshed_at = then
        row.updated_at = then
    db.commit()
    return then


def _same_instant(a: dt.datetime, b: dt.datetime) -> bool:
    return abs((a.replace(tzinfo=a.tzinfo or dt.UTC) - b).total_seconds()) < 1


# --- the person's user label --------------------------------------------------------------


def test_a_renamed_user_is_relabelled_without_a_version_bump(client, db_session, planned):
    dealership_id, person_id, _, user_id, _, headers = planned
    version_before = _row(db_session, person_id).version
    then = _backdate(db_session, person_id)
    db_session.get(User, user_id).last_name = "Keller"
    db_session.commit()
    assert _row(db_session, person_id).user_label == "Lea Meier"  # a copy, until the job runs

    report = refresh_person_labels(db_session)

    assert (report.changed, report.restamped, report.unresolved) == ([(person_id, "Lea Meier", "Lea Keller")], 0, [])
    row = _row(db_session, person_id)
    assert (row.user_label, row.version) == ("Lea Keller", version_before)
    assert row.user_label_refreshed_at > then and row.updated_at > then
    read = client.get(f"{_base(dealership_id)}/resources/{person_id}", headers=headers)
    assert read.status_code == 200, read.text
    assert (read.json()["userLabel"], read.json()["version"]) == ("Lea Keller", version_before)


def test_a_current_user_label_is_only_restamped(db_session, planned):
    _, person_id, _, _, _, _ = planned
    then = _backdate(db_session, person_id)

    report = refresh_person_labels(db_session)

    assert (report.changed, report.restamped) == ([], 1)
    row = _row(db_session, person_id)
    assert row.user_label == "Lea Meier"
    assert row.user_label_refreshed_at > then
    assert _same_instant(row.updated_at, then)


def test_a_deactivated_user_keeps_the_label_they_had(db_session, planned):
    _, person_id, _, user_id, _, _ = planned
    then = _backdate(db_session, person_id)
    user = db_session.get(User, user_id)
    user.last_name, user.status = "Graf", UserStatus.DEACTIVATED
    db_session.commit()

    report = refresh_person_labels(db_session)

    assert (report.changed, report.restamped) == ([], 1)
    row = _row(db_session, person_id)
    assert row.user_label == "Lea Meier"
    assert row.user_label_refreshed_at > then


def test_a_user_id_naming_nobody_is_left_alone(db_session, planned):
    _, person_id, _, _, _, _ = planned
    then = _backdate(db_session, person_id)
    dangling = uuid.uuid4()
    _row(db_session, person_id).user_id = dangling
    db_session.commit()

    report = refresh_person_labels(db_session)

    assert (report.changed, report.restamped, report.unresolved) == ([], 0, [(person_id, dangling)])
    row = _row(db_session, person_id)
    assert row.user_label == "Lea Meier"
    assert _same_instant(row.user_label_refreshed_at, then)


def test_a_workplace_has_no_user_label_to_refresh(db_session, planned):
    _, _, workplace_id, _, _, _ = planned

    report = refresh_person_labels(db_session)

    assert report.restamped == 1
    assert _row(db_session, workplace_id).user_label_refreshed_at is None


# --- every resource's location label -----------------------------------------------------


def test_a_renamed_location_is_relabelled_on_every_resource_without_a_version_bump(client, db_session, planned):
    dealership_id, person_id, workplace_id, _, seat, headers = planned
    old_name = db_session.get(Location, seat).name
    versions = {rid: _row(db_session, rid).version for rid in (person_id, workplace_id)}
    then = _backdate(db_session, person_id, workplace_id)
    db_session.get(Location, seat).name = "Zürich Oerlikon"
    db_session.commit()

    report = refresh_location_labels(db_session)

    assert sorted(report.changed) == sorted(
        [(person_id, old_name, "Zürich Oerlikon"), (workplace_id, old_name, "Zürich Oerlikon")]
    )
    for resource_id, version in versions.items():
        row = _row(db_session, resource_id)
        assert (row.location_label, row.version) == ("Zürich Oerlikon", version)
        assert row.location_label_refreshed_at > then and row.updated_at > then
    read = client.get(f"{_base(dealership_id)}/resources/{workplace_id}", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["locationLabel"] == "Zürich Oerlikon"

    again = refresh_location_labels(db_session)
    assert (again.changed, again.restamped) == ([], 2)


def test_a_current_location_label_is_only_restamped(db_session, planned):
    _, person_id, workplace_id, _, _, _ = planned
    then = _backdate(db_session, person_id, workplace_id)

    report = refresh_location_labels(db_session)

    assert (report.changed, report.restamped) == ([], 2)
    row = _row(db_session, workplace_id)
    assert row.location_label_refreshed_at > then
    assert _same_instant(row.updated_at, then)


def test_another_betriebs_location_is_left_alone(client, db_session, planned):
    _, _, workplace_id, _, _, _ = planned
    foreign = _seat(db_session, _betrieb(client, db_session))
    then = _backdate(db_session, workplace_id)
    row = _row(db_session, workplace_id)
    row.location_id, row.location_label = foreign, "Old"
    db_session.commit()

    report = refresh_location_labels(db_session)

    assert report.unresolved == [(workplace_id, foreign)]
    assert workplace_id not in {changed[0] for changed in report.changed}
    row = _row(db_session, workplace_id)
    assert row.location_label == "Old"
    assert _same_instant(row.location_label_refreshed_at, then)


def test_a_deactivated_location_s_new_name_is_still_followed(db_session, planned):
    _, person_id, workplace_id, _, seat, _ = planned
    for resource_id in (person_id, workplace_id):
        _row(db_session, resource_id).is_active = False
    location = db_session.get(Location, seat)
    location.name, location.is_active = "Zürich (geschlossen)", False
    db_session.commit()

    refresh_location_labels(db_session)

    assert _row(db_session, workplace_id).location_label == "Zürich (geschlossen)"


# --- batching and locking ----------------------------------------------------------------


def test_the_jobs_walk_more_than_one_batch(client, db_session, planned, monkeypatch):
    dealership_id, _, _, _, seat, _ = planned
    monkeypatch.setattr(labels, "_BATCH", 2)
    for n in range(2, 6):
        resources.create_workplace(db_session, dealership_id=dealership_id, actor_id=None, name=f"Bühne {n}", location_id=seat)
    db_session.commit()
    db_session.get(Location, seat).name = "Zürich Altstetten"
    db_session.commit()

    assert len(refresh_location_labels(db_session).changed) == 6
    assert refresh_person_labels(db_session).restamped == 1


@ROW_LOCKS
def test_a_resource_another_writer_holds_is_skipped_until_the_next_night(engine, db_session, planned):
    """FOR NO KEY UPDATE SKIP LOCKED: the job relabels what it can lock and
    leaves a held resource (label and stamp) for the next run, never waits."""

    _, person_id, workplace_id, _, seat, _ = planned
    then = _backdate(db_session, person_id, workplace_id)
    db_session.get(Location, seat).name = "Zürich Nord"
    db_session.commit()

    holder = sessionmaker(bind=engine)()
    try:
        holder.execute(text("SELECT id FROM planning_resource WHERE id = :id FOR NO KEY UPDATE"), {"id": workplace_id})
        job_db = sessionmaker(bind=engine)()
        try:
            # A job that waited would fail here instead of hanging the test.
            job_db.execute(text("SET lock_timeout = '3s'"))
            assert [changed[0] for changed in refresh_location_labels(job_db).changed] == [person_id]
        finally:
            job_db.close()
    finally:
        holder.rollback()
        holder.close()

    held = _row(db_session, workplace_id)
    assert held.location_label != "Zürich Nord"
    assert _same_instant(held.location_label_refreshed_at, then)
    assert [changed[0] for changed in refresh_location_labels(db_session).changed] == [workplace_id]  # the next night


# --- the label-age alarm (rule 10) --------------------------------------------------------


def test_the_label_ages_are_none_without_a_resource(db_session):
    assert oldest_person_label_age_seconds(db_session) is None
    assert oldest_location_label_age_seconds(db_session) is None


def test_the_label_ages_read_the_stalest_stamp(db_session, planned):
    _, person_id, _, _, _, _ = planned
    _backdate(db_session, person_id)

    person_age = oldest_person_label_age_seconds(db_session)
    location_age = oldest_location_label_age_seconds(db_session)

    assert person_age is not None and 30 * 3600 <= person_age < 31 * 3600
    assert location_age is not None and 30 * 3600 <= location_age < 31 * 3600
    refresh_person_labels(db_session)
    refresh_location_labels(db_session)
    assert oldest_person_label_age_seconds(db_session) < 60
    assert oldest_location_label_age_seconds(db_session) < 60


def test_the_worker_heartbeat_records_both_planning_label_ages(db_session, planned, monkeypatch):
    recorded: dict[str, float | None] = {}
    monkeypatch.setattr(worker, "record_label_age_seconds", lambda label, age: recorded.__setitem__(label, age))

    worker._heartbeat(db_session, worker.InProcessTransport(lambda: db_session))

    assert recorded["planning.person"] is not None
    assert recorded["planning.location"] is not None


# --- scheduling --------------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_both_refreshes_before_reconciliation(_clean_registry):
    worker.register_daily_jobs()
    names = registered_job_names()
    assert names.index("planning.person_labels") < names.index("reconciliation.run_all")
    assert names.index("planning.location_labels") < names.index("reconciliation.run_all")
