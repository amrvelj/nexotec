"""KAN-808 PR-1a: planning's nightly reconciliation (rule 10, ADR-107) —
the platform ids its rows hold. Clean data finds nothing; a resource whose
location or user names nothing, a row of a Betrieb that does not exist, an
active person whose user was deactivated and an actor naming no user are
each reported, never repaired."""

import uuid

import pytest
from sqlalchemy import select

from app.core.reconciliation import ReconciliationAlarm
from app.planning import reconciliation
from app.planning.models.resource import Resource
from app.planning.services import lists, resources
from app.platform.identity.models.user import User, UserStatus
from app.platform.organisation.models.dealership import Location
from tests import setup_checklist_support as s
from tests.identity_support import create_dealership


@pytest.fixture()
def planned(client, db_session):
    """A Betrieb with a workplace (and its type) and a person, written by a real user."""

    dealership_id = uuid.UUID(create_dealership(client))
    boss = uuid.UUID(s.manager(client, db_session, dealership_id)["id"])
    colleague = uuid.UUID(s.manager(client, db_session, dealership_id, isDealerManager=False)["id"])
    seat = db_session.scalar(select(Location.id).where(Location.tenant_id == dealership_id))
    lift = lists.create_entry(db_session, lists.WORKPLACE_TYPES, dealership_id=dealership_id, actor_id=boss, name="Lift")
    workplace = resources.create_workplace(
        db_session, dealership_id=dealership_id, actor_id=boss, name="Bühne 1", location_id=seat,
        workplace_type_id=lift.id,
    )
    person = resources.create_person(
        db_session, dealership_id=dealership_id, actor_id=boss, user_id=colleague, location_id=seat
    )
    db_session.commit()
    return dealership_id, workplace.id, person.id, colleague


def _findings(db_session) -> set[tuple[str, uuid.UUID]]:
    with pytest.raises(ReconciliationAlarm) as alarm:
        reconciliation.run(db_session)
    return {(orphan.check_label, orphan.source_row_id) for orphan in alarm.value.orphans}


def test_clean_data_finds_nothing(db_session, planned):
    run = reconciliation.run(db_session)
    assert (run.context, run.orphans_found) == ("planning", 0)
    assert run.checks_run == len(reconciliation.CHECKS)


def test_a_location_or_a_user_naming_nothing_is_reported(db_session, planned):
    _, workplace_id, person_id, _ = planned
    db_session.get(Resource, workplace_id).location_id = uuid.uuid4()
    db_session.get(Resource, person_id).user_id = uuid.uuid4()
    db_session.commit()
    assert _findings(db_session) == {
        ("planning_resource.location_id -> location.id", workplace_id),
        ("planning_resource.user_id -> user.id", person_id),
    }


def test_a_row_of_a_betrieb_that_does_not_exist_is_reported(db_session, planned):
    stray = lists.create_entry(db_session, lists.QUALIFICATIONS, dealership_id=uuid.uuid4(), actor_id=None, name="X")
    db_session.commit()
    assert _findings(db_session) == {("planning_qualification.tenant_id -> dealership.id", stray.id)}


def test_an_active_person_whose_user_left_is_reported(db_session, planned):
    _, _, person_id, colleague = planned
    db_session.get(User, colleague).status = UserStatus.DEACTIVATED
    db_session.commit()
    assert _findings(db_session) == {("active person resource whose user is deactivated", person_id)}
    # Deactivated in the planner too: nothing left to report.
    db_session.get(Resource, person_id).is_active = False
    db_session.commit()
    assert reconciliation.run(db_session).orphans_found == 0


def test_an_actor_naming_no_user_is_reported(db_session, planned):
    _, workplace_id, _, _ = planned
    db_session.get(Resource, workplace_id).updated_by = uuid.uuid4()
    db_session.commit()
    assert _findings(db_session) == {("planning_resource.updated_by -> user.id", workplace_id)}
