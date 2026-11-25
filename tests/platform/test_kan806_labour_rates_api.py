"""KAN-806: the labour-rates API in Dealer Administration — who may do what,
the Aftersales module, the Betrieb's own data only, If-Match, idempotency,
the screen's read and «which rate applies here»; and the agreed rate, created
at the composition root because it names a contact."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token
from app.core.permissions import ModuleCode
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services import labour_rates as rates_service
from tests import setup_checklist_support as s
from tests.identity_support import create_dealership
from tests.platform_tenant_support import ensure_platform_tenant
from tests.token_support import dealer_group_of


def _today():
    # Asked per use, so a run across midnight in Zurich still agrees with the server.
    return rates_service.today()


def _betrieb(client, db, *, aftersales: bool = True, pending: bool = False) -> uuid.UUID:
    dealership_id = (
        s.pending_dealership(client, db, modules=(ModuleCode.AFTERSALES,))
        if pending
        else uuid.UUID(create_dealership(client))
    )
    if aftersales and not pending:
        db.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
        db.commit()
    return dealership_id


def _headers(client, db, dealership_id, *, roles=frozenset(), manager=False, user=None) -> dict[str, str]:
    user = user or s.manager(client, db, dealership_id, isDealerManager=manager)
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=dealership_id,
        group_id=dealer_group_of(dealership_id),
        roles=frozenset(roles),
        is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


def _base(dealership_id) -> str:
    return f"/v1/dealerships/{dealership_id}"


def _seat(db, dealership_id) -> uuid.UUID:
    return db.scalar(select(Location.id).where(Location.tenant_id == dealership_id))


def _set_o16(client, headers, dealership_id, value="net"):
    response = client.put(
        f"{_base(dealership_id)}/setting-values/labour_rates.rates_entered",
        json={"value": {"choice": value}},
        headers={**headers, "If-Match": "0"},
    )
    assert response.status_code == 200, response.text


@pytest.fixture()
def betrieb(client, db_session):
    dealership_id = _betrieb(client, db_session)
    boss = _headers(client, db_session, dealership_id, manager=True)
    _set_o16(client, boss, dealership_id)
    return dealership_id, boss


def _category(client, headers, dealership_id, name="Mechanik"):
    response = client.post(f"{_base(dealership_id)}/labour-rate-categories", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _rate(client, headers, dealership_id, category_id, location_id, *, amount="150.00", valid_from=None):
    return client.post(
        f"{_base(dealership_id)}/labour-rate-categories/{category_id}/rates",
        json={"locationId": str(location_id), "validFrom": (valid_from or _today()).isoformat(), "amount": amount,
              "entered": "net"},
        headers=headers,
    )


def test_the_manager_builds_categories_rates_and_members(client, db_session, betrieb):
    dealership_id, boss = betrieb
    category = _category(client, boss, dealership_id)
    seat = _seat(db_session, dealership_id)
    created = _rate(client, boss, dealership_id, category["id"], seat, amount="148.5")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["rates"][0]["amount"] == "148.5000"
    assert (body["rates"][0]["entered"], body["rates"][0]["status"]) == ("net", "in_force")

    technician = s.manager(client, db_session, dealership_id, isDealerManager=False)
    added = client.post(
        f"{_base(dealership_id)}/labour-rate-categories/{category['id']}/members",
        json={"userId": technician["id"]},
        headers=boss,
    )
    assert added.status_code == 201, added.text
    assert [m["userId"] for m in added.json()["members"]] == [technician["id"]]

    listing = client.get(f"{_base(dealership_id)}/labour-rate-categories", headers=boss)
    assert listing.status_code == 200, listing.text
    assert listing.json()["today"] == _today().isoformat()
    assert [item["name"] for item in listing.json()["items"]] == ["Mechanik"]

    removed = client.delete(
        f"{_base(dealership_id)}/labour-rate-categories/{category['id']}/members/{technician['id']}", headers=boss
    )
    assert removed.status_code == 204, removed.text


@pytest.mark.parametrize("role", [AccessRole.AFTERSALES, AccessRole.PARTS, AccessRole.FINANCE])
def test_aftersales_parts_and_finance_read_and_do_not_write(client, db_session, betrieb, role):
    dealership_id, _ = betrieb
    reader = _headers(client, db_session, dealership_id, roles={role})
    assert client.get(f"{_base(dealership_id)}/labour-rate-categories", headers=reader).status_code == 200
    refused = client.post(f"{_base(dealership_id)}/labour-rate-categories", json={"name": "X"}, headers=reader)
    assert refused.status_code == 403


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.TECHNICIAN])
def test_sales_and_the_technician_see_no_labour_rates(client, db_session, betrieb, role):
    dealership_id, _ = betrieb
    other = _headers(client, db_session, dealership_id, roles={role})
    assert client.get(f"{_base(dealership_id)}/labour-rate-categories", headers=other).status_code == 403


def test_without_aftersales_labour_rates_are_not_found(client, db_session):
    dealership_id = _betrieb(client, db_session, aftersales=False)
    boss = _headers(client, db_session, dealership_id, manager=True)
    assert client.get(f"{_base(dealership_id)}/labour-rate-categories", headers=boss).status_code == 404
    assert client.post(f"{_base(dealership_id)}/labour-rate-categories", json={"name": "X"}, headers=boss).status_code == 404


def test_another_betrieb_is_not_found(client, db_session, betrieb):
    dealership_id, _ = betrieb
    other = _betrieb(client, db_session)
    other_boss = _headers(client, db_session, other, manager=True)
    assert client.get(f"{_base(dealership_id)}/labour-rate-categories", headers=other_boss).status_code == 404


def test_the_platform_tenant_has_no_labour_rates(client, db_session):
    platform_id = uuid.UUID(ensure_platform_tenant(client))
    boss = _headers(client, db_session, platform_id, manager=True, user={"id": str(uuid.uuid4())})
    assert client.get(f"{_base(platform_id)}/labour-rate-categories", headers=boss).status_code == 404


def test_a_stale_if_match_is_refused(client, db_session, betrieb):
    dealership_id, boss = betrieb
    category = _category(client, boss, dealership_id)
    stale = client.patch(
        f"{_base(dealership_id)}/labour-rate-categories/{category['id']}",
        json={"name": "Mechanik und Elektrik"},
        headers={**boss, "If-Match": str(category["version"] + 1)},
    )
    assert stale.status_code == 409
    renamed = client.patch(
        f"{_base(dealership_id)}/labour-rate-categories/{category['id']}",
        json={"nameTranslations": {"fr": "Mécanique"}},
        headers={**boss, "If-Match": str(category["version"])},
    )
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["name"], renamed.json()["nameTranslations"]) == ("Mechanik", {"fr": "Mécanique"})


def test_an_idempotency_key_replays_the_create(client, db_session, betrieb):
    dealership_id, boss = betrieb
    headers = {**boss, "Idempotency-Key": str(uuid.uuid4())}
    first = client.post(f"{_base(dealership_id)}/labour-rate-categories", json={"name": "Lack"}, headers=headers)
    again = client.post(f"{_base(dealership_id)}/labour-rate-categories", json={"name": "Lack"}, headers=headers)
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json()["id"] == again.json()["id"]


def test_a_rate_amount_travels_as_a_decimal_string(client, db_session, betrieb):
    dealership_id, boss = betrieb
    category = _category(client, boss, dealership_id)
    refused = client.post(
        f"{_base(dealership_id)}/labour-rate-categories/{category['id']}/rates",
        json={"locationId": str(_seat(db_session, dealership_id)), "validFrom": _today().isoformat(), "amount": 150,
              "entered": "net"},
        headers=boss,
    )
    assert refused.status_code == 422


def test_check_15_through_the_api_a_rate_in_use_stays_one_to_come_is_withdrawn(client, db_session, betrieb):
    dealership_id, boss = betrieb
    category = _category(client, boss, dealership_id)
    seat = _seat(db_session, dealership_id)
    in_use = _rate(client, boss, dealership_id, category["id"], seat).json()["rates"][0]
    past = _rate(client, boss, dealership_id, category["id"], seat, valid_from=_today() - dt.timedelta(days=1))
    assert (past.status_code, past.json()["error"]["details"]["reason"]) == (422, "labour_rate_starts_in_the_past")
    refused = client.post(
        f"{_base(dealership_id)}/labour-rates/{in_use['id']}/withdraw", headers={**boss, "If-Match": "1"}
    )
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (409, "labour_rate_in_use")
    later = _rate(client, boss, dealership_id, category["id"], seat, valid_from=_today() + dt.timedelta(days=7))
    scheduled = next(rate for rate in later.json()["rates"] if rate["status"] == "scheduled")
    withdrawn = client.post(
        f"{_base(dealership_id)}/labour-rates/{scheduled['id']}/withdraw", headers={**boss, "If-Match": "1"}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert {r["id"]: r["status"] for r in withdrawn.json()["rates"]}[scheduled["id"]] == "withdrawn"


def test_no_route_edits_a_rate_or_an_agreement():
    from app.main import app

    paths = {(method, route.path) for route in app.routes for method in getattr(route, "methods", ())}
    assert not [p for p in paths if p[0] in {"PUT", "PATCH"} and ("labour-rates/" in p[1] or "labour-rate-agreements/" in p[1])]


def test_no_rate_entered_while_o16_is_not_set(client, db_session):
    dealership_id = _betrieb(client, db_session)
    boss = _headers(client, db_session, dealership_id, manager=True)
    category = _category(client, boss, dealership_id)
    refused = _rate(client, boss, dealership_id, category["id"], _seat(db_session, dealership_id))
    assert refused.status_code == 409
    assert refused.json()["error"]["details"] == {
        "reason": "labour_rates_entry_basis_not_set", "setting": "labour_rates.rates_entered",
    }


def test_the_answer_route_names_its_source(client, db_session, betrieb):
    dealership_id, boss = betrieb
    category = _category(client, boss, dealership_id)
    seat = _seat(db_session, dealership_id)
    nothing = client.get(
        f"{_base(dealership_id)}/labour-rate-answer",
        params={"categoryId": category["id"], "locationId": str(seat)},
        headers=boss,
    )
    assert nothing.status_code == 200, nothing.text
    assert (nothing.json()["found"], nothing.json()["missing"]) == (False, "no_rate")
    _rate(client, boss, dealership_id, category["id"], seat)
    found = client.get(
        f"{_base(dealership_id)}/labour-rate-answer",
        params={"categoryId": category["id"], "locationId": str(seat), "on": _today().isoformat()},
        headers=boss,
    )
    assert (found.json()["source"], found.json()["amount"], found.json()["entered"]) == (
        "category_rate", "150.0000", "net",
    )


def test_only_someone_who_works_here_is_allocated(client, db_session, betrieb):
    dealership_id, boss = betrieb
    category = _category(client, boss, dealership_id)
    elsewhere = _betrieb(client, db_session)
    stranger = s.manager(client, db_session, elsewhere, isDealerManager=False)
    refused = client.post(
        f"{_base(dealership_id)}/labour-rate-categories/{category['id']}/members",
        json={"userId": stranger["id"]},
        headers=boss,
    )
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (
        422, "labour_category_user_cannot_act_here",
    )
    colleague = s.manager(client, db_session, dealership_id, isDealerManager=False)
    path = f"{_base(dealership_id)}/labour-rate-categories/{category['id']}/members"
    assert client.post(path, json={"userId": colleague["id"]}, headers=boss).status_code == 201
    twice = client.post(path, json={"userId": colleague["id"]}, headers=boss)
    assert (twice.status_code, twice.json()["error"]["details"]["reason"]) == (409, "labour_category_member_exists")


def test_the_settings_area_shows_o16_o18_o19(client, db_session, betrieb):
    dealership_id, boss = betrieb
    area = client.get(f"{_base(dealership_id)}/setting-areas/labour_rates", headers=boss)
    assert area.status_code == 200, area.text
    keys = [item["declaration"]["key"] for item in area.json()["settings"]]
    assert keys == ["labour_rates.rates_entered", "labour_rates.time_unit", "labour_rates.work_units_per_hour"]
    refused = client.put(
        f"{_base(dealership_id)}/setting-values/labour_rates.time_unit",
        json={"value": {"choice": "work_units"}},
        headers={**boss, "If-Match": "0"},
    )
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (422, "setting_choice_requires_setting")


# --- PR-2: the checklist items and the people the manager may allocate -----------------------


def _checklist_item(db, dealership_id, code):
    from app.platform.administration.services import setup_checklist
    from app.platform.organisation.models.dealership import Dealership

    db.expire_all()
    checklist = setup_checklist.derive(db, db.get(Dealership, dealership_id))
    return next((item for item in checklist.items if item.code == code), None)


def test_o16_and_a_category_with_a_rate_are_items_of_the_aftersales_checklist(client, db_session):
    dealership_id = _betrieb(client, db_session)
    boss = _headers(client, db_session, dealership_id, manager=True)
    o16 = _checklist_item(db_session, dealership_id, "labour_rates.rates_entered")
    rates_item = _checklist_item(db_session, dealership_id, "labour_rates")
    assert (o16.complete, o16.link) == (False, "/administration/settings/labour_rates")
    assert (rates_item.complete, rates_item.blocks, rates_item.link) == (False, None, "/administration/labour-rates")

    _set_o16(client, boss, dealership_id)
    assert _checklist_item(db_session, dealership_id, "labour_rates.rates_entered").complete is True
    category = _category(client, boss, dealership_id)
    later = _today() + dt.timedelta(days=10)
    assert _rate(client, boss, dealership_id, category["id"], _seat(db_session, dealership_id), valid_from=later).status_code == 201
    # A rate still to come counts (Anto, 2026-11-25).
    assert _checklist_item(db_session, dealership_id, "labour_rates").complete is True

    without = _betrieb(client, db_session, aftersales=False)
    assert _checklist_item(db_session, without, "labour_rates") is None
    assert _checklist_item(db_session, without, "labour_rates.rates_entered") is None


def test_the_manager_reads_who_may_be_allocated(client, db_session, betrieb):
    dealership_id, boss = betrieb
    colleague = s.manager(client, db_session, dealership_id, isDealerManager=False, firstName="Lea", lastName="Meier")
    listing = client.get(f"{_base(dealership_id)}/labour-category-candidates", headers=boss)
    assert listing.status_code == 200, listing.text
    assert {"userId": colleague["id"], "displayName": "Lea Meier"} in listing.json()["items"]
    reader = _headers(client, db_session, dealership_id, roles={AccessRole.AFTERSALES})
    assert client.get(f"{_base(dealership_id)}/labour-category-candidates", headers=reader).status_code == 403


def test_the_candidates_are_the_betriebs_own_and_need_aftersales(client, db_session, betrieb):
    dealership_id, _ = betrieb
    other = _betrieb(client, db_session)
    other_boss = _headers(client, db_session, other, manager=True)
    assert client.get(f"{_base(dealership_id)}/labour-category-candidates", headers=other_boss).status_code == 404
    without = _betrieb(client, db_session, aftersales=False)
    boss_without = _headers(client, db_session, without, manager=True)
    assert client.get(f"{_base(without)}/labour-category-candidates", headers=boss_without).status_code == 404
