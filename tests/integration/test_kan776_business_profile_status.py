"""KAN-776 (D-A-38, FR-A-43; ADR-195, ADR-196; Anto, 2026-11-20, option C):
when Google refuses Nexotec's own Business Profile client or project, or its
breaker is open, staff see the cause on the platform status view and each
Betrieb with a live binding sees one «interrupted, we are on it» item — no
link, no Google reason, no other Betrieb. A Betrieb without a live binding has
no item. A refusal of one binding's grant is not an interruption.

Against the recorded fake only (`tests/gbp_fakes.py`): nothing has run against
Google.
"""

import datetime as dt
import time
import uuid

import pytest

from app.core.status import StatusState, status_providers
from app.integration.adapters import google_business_profile as gbp
from app.integration.errors import ProviderAccountRefusedError, ProviderRejectedError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding
from app.integration.services import gbp_status, resilience
from app.integration.services.gateway import call_capability
from app.platform.administration.services import status_page
from tests.identity_support import platform_admin_headers
from tests.inventory.test_inventory_invoicing_gate import _make_dealership
from tests.integration.test_kan226_business_profile import (  # noqa: F401 — fixtures
    _as,
    _link,
    _location,
    _pick,
    betrieb,
    client_connection,
    google,
)

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
CLIENT_STATUS = "/v1/integrations/business-profile/client-status"


def _item(db_session, dealership_id):
    items = [item for item in status_page.derive(db_session, dealership_id, now=NOW).items if item.source == "google_business_profile"]
    assert len(items) <= 1
    return items[0] if items else None


def _bound(db_session, dealership_id, *, status=GbpBindingStatus.ACTIVE) -> GbpLocationBinding:
    location = _location(db_session, dealership_id, name=f"Filiale {uuid.uuid4().hex[:4]}")
    binding = GbpLocationBinding(
        tenant_id=dealership_id,
        location_id=location.id,
        location_label=location.name,
        location_label_refreshed_at=NOW,
        status=status,
        connected_at=NOW,
    )
    db_session.add(binding)
    db_session.commit()
    return binding


def _call(db_session, connection, *, provider_error=None, at=NOW, status=CallStatus.ERROR, http_status=403):
    row = IntegrationCallLog(
        connection_id=connection.id,
        capability="accounts_list",
        status=status,
        duration_ms=5,
        correlation_id=uuid.uuid4(),
        http_status=http_status,
        provider_error=provider_error,
    )
    row.created_at = at
    db_session.add(row)
    db_session.commit()
    return row


def _accounts(db_session, connection, binding_id):
    with call_capability(db_session, connection=connection, capability=gbp.GBP_CAPABILITY) as adapter:
        token = adapter.access_token(binding_id=binding_id)  # type: ignore[attr-defined]
        adapter.list_accounts(access_token=token)  # type: ignore[attr-defined]


def test_the_business_profile_source_is_registered_by_the_composition_root():
    import app.status_registry  # noqa: F401 — the root registers on import

    assert "google_business_profile" in {provider.source for provider in status_providers()}


def test_a_betrieb_without_a_live_binding_has_no_item(db_session, google, client_connection):
    dealership = _make_dealership(db_session)
    assert _item(db_session, dealership.id) is None
    _bound(db_session, dealership.id, status=GbpBindingStatus.DISCONNECTED)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED")
    assert _item(db_session, dealership.id) is None


@pytest.mark.parametrize("status", [GbpBindingStatus.ACTIVE, GbpBindingStatus.PENDING, GbpBindingStatus.ERROR])
def test_a_live_binding_with_a_healthy_client_is_one_ok_item(db_session, google, client_connection, status):
    dealership = _make_dealership(db_session)
    _bound(db_session, dealership.id, status=status)
    item = _item(db_session, dealership.id)
    assert item is not None
    assert item.key == "google_business_profile.client"
    assert item.state == StatusState.OK
    assert item.message_key == "statusPage.items.googleBusinessProfile.ok"
    assert item.link is None and item.params == {}


def test_a_project_refusal_interrupts_every_bound_betrieb_until_a_call_succeeds(
    client, db_session, google, client_connection, betrieb
):
    """The real path: Google answers 403 SERVICE_DISABLED for Nexotec's
    project through the adapter; the call log notes the reason; once, below
    the breaker's threshold, is enough. The next successful call clears it."""

    fake, _ = google
    dealership, manager, location = betrieb
    binding = _pick(client, dealership, manager, _link(client, fake, dealership, manager, location)["binding"]).json()
    other = _make_dealership(db_session)
    _bound(db_session, other.id)
    unbound = _make_dealership(db_session)
    assert _item(db_session, dealership.id).state == StatusState.OK

    fake.project_refusal = "SERVICE_DISABLED"
    with pytest.raises(ProviderRejectedError):
        _accounts(db_session, client_connection, uuid.UUID(binding["id"]))
    assert not resilience.circuit_is_open_now(client_connection.id)

    for dealership_id in (dealership.id, other.id):
        item = _item(db_session, dealership_id)
        assert item.state == StatusState.BLOCKED
        assert item.message_key == "statusPage.items.googleBusinessProfile.interrupted"
        # Never the Betrieb's to fix, never Google's reason, never another Betrieb.
        assert item.link is None and item.params == {}
    assert _item(db_session, unbound.id) is None

    staff = client.get(CLIENT_STATUS, headers=platform_admin_headers())
    assert staff.status_code == 200, staff.text
    body = staff.json()
    assert body["configured"] is True and body["interrupted"] is True and body["breakerOpen"] is False
    assert body["refusal"] == "SERVICE_DISABLED" and body["refusedAt"] is not None
    assert body["boundDealerships"] == 2

    fake.project_refusal = None
    _accounts(db_session, client_connection, uuid.UUID(binding["id"]))
    assert _item(db_session, dealership.id).state == StatusState.OK
    body = client.get(CLIENT_STATUS, headers=platform_admin_headers()).json()
    assert body["interrupted"] is False and body["refusal"] is None and body["lastCallAt"] is not None


def test_one_bindings_refused_grant_is_not_an_interruption(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, manager, location = betrieb
    binding = _pick(client, dealership, manager, _link(client, fake, dealership, manager, location)["binding"]).json()
    fake.refuse_listing = True
    with pytest.raises(ProviderAccountRefusedError):
        _accounts(db_session, client_connection, uuid.UUID(binding["id"]))
    assert _item(db_session, dealership.id).state == StatusState.OK


@pytest.mark.parametrize("reason", ["invalid_client", "accessNotConfigured", "BILLING_DISABLED"])
def test_a_client_or_project_refusal_as_the_latest_call_interrupts(db_session, google, client_connection, reason):
    dealership = _make_dealership(db_session)
    _bound(db_session, dealership.id)
    _call(db_session, client_connection, provider_error="PERMISSION_DENIED", at=NOW - dt.timedelta(minutes=5))
    assert _item(db_session, dealership.id).state == StatusState.OK
    _call(db_session, client_connection, provider_error=reason)
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED
    # An older refusal under a newer success no longer counts.
    _call(db_session, client_connection, at=NOW + dt.timedelta(minutes=1), status=CallStatus.SUCCESS, http_status=200)
    assert _item(db_session, dealership.id).state == StatusState.OK


def test_an_open_breaker_interrupts_and_staff_see_it(client, db_session, google, client_connection):
    dealership = _make_dealership(db_session)
    _bound(db_session, dealership.id)
    for _ in range(resilience._FAILURE_THRESHOLD):
        resilience.record_failure(client_connection.id)
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED
    body = client.get(CLIENT_STATUS, headers=platform_admin_headers()).json()
    assert body["breakerOpen"] is True and body["interrupted"] is True and body["refusal"] is None


def test_no_enabled_client_with_a_live_binding_interrupts(client, db_session, google, client_connection):
    dealership = _make_dealership(db_session)
    _bound(db_session, dealership.id)
    client_connection.enabled = False
    db_session.commit()
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED
    body = client.get(CLIENT_STATUS, headers=platform_admin_headers()).json()
    assert body == {
        "configured": False,
        "interrupted": True,
        "breakerOpen": False,
        "refusal": None,
        "refusedAt": None,
        "lastCallAt": None,
        "boundDealerships": 1,
    }


def test_the_betriebs_status_page_shows_the_item_over_the_api(client, db_session, google, client_connection, betrieb):
    dealership, manager, _ = betrieb
    _bound(db_session, dealership.id)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED", at=dt.datetime.now(dt.UTC))
    response = client.get(f"/v1/dealerships/{dealership.id}/status", headers=_as(manager))
    assert response.status_code == 200, response.text
    items = [item for item in response.json()["items"] if item["source"] == "google_business_profile"]
    assert len(items) == 1
    assert items[0]["state"] == "blocked" and items[0]["link"] is None and items[0]["params"] == {}
    assert "SERVICE_DISABLED" not in response.text


def test_only_staff_read_the_clients_cause(client, db_session, google, client_connection, betrieb):
    _, manager, _ = betrieb
    assert client.get(CLIENT_STATUS, headers=_as(manager)).status_code == 403


def test_the_status_read_never_half_opens_the_breaker(db_session, google, client_connection, monkeypatch):
    """`circuit_is_open_now` reports an expired open state as closed but
    leaves it for the next call, so reading the status page changes nothing."""

    for _ in range(resilience._FAILURE_THRESHOLD):
        resilience.record_failure(client_connection.id)
    circuit = resilience._CIRCUITS[client_connection.id]
    circuit.opened_at = time.monotonic() - resilience._OPEN_DURATION_SECONDS - 1
    assert resilience.circuit_is_open_now(client_connection.id) is False
    assert circuit.opened_at is not None and circuit.failure_count == resilience._FAILURE_THRESHOLD
    assert resilience.circuit_is_open_now(uuid.uuid4()) is False
    assert uuid.uuid4() not in resilience._CIRCUITS


def test_the_client_refusals_are_adr_196s_project_refusals_plus_invalid_client():
    assert gbp_status.CLIENT_REFUSALS == gbp.PROJECT_REFUSALS | {"invalid_client"}
