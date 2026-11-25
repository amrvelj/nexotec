"""KAN-805 PR-5: what the settings screen's history shows — the value before
and after each change, and who made it, by name for a user the Betrieb knows
(the ticket's Design section: «who, when, the value before and the value
after; a restore adds an entry»). Plus PR-2's review leftover: too many
decimals is refused as such, not as «not a number»."""

import uuid

import pytest

from app.platform.organisation.services import setting_store as store
from tests import setting_store_support as samples
from tests.platform.test_kan805_area_settings_api import _base, _betrieb, _put

KEY = samples.WAIT.key


@pytest.fixture()
def declared():
    with samples.declared(*samples.SAMPLES):
        yield


def _history(client, headers, dealership_id, **params):
    response = client.get(f"{_base(dealership_id)}/setting-values/{KEY}/history", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _numbers(items, field):
    return [(item[field] or {}).get("number") for item in items]


def test_each_change_carries_the_value_before_it(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    for version, value in enumerate(("10", "20")):
        assert _put(client, boss, dealership_id, KEY, {"number": value}, version).status_code == 200
    assert _put(client, boss, dealership_id, KEY, None, 2).status_code == 200  # cleared

    items = _history(client, boss, dealership_id)["items"]
    assert [(item["isSet"], item["isSetBefore"]) for item in items] == [(False, True), (True, True), (True, False)]
    assert _numbers(items, "value") == [None, "20", "10"]
    assert _numbers(items, "valueBefore") == ["20", "10", None]


def test_the_value_before_survives_a_page_boundary(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    for version, value in enumerate(("10", "20", "30")):
        assert _put(client, boss, dealership_id, KEY, {"number": value}, version).status_code == 200
    first = _history(client, boss, dealership_id, limit=2)
    second = _history(client, boss, dealership_id, limit=2, cursor=first["nextCursor"])
    assert _numbers(first["items"], "valueBefore") == ["20", "10"]
    assert _numbers(second["items"], "valueBefore") == [None]


def test_a_restore_is_an_entry_with_its_own_before_and_after(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    for version, value in enumerate(("10", "20")):
        assert _put(client, boss, dealership_id, KEY, {"number": value}, version).status_code == 200
    oldest = _history(client, boss, dealership_id)["items"][-1]["id"]
    restored = client.post(
        f"{_base(dealership_id)}/setting-changes/{oldest}/restore", headers={**boss, "If-Match": "2"}
    )
    assert restored.status_code == 200, restored.text
    items = _history(client, boss, dealership_id)["items"]
    assert len(items) == 3  # nothing removed
    assert (items[0]["origin"], items[0]["valueBefore"]["number"], items[0]["value"]["number"]) == ("restore", "20", "10")


def test_the_history_names_who_changed_it_and_every_role_reads_it(client, db_session, declared):
    dealership_id, boss, clerk = _betrieb(client, db_session)
    assert _put(client, boss, dealership_id, KEY, {"number": "10"}, 0).status_code == 200
    # A user this Betrieb does not know (platform staff, another Betrieb's user).
    store.change_setting(
        db_session, dealership_id=dealership_id, location_id=None, key=KEY, value=store.NOT_SET,
        expected_version=1, actor_id=uuid.uuid4(),
    )
    db_session.commit()

    items = _history(client, clerk, dealership_id)["items"]
    me = client.get("/v1/auth/me", headers=boss).json()["user"]
    assert items[0]["createdByName"] is None
    assert items[1]["createdByName"] == f"{me['firstName']} {me['lastName']}"


def test_too_many_decimals_is_refused_as_such(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    refused = _put(client, boss, dealership_id, samples.FEE.key, {"number": "1.12345678901"}, 0)
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (
        422,
        "setting_value_too_many_decimals",
    )
