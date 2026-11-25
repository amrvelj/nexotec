"""KAN-808 PR-1a: the planner's resources in Dealer Administration (S7) —
the lists, people and workplaces, working hours; who may do what, the
Aftersales module, the Betrieb's own data only, If-Match, idempotency, the
samples of the starting sets, and a Betrieb still being set up."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.permissions import ModuleCode
from app.planning.models.lists import TechnicianType, WorkplaceType
from app.planning.models.resource import Resource
from app.platform.identity.models.user import User, UserStatus
from app.platform.organisation.models.dealership import Dealership, Location
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services.entitlement import list_active_modules
from tests import setup_checklist_support as s
from tests.identity_support import PasswordLogin, cookie_headers, create_dealership
from tests.platform_tenant_support import ensure_platform_tenant
from tests.token_support import dealer_group_of


def _betrieb(client, db, *, aftersales: bool = True) -> uuid.UUID:
    dealership_id = uuid.UUID(create_dealership(client))
    if aftersales:
        db.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
        db.commit()
    return dealership_id


def _headers(client, db, dealership_id, *, roles=frozenset(), manager=False, user=None) -> dict[str, str]:
    """A token as a session mints it: the Betrieb's real modules and their version."""

    user = user or s.manager(client, db, dealership_id, isDealerManager=manager)
    db.expire_all()
    dealership = db.get(Dealership, dealership_id)
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=dealership_id,
        group_id=dealer_group_of(dealership_id),
        roles=frozenset(roles),
        is_dealer_manager=manager,
        entitlements=list_active_modules(db, dealership_id) if dealership is not None else None,
        entitlements_version=dealership.entitlements_version if dealership is not None else 0,
    )
    return {"Authorization": f"Bearer {token}"}


def _base(dealership_id) -> str:
    return f"/v1/dealerships/{dealership_id}/planning"


def _seat(db, dealership_id) -> uuid.UUID:
    return db.scalar(select(Location.id).where(Location.tenant_id == dealership_id))


def _second_location(db, dealership_id, *, active: bool = True) -> uuid.UUID:
    location = Location(
        tenant_id=dealership_id, code=f"L{uuid.uuid4().hex[:6]}", name="Filiale Winterthur", is_active=active
    )
    db.add(location)
    db.commit()
    return location.id


@pytest.fixture()
def betrieb(client, db_session):
    dealership_id = _betrieb(client, db_session)
    return dealership_id, _headers(client, db_session, dealership_id, manager=True)


def _post(client, path, body, headers, status=201):
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == status, response.text
    return response.json()


def _person(client, db, dealership_id, headers, **body):
    colleague = s.manager(client, db, dealership_id, isDealerManager=False, firstName="Lea", lastName="Meier")
    created = _post(
        client,
        f"{_base(dealership_id)}/resources",
        {"kind": "person", "userId": colleague["id"], "locationId": str(_seat(db, dealership_id)), **body},
        headers,
    )
    return created, colleague


def _workplace(client, db, dealership_id, headers, name="Bühne 1", **body):
    return _post(
        client,
        f"{_base(dealership_id)}/resources",
        {"kind": "workplace", "name": name, "locationId": str(_seat(db, dealership_id)), **body},
        headers,
    )


def _reason(response) -> tuple[int, str | None]:
    return response.status_code, response.json()["error"]["details"].get("reason")


# --- the manager sets the planner up --------------------------------------------------------


def test_the_manager_builds_the_lists_and_the_resources(client, db_session, betrieb):
    dealership_id, boss = betrieb
    base = _base(dealership_id)
    lift = _post(client, f"{base}/workplace-types", {"name": "Hebebühne", "nameTranslations": {"fr": "Pont"}}, boss)
    assert (lift["name"], lift["nameTranslations"], lift["isSample"], lift["seedCode"]) == (
        "Hebebühne", {"fr": "Pont"}, False, None,
    )
    mechatronic = _post(
        client, f"{base}/technician-types", {"name": "Automobil-Mechatroniker", "rank": 3, "countedTimeFactor": "1.25"},
        boss,
    )
    assert (mechatronic["rank"], mechatronic["countedTimeFactor"]) == (3, "1.25")
    hv = _post(client, f"{base}/qualifications", {"name": "Hochvolt", "isMandatory": True}, boss)
    assert hv["isMandatory"] is True

    person, colleague = _person(
        client, db_session, dealership_id, boss, technicianTypeId=mechatronic["id"], qualificationIds=[hv["id"]]
    )
    assert (person["kind"], person["userId"], person["userLabel"], person["personActsHere"]) == (
        "person", colleague["id"], "Lea Meier", True,
    )
    assert (person["technicianTypeId"], person["qualificationIds"], person["name"]) == (mechatronic["id"], [hv["id"]], None)
    assert person["locationLabel"] == db_session.get(Location, _seat(db_session, dealership_id)).name

    workplace = _workplace(client, db_session, dealership_id, boss, workplaceTypeId=lift["id"])
    assert (workplace["name"], workplace["workplaceTypeId"], workplace["personActsHere"], workplace["userId"]) == (
        "Bühne 1", lift["id"], None, None,
    )

    listing = client.get(f"{base}/resources", headers=boss)
    assert listing.status_code == 200, listing.text
    assert {item["id"] for item in listing.json()["items"]} == {person["id"], workplace["id"]}
    only_people = client.get(f"{base}/resources", params={"kind": "person"}, headers=boss).json()["items"]
    assert [item["id"] for item in only_people] == [person["id"]]
    for path, item in (("workplace-types", lift), ("technician-types", mechatronic), ("qualifications", hv)):
        assert [entry["id"] for entry in client.get(f"{base}/{path}", headers=boss).json()["items"]] == [item["id"]]

    audited = set(
        db_session.scalars(select(AuditEvent.entity_type).where(AuditEvent.tenant_id == dealership_id))
    )
    assert {"planning_workplace_type", "planning_technician_type", "planning_qualification", "planning_resource"} <= audited


def test_working_hours_with_a_lunch_break_replace_the_week(client, db_session, betrieb):
    dealership_id, boss = betrieb
    person, _ = _person(client, db_session, dealership_id, boss)
    path = f"{_base(dealership_id)}/resources/{person['id']}/working-hours"
    week = {
        "days": [
            {"weekday": 2, "startsAt": "07:30", "endsAt": "12:00", "secondStartsAt": "13:00", "secondEndsAt": "17:00"},
            {"weekday": 1, "startsAt": "07:30", "endsAt": "16:30"},
        ]
    }
    stored = client.put(path, json=week, headers={**boss, "If-Match": str(person["version"])})
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["version"] == person["version"] + 1
    assert [(d["weekday"], d["startsAt"], d["secondEndsAt"]) for d in body["workingHours"]] == [
        (1, "07:30:00", None),
        (2, "07:30:00", "17:00:00"),
    ]
    # The whole week is replaced: Tuesday alone stays.
    again = client.put(path, json={"days": week["days"][:1]}, headers={**boss, "If-Match": str(body["version"])})
    assert [d["weekday"] for d in again.json()["workingHours"]] == [2]
    stale = client.put(path, json=week, headers={**boss, "If-Match": str(body["version"])})
    assert stale.status_code == 409
    assert "currentVersion" in stale.json()["error"]["details"]


@pytest.mark.parametrize(
    "day",
    [
        {"weekday": 1, "startsAt": "12:00", "endsAt": "08:00"},
        {"weekday": 1, "startsAt": "08:00", "endsAt": "12:00", "secondStartsAt": "11:00", "secondEndsAt": "17:00"},
        {"weekday": 1, "startsAt": "08:00", "endsAt": "12:00", "secondStartsAt": "13:00"},
        {"weekday": 8, "startsAt": "08:00", "endsAt": "12:00"},
        {"weekday": 1, "startsAt": "08:00:30", "endsAt": "12:00"},
    ],
)
def test_working_hours_that_do_not_hold_together_are_refused(client, db_session, betrieb, day):
    dealership_id, boss = betrieb
    person, _ = _person(client, db_session, dealership_id, boss)
    refused = client.put(
        f"{_base(dealership_id)}/resources/{person['id']}/working-hours",
        json={"days": [day]},
        headers={**boss, "If-Match": str(person["version"])},
    )
    assert _reason(refused) == (422, "planning_working_hours_invalid")


def test_a_weekday_twice_and_hours_on_a_workplace_are_refused(client, db_session, betrieb):
    dealership_id, boss = betrieb
    person, _ = _person(client, db_session, dealership_id, boss)
    monday = {"weekday": 1, "startsAt": "08:00", "endsAt": "12:00"}
    twice = client.put(
        f"{_base(dealership_id)}/resources/{person['id']}/working-hours",
        json={"days": [monday, monday]},
        headers={**boss, "If-Match": str(person["version"])},
    )
    assert _reason(twice) == (422, "planning_working_hours_invalid")
    assert twice.json()["error"]["details"]["weekday"] == 1
    workplace = _workplace(client, db_session, dealership_id, boss)
    refused = client.put(
        f"{_base(dealership_id)}/resources/{workplace['id']}/working-hours",
        json={"days": [monday]},
        headers={**boss, "If-Match": str(workplace["version"])},
    )
    assert _reason(refused) == (422, "planning_person_only")


# --- who may do what ------------------------------------------------------------------------


def test_aftersales_reads_and_does_not_write(client, db_session, betrieb):
    dealership_id, boss = betrieb
    _workplace(client, db_session, dealership_id, boss)
    reader = _headers(client, db_session, dealership_id, roles={AccessRole.AFTERSALES})
    for path in ("resources", "workplace-types", "technician-types", "qualifications"):
        assert client.get(f"{_base(dealership_id)}/{path}", headers=reader).status_code == 200, path
    refused = client.post(f"{_base(dealership_id)}/workplace-types", json={"name": "X"}, headers=reader)
    assert refused.status_code == 403
    # The candidates are the writer's list.
    assert client.get(f"{_base(dealership_id)}/person-candidates", headers=reader).status_code == 403


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.TECHNICIAN, AccessRole.PARTS])
def test_other_roles_see_no_planner_resources(client, db_session, betrieb, role):
    dealership_id, _ = betrieb
    other = _headers(client, db_session, dealership_id, roles={role})
    assert client.get(f"{_base(dealership_id)}/resources", headers=other).status_code == 403


def test_the_role_is_checked_before_the_idempotency_key_is_claimed(client, db_session, betrieb):
    """A 403 never takes the key: the manager's request under the same key
    then runs, it is not answered with the refusal."""

    dealership_id, boss = betrieb
    key = {"Idempotency-Key": str(uuid.uuid4())}
    reader = _headers(client, db_session, dealership_id, roles={AccessRole.AFTERSALES})
    path = f"{_base(dealership_id)}/qualifications"
    assert client.post(path, json={"name": "Klima"}, headers={**reader, **key}).status_code == 403
    first = client.post(path, json={"name": "Klima"}, headers={**boss, **key})
    again = client.post(path, json={"name": "Klima"}, headers={**boss, **key})
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json()["id"] == again.json()["id"]


def test_an_idempotency_key_replays_a_resource_create(client, db_session, betrieb):
    dealership_id, boss = betrieb
    headers = {**boss, "Idempotency-Key": str(uuid.uuid4())}
    body = {"kind": "workplace", "name": "Diagnoseplatz", "locationId": str(_seat(db_session, dealership_id))}
    first = client.post(f"{_base(dealership_id)}/resources", json=body, headers=headers)
    again = client.post(f"{_base(dealership_id)}/resources", json=body, headers=headers)
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json()["id"] == again.json()["id"]
    assert db_session.scalar(select(Resource.id).where(Resource.tenant_id == dealership_id, Resource.name == "Diagnoseplatz"))


def test_without_aftersales_the_planner_is_not_found_even_for_the_manager(client, db_session):
    dealership_id = _betrieb(client, db_session, aftersales=False)
    boss = _headers(client, db_session, dealership_id, manager=True)
    assert client.get(f"{_base(dealership_id)}/resources", headers=boss).status_code == 404
    assert client.post(f"{_base(dealership_id)}/qualifications", json={"name": "X"}, headers=boss).status_code == 404
    # Before the role check: a role outside the row gets 404 too, not 403 (D-A-04).
    sales = _headers(client, db_session, dealership_id, roles={AccessRole.SALES})
    assert client.get(f"{_base(dealership_id)}/resources", headers=sales).status_code == 404


def test_another_betrieb_and_the_platform_tenant_are_not_found(client, db_session, betrieb):
    dealership_id, boss = betrieb
    workplace = _workplace(client, db_session, dealership_id, boss)
    other = _betrieb(client, db_session)
    other_boss = _headers(client, db_session, other, manager=True)
    assert client.get(f"{_base(dealership_id)}/resources", headers=other_boss).status_code == 404
    # Its own path, someone else's id: not found either.
    assert client.get(f"{_base(other)}/resources/{workplace['id']}", headers=other_boss).status_code == 404
    platform_id = uuid.UUID(ensure_platform_tenant(client))
    staff = _headers(client, db_session, platform_id, manager=True, user={"id": str(uuid.uuid4())})
    assert client.get(f"{_base(platform_id)}/resources", headers=staff).status_code == 404


def test_a_betrieb_being_set_up_reaches_the_planner_resources(client, db_session, mailbox):
    """S7 is Dealer Administration (the session guard's prefixes): a pending
    Betrieb's manager sets it up before activation; other modules stay 404."""

    dealership_id = s.pending_dealership(client, db_session, modules=(ModuleCode.AFTERSALES,))
    boss = s.manager(client, db_session, dealership_id, active=False)
    headers = cookie_headers(PasswordLogin(client, mailbox).session_cookie(boss))
    assert client.get("/v1/contacts", headers=headers).status_code == 404
    assert client.get(f"{_base(dealership_id)}/resources", headers=headers).status_code == 200
    created = client.post(f"{_base(dealership_id)}/workplace-types", json={"name": "Hebebühne"}, headers=headers)
    assert created.status_code == 201, created.text


# --- people and places ------------------------------------------------------------------------


def test_only_someone_who_works_here_becomes_a_person_and_only_once(client, db_session, betrieb):
    dealership_id, boss = betrieb
    elsewhere = _betrieb(client, db_session)
    stranger = s.manager(client, db_session, elsewhere, isDealerManager=False)
    path = f"{_base(dealership_id)}/resources"
    seat = str(_seat(db_session, dealership_id))
    refused = client.post(path, json={"kind": "person", "userId": stranger["id"], "locationId": seat}, headers=boss)
    assert _reason(refused) == (422, "planning_person_not_in_dealership")
    unknown = client.post(path, json={"kind": "person", "userId": str(uuid.uuid4()), "locationId": seat}, headers=boss)
    assert _reason(unknown) == (422, "planning_person_not_in_dealership")

    person, colleague = _person(client, db_session, dealership_id, boss)
    twice = client.post(path, json={"kind": "person", "userId": colleague["id"], "locationId": seat}, headers=boss)
    assert _reason(twice) == (409, "planning_person_already_a_resource")
    # A deactivated person resource still holds the user: it is reactivated, not created again.
    off = client.patch(
        f"{path}/{person['id']}", json={"isActive": False}, headers={**boss, "If-Match": str(person["version"])}
    )
    assert off.status_code == 200, off.text
    again = client.post(path, json={"kind": "person", "userId": colleague["id"], "locationId": seat}, headers=boss)
    assert _reason(again) == (409, "planning_person_already_a_resource")


def test_the_location_is_one_of_the_betriebs_active_locations(client, db_session, betrieb):
    dealership_id, boss = betrieb
    other = _betrieb(client, db_session)
    path = f"{_base(dealership_id)}/resources"
    foreign = client.post(
        path, json={"kind": "workplace", "name": "A", "locationId": str(_seat(db_session, other))}, headers=boss
    )
    assert _reason(foreign) == (422, "unknown_location")
    closed = _second_location(db_session, dealership_id, active=False)
    inactive = client.post(path, json={"kind": "workplace", "name": "A", "locationId": str(closed)}, headers=boss)
    assert _reason(inactive) == (422, "planning_location_inactive")

    workplace = _workplace(client, db_session, dealership_id, boss)
    moved_to_closed = client.patch(
        f"{path}/{workplace['id']}", json={"locationId": str(closed)}, headers={**boss, "If-Match": "1"}
    )
    assert _reason(moved_to_closed) == (422, "planning_location_inactive")
    branch = _second_location(db_session, dealership_id)
    moved = client.patch(f"{path}/{workplace['id']}", json={"locationId": str(branch)}, headers={**boss, "If-Match": "1"})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["locationId"], moved.json()["locationLabel"]) == (str(branch), "Filiale Winterthur")
    at_branch = client.get(path, params={"locationId": str(branch)}, headers=boss).json()["items"]
    assert [item["id"] for item in at_branch] == [workplace["id"]]


def test_a_workplace_name_is_unique_at_its_location(client, db_session, betrieb):
    dealership_id, boss = betrieb
    _workplace(client, db_session, dealership_id, boss, name="Bühne 1")
    taken = client.post(
        f"{_base(dealership_id)}/resources",
        json={"kind": "workplace", "name": "bühne  1", "locationId": str(_seat(db_session, dealership_id))},
        headers=boss,
    )
    assert _reason(taken) == (409, "planning_workplace_name_taken")
    # The same name at another location is another workplace.
    branch = _second_location(db_session, dealership_id)
    elsewhere = client.post(
        f"{_base(dealership_id)}/resources",
        json={"kind": "workplace", "name": "Bühne 1", "locationId": str(branch)},
        headers=boss,
    )
    assert elsewhere.status_code == 201, elsewhere.text


def test_a_field_of_the_other_kind_is_refused(client, db_session, betrieb):
    dealership_id, boss = betrieb
    path = f"{_base(dealership_id)}/resources"
    qualification = _post(client, f"{_base(dealership_id)}/qualifications", {"name": "Hochvolt"}, boss)
    workplace_with_people_fields = client.post(
        path,
        json={"kind": "workplace", "name": "A", "locationId": str(_seat(db_session, dealership_id)),
              "qualificationIds": [qualification["id"]]},
        headers=boss,
    )
    assert workplace_with_people_fields.status_code == 422
    workplace = _workplace(client, db_session, dealership_id, boss)
    person, _ = _person(client, db_session, dealership_id, boss)
    refused = client.patch(
        f"{path}/{workplace['id']}", json={"qualificationIds": [qualification["id"]]}, headers={**boss, "If-Match": "1"}
    )
    assert _reason(refused) == (422, "planning_person_only")
    refused = client.patch(f"{path}/{person['id']}", json={"name": "Lea"}, headers={**boss, "If-Match": "1"})
    assert _reason(refused) == (422, "planning_workplace_only")
    # Sent empty, the other kind's field says what the resource already is.
    fine = client.patch(
        f"{path}/{workplace['id']}", json={"qualificationIds": [], "technicianTypeId": None, "sortOrder": 2},
        headers={**boss, "If-Match": "1"},
    )
    assert (fine.status_code, fine.json()["sortOrder"]) == (200, 2)


def test_a_list_entry_of_another_betrieb_is_unknown_here(client, db_session, betrieb):
    dealership_id, boss = betrieb
    other = _betrieb(client, db_session)
    other_boss = _headers(client, db_session, other, manager=True)
    foreign_type = _post(client, f"{_base(other)}/workplace-types", {"name": "Hebebühne"}, other_boss)
    foreign_qualification = _post(client, f"{_base(other)}/qualifications", {"name": "Hochvolt"}, other_boss)
    refused = client.post(
        f"{_base(dealership_id)}/resources",
        json={"kind": "workplace", "name": "A", "locationId": str(_seat(db_session, dealership_id)),
              "workplaceTypeId": foreign_type["id"]},
        headers=boss,
    )
    assert _reason(refused) == (422, "planning_list_entry_unknown")
    person, _ = _person(client, db_session, dealership_id, boss)
    refused = client.patch(
        f"{_base(dealership_id)}/resources/{person['id']}",
        json={"qualificationIds": [foreign_qualification["id"]]},
        headers={**boss, "If-Match": "1"},
    )
    assert _reason(refused) == (422, "planning_list_entry_unknown")
    # And the other Betrieb's entry is not found under this Betrieb's path.
    assert client.patch(
        f"{_base(dealership_id)}/workplace-types/{foreign_type['id']}", json={"name": "Y"},
        headers={**boss, "If-Match": "1"},
    ).status_code == 404


def test_deactivating_takes_a_resource_out_of_the_planner_and_keeps_it(client, db_session, betrieb):
    dealership_id, boss = betrieb
    workplace = _workplace(client, db_session, dealership_id, boss)
    path = f"{_base(dealership_id)}/resources"
    stale = client.patch(f"{path}/{workplace['id']}", json={"isActive": False}, headers={**boss, "If-Match": "7"})
    assert stale.status_code == 409
    off = client.patch(f"{path}/{workplace['id']}", json={"isActive": False}, headers={**boss, "If-Match": "1"})
    assert (off.status_code, off.json()["isActive"], off.json()["version"]) == (200, False, 2)
    assert client.get(path, headers=boss).json()["items"] == []
    listed = client.get(path, params={"includeInactive": "true"}, headers=boss).json()["items"]
    assert [item["id"] for item in listed] == [workplace["id"]]


def test_a_person_who_left_is_marked_and_not_reactivated(client, db_session, betrieb):
    dealership_id, boss = betrieb
    person, colleague = _person(client, db_session, dealership_id, boss)
    path = f"{_base(dealership_id)}/resources/{person['id']}"
    off = client.patch(path, json={"isActive": False}, headers={**boss, "If-Match": "1"})
    assert off.status_code == 200, off.text
    user = db_session.get(User, uuid.UUID(colleague["id"]))
    user.status = UserStatus.SUSPENDED
    db_session.commit()
    assert client.get(path, headers=boss).json()["personActsHere"] is False
    refused = client.patch(path, json={"isActive": True}, headers={**boss, "If-Match": "2"})
    assert _reason(refused) == (422, "planning_person_not_in_dealership")


def test_the_person_candidates_are_who_works_here_and_is_no_resource_yet(client, db_session, betrieb):
    dealership_id, boss = betrieb
    candidate = s.manager(client, db_session, dealership_id, isDealerManager=False, firstName="Urs", lastName="Graf")
    _, planned = _person(client, db_session, dealership_id, boss)
    listing = client.get(f"{_base(dealership_id)}/person-candidates", headers=boss)
    assert listing.status_code == 200, listing.text
    ids = {item["userId"] for item in listing.json()["items"]}
    assert candidate["id"] in ids
    assert planned["id"] not in ids
    assert {"userId": candidate["id"], "displayName": "Urs Graf"} in listing.json()["items"]


# --- the lists ----------------------------------------------------------------------------------


def _sample(db, model, dealership_id, *, name, seed_code):
    entry = model(tenant_id=dealership_id, name=name, is_sample=True, seed_code=seed_code)
    db.add(entry)
    db.commit()
    return entry.id


def test_a_change_or_a_confirmation_ends_a_sample(client, db_session, betrieb):
    dealership_id, boss = betrieb
    base = _base(dealership_id)
    renamed_id = _sample(db_session, WorkplaceType, dealership_id, name="Hebebühne", seed_code="lift")
    kept_id = _sample(db_session, WorkplaceType, dealership_id, name="Waschplatz", seed_code="wash")
    ranked_id = _sample(db_session, TechnicianType, dealership_id, name="Werkstattchef", seed_code="foreman")
    listing = client.get(f"{base}/workplace-types", headers=boss).json()["items"]
    assert {(item["seedCode"], item["isSample"]) for item in listing} == {("lift", True), ("wash", True)}

    renamed = client.patch(
        f"{base}/workplace-types/{renamed_id}", json={"name": "Hebebühne 2 t"}, headers={**boss, "If-Match": "1"}
    )
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["isSample"], renamed.json()["seedCode"], renamed.json()["version"]) == (False, "lift", 2)

    confirmed = client.post(f"{base}/workplace-types/{kept_id}/confirmation", headers={**boss, "If-Match": "1"})
    assert confirmed.status_code == 200, confirmed.text
    assert (confirmed.json()["isSample"], confirmed.json()["name"], confirmed.json()["version"]) == (False, "Waschplatz", 2)
    stale = client.post(f"{base}/workplace-types/{kept_id}/confirmation", headers={**boss, "If-Match": "1"})
    assert stale.status_code == 409

    # The Betrieb's own figure goes onto an entry that thereby stops being a sample.
    ranked = client.patch(
        f"{base}/technician-types/{ranked_id}", json={"rank": 1, "countedTimeFactor": "1.10"},
        headers={**boss, "If-Match": "1"},
    )
    assert ranked.status_code == 200, ranked.text
    assert (ranked.json()["isSample"], ranked.json()["rank"], ranked.json()["countedTimeFactor"]) == (False, 1, "1.10")
    actions = set(db_session.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == kept_id)))
    assert actions == {"confirm"}


def test_a_sample_never_carries_a_figure(db_session):
    """The database refuses a rank or a factor on a sample (ruling 174): a
    figure is the Betrieb's own, never Nexotec's starting set."""

    db_session.add(TechnicianType(tenant_id=uuid.uuid4(), name="Lernender", is_sample=True, seed_code="apprentice", rank=5))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(WorkplaceType(tenant_id=uuid.uuid4(), name="Diagnoseplatz", is_sample=True))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_an_entry_in_use_is_not_deleted(client, db_session, betrieb):
    dealership_id, boss = betrieb
    base = _base(dealership_id)
    lift = _post(client, f"{base}/workplace-types", {"name": "Hebebühne"}, boss)
    hv = _post(client, f"{base}/qualifications", {"name": "Hochvolt"}, boss)
    unused = _post(client, f"{base}/technician-types", {"name": "Lernender"}, boss)
    workplace = _workplace(client, db_session, dealership_id, boss, workplaceTypeId=lift["id"])
    person, _ = _person(client, db_session, dealership_id, boss, qualificationIds=[hv["id"]])
    for path, entry in (("workplace-types", lift), ("qualifications", hv)):
        refused = client.delete(f"{base}/{path}/{entry['id']}", headers={**boss, "If-Match": "1"})
        assert _reason(refused) == (409, "planning_list_entry_in_use"), path
    # A deactivated resource still uses it.
    client.patch(f"{base}/resources/{workplace['id']}", json={"isActive": False}, headers={**boss, "If-Match": "1"})
    refused = client.delete(f"{base}/workplace-types/{lift['id']}", headers={**boss, "If-Match": "1"})
    assert _reason(refused) == (409, "planning_list_entry_in_use")

    stale = client.delete(f"{base}/technician-types/{unused['id']}", headers={**boss, "If-Match": "2"})
    assert stale.status_code == 409
    deleted = client.delete(f"{base}/technician-types/{unused['id']}", headers={**boss, "If-Match": "1"})
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"{base}/technician-types", headers=boss).json()["items"] == []
    # Once no resource holds it, the qualification goes too.
    client.patch(f"{base}/resources/{person['id']}", json={"qualificationIds": []}, headers={**boss, "If-Match": "1"})
    assert client.delete(f"{base}/qualifications/{hv['id']}", headers={**boss, "If-Match": "1"}).status_code == 204


def test_a_name_is_unique_per_list_and_a_stale_if_match_is_refused(client, db_session, betrieb):
    dealership_id, boss = betrieb
    base = _base(dealership_id)
    lift = _post(client, f"{base}/workplace-types", {"name": "Hebebühne"}, boss)
    taken = client.post(f"{base}/workplace-types", json={"name": "  hebebühne "}, headers=boss)
    assert _reason(taken) == (409, "planning_list_entry_name_taken")
    # Another list may use the same word.
    assert client.post(f"{base}/qualifications", json={"name": "Hebebühne"}, headers=boss).status_code == 201
    stale = client.patch(f"{base}/workplace-types/{lift['id']}", json={"name": "Lift"}, headers={**boss, "If-Match": "2"})
    assert stale.status_code == 409
    renamed = client.patch(
        f"{base}/workplace-types/{lift['id']}", json={"nameTranslations": {"it": "Ponte"}}, headers={**boss, "If-Match": "1"}
    )
    assert (renamed.json()["name"], renamed.json()["nameTranslations"]) == ("Hebebühne", {"it": "Ponte"})
    # Nothing changed: nothing written.
    same = client.patch(
        f"{base}/workplace-types/{lift['id']}", json={"name": "Hebebühne"}, headers={**boss, "If-Match": "2"}
    )
    assert same.json()["version"] == 2


@pytest.mark.parametrize(
    "body",
    [{"name": "A", "countedTimeFactor": 1.5}, {"name": "A", "countedTimeFactor": "0"}, {"name": "A", "rank": 100},
     {"name": "A", "countedTimeFactor": "10.00"}, {"name": "   "}],
)
def test_a_technician_types_figures_are_checked(client, db_session, betrieb, body):
    dealership_id, boss = betrieb
    assert client.post(f"{_base(dealership_id)}/technician-types", json=body, headers=boss).status_code == 422


def test_the_lists_keep_their_order(client, db_session, betrieb):
    dealership_id, boss = betrieb
    base = _base(dealership_id)
    for name, order in (("Zeta", 0), ("alpha", 0), ("Ärztin", 0), ("Erst", 0)):
        _post(client, f"{base}/qualifications", {"name": name, "sortOrder": order}, boss)
    _post(client, f"{base}/qualifications", {"name": "Zuletzt", "sortOrder": 5}, boss)
    names = [item["name"] for item in client.get(f"{base}/qualifications", headers=boss).json()["items"]]
    assert names == ["alpha", "Ärztin", "Erst", "Zeta", "Zuletzt"]

