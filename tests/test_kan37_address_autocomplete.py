"""KAN-37 (ADR-101) — Google Places Autocomplete on the address forms: the
backend's share of it.

- `dealership_settings.address_autocomplete_enabled` ships ON (Anto,
  2026-10-25), is the manager's to switch through the settings PATCH with
  If-Match, and the change is audited with before and after;
- `GET /v1/dealerships/{id}/address-autocomplete` answers `enabled` and the
  browser key only when the switch is on AND the platform holds a key —
  off with no key whatever the switch says, off with a key when the Betrieb
  switched it off; the key never travels while `enabled` is false;
- any role of the Betrieb reads it (every role enters addresses); another
  Betrieb's manager gets 404, never 403;
- a Betrieb without a settings row (fixtures, the seed) reads the shipped
  value — on — without a write.
"""

import uuid

import pytest
import sqlalchemy as sa

from app.core.auth import AccessRole
from app.core.config import get_settings
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.models.dealership_settings import DealershipSettings
from tests.dealership_support import valid_uid
from tests.test_dealership import _bearer, _create_dealership, _token

KEY = "AIza-test-browser-key"


@pytest.fixture()
def browser_key():
    """The platform holds a Google Places browser key for this test."""

    settings = get_settings()
    original = settings.google_places_browser_key
    settings.google_places_browser_key = KEY
    try:
        yield KEY
    finally:
        settings.google_places_browser_key = original


@pytest.fixture()
def no_browser_key():
    settings = get_settings()
    original = settings.google_places_browser_key
    settings.google_places_browser_key = None
    try:
        yield
    finally:
        settings.google_places_browser_key = original


def _manager(dealership_id: str) -> dict[str, str]:
    return _bearer(_token(tenant_id=uuid.UUID(dealership_id), is_dealer_manager=True))


def _role(dealership_id: str, role: AccessRole) -> dict[str, str]:
    return _bearer(_token(role, tenant_id=uuid.UUID(dealership_id)))


def _audit(client, dealership_id: str) -> list[dict]:
    log = client.get(f"/v1/dealerships/{dealership_id}/audit-log", headers=_manager(dealership_id))
    assert log.status_code == 200, log.text
    return log.json()["items"]


def test_the_switch_ships_on_and_the_manager_turns_it_off_with_if_match_audited(client):
    dealership = _create_dealership(client)
    url = f"/v1/dealerships/{dealership['id']}/settings"
    headers = _manager(dealership["id"])

    assert client.get(url, headers=headers).json()["addressAutocompleteEnabled"] is True

    assert client.patch(url, json={"addressAutocompleteEnabled": False}, headers=headers).status_code == 400
    response = client.patch(url, json={"addressAutocompleteEnabled": False}, headers={**headers, "If-Match": "1"})
    assert response.status_code == 200, response.text
    assert response.json()["addressAutocompleteEnabled"] is False and response.json()["version"] == 2
    assert client.get(url, headers=headers).json()["addressAutocompleteEnabled"] is False

    event = next(e for e in _audit(client, dealership["id"]) if e["entityType"] == "dealership_settings")
    assert event["before"] == {"address_autocomplete_enabled": True}
    assert event["after"] == {"address_autocomplete_enabled": False}

    # Null is not "off": the column is NOT NULL, the body is refused (KAN-312).
    cleared = client.patch(url, json={"addressAutocompleteEnabled": None}, headers={**headers, "If-Match": "2"})
    assert cleared.status_code == 422, cleared.text


def test_the_config_read_carries_the_key_only_while_the_switch_is_on(client, browser_key):
    dealership = _create_dealership(client)
    config_url = f"/v1/dealerships/{dealership['id']}/address-autocomplete"
    headers = _manager(dealership["id"])

    assert client.get(config_url, headers=headers).json() == {"enabled": True, "browserKey": KEY}

    off = client.patch(
        f"/v1/dealerships/{dealership['id']}/settings",
        json={"addressAutocompleteEnabled": False},
        headers={**headers, "If-Match": "1"},
    )
    assert off.status_code == 200, off.text
    assert client.get(config_url, headers=headers).json() == {"enabled": False, "browserKey": None}


def test_without_a_platform_key_the_feature_is_off_whatever_the_switch_says(client, no_browser_key):
    dealership = _create_dealership(client)
    headers = _manager(dealership["id"])
    assert client.get(f"/v1/dealerships/{dealership['id']}/settings", headers=headers).json()["addressAutocompleteEnabled"] is True
    response = client.get(f"/v1/dealerships/{dealership['id']}/address-autocomplete", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": False, "browserKey": None}


def test_any_role_of_the_betrieb_reads_the_config_and_another_betrieb_gets_404(client, browser_key):
    dealership = _create_dealership(client)
    config_url = f"/v1/dealerships/{dealership['id']}/address-autocomplete"
    for role in (AccessRole.SALES, AccessRole.AFTERSALES, AccessRole.INVENTORY):
        response = client.get(config_url, headers=_role(dealership["id"], role))
        assert response.status_code == 200, (role, response.text)
        assert response.json()["enabled"] is True
    # Reading the config is not switching it: the switch stays the manager's.
    refused = client.patch(
        f"/v1/dealerships/{dealership['id']}/settings",
        json={"addressAutocompleteEnabled": False},
        headers={**_role(dealership["id"], AccessRole.SALES), "If-Match": "1"},
    )
    assert refused.status_code == 403
    other = _create_dealership(client)
    assert client.get(config_url, headers=_manager(other["id"])).status_code == 404


def test_a_betrieb_without_a_settings_row_reads_on_without_writing(client, db_session, browser_key):
    group = DealerGroup(name="G")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage AG",
        franchise_type=FranchiseType.INDEPENDENT,
        phone="+41441234567",
        uid=valid_uid(),
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
    )
    db_session.add(dealership)
    db_session.commit()
    response = client.get(f"/v1/dealerships/{dealership.id}/address-autocomplete", headers=_manager(str(dealership.id)))
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": True, "browserKey": KEY}
    assert db_session.scalar(sa.select(sa.func.count()).select_from(DealershipSettings)) == 0
