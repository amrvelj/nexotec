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
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.status import StatusState, status_providers
from app.integration.adapters import google_business_profile as gbp
from app.integration.errors import ProviderAccountRefusedError, ProviderRejectedError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.models.google_business_profile import GBP_PROVIDER_CODE, GbpBindingStatus, GbpLocationBinding
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef, SecretSlot
from app.integration.services import gbp_client_check, gbp_status, resilience
from app.integration.services.gateway import call_capability
from app.platform.administration.services import status_notifications, status_page
from app.platform.identity.models.notification import Notification
from app.platform.organisation.models.dealership import Dealership
from tests import gbp_fakes
from tests.gbp_fakes import CLIENT_ID, CLIENT_SECRET, LOCATION_1, LOCATION_2
from tests.identapi_support import make_user
from tests.identity_support import platform_admin_headers
from tests.integration.test_kan226_business_profile import _as, _link, _location, _pick
from tests.inventory.test_inventory_invoicing_gate import _make_dealership

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
CLIENT_STATUS = "/v1/integrations/business-profile/client-status"


@pytest.fixture
def google(monkeypatch):
    fake, secrets_store = gbp_fakes.install(monkeypatch)
    yield fake, secrets_store
    resilience._CIRCUITS.clear()


@pytest.fixture
def client_connection(db_session, google):
    """Nexotec's OAuth client at Google: the platform-scope connection."""

    _, secrets_store = google
    provider = IntegrationProvider(
        provider_code=GBP_PROVIDER_CODE,
        category="marketplace",
        display_name="Google Business Profile",
        auth_type="oauth2",
        required_secret_slots=["client_secret"],
        required_config_keys=["clientId"],
        capability_codes=["business_profile"],
        supports_sandbox=False,
    )
    db_session.add(provider)
    db_session.flush()
    connection = IntegrationConnection(
        provider_id=provider.id,
        scope=ConnectionScope.PLATFORM,
        tenant_id=None,
        display_name="Nexotec GBP",
        environment=ConnectionEnvironment.PRODUCTION,
        config={"clientId": CLIENT_ID},
        enabled=True,
    )
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        IntegrationSecretRef(
            connection_id=connection.id,
            slot=SecretSlot.CLIENT_SECRET,
            secret_ref=f"/integrations/{connection.id}/client_secret",
        )
    )
    db_session.commit()
    secrets_store.values[(connection.id, "client_secret")] = CLIENT_SECRET
    return connection


@pytest.fixture
def betrieb(db_session):
    dealership = _make_dealership(db_session)
    manager = make_user(db_session, tenant_id=dealership.id, email=f"chef-{uuid.uuid4().hex[:6]}@example.test")
    return dealership, manager, _location(db_session, dealership.id)


def _item(db_session, dealership_id):
    items = [
        item
        for item in status_page.derive(db_session, dealership_id, now=NOW).items
        if item.source == "google_business_profile"
    ]
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


# --- Anto, 2026-11-21, decision 2 B: the managers' own notification -----------------------


def test_the_items_notification_is_a_catalogued_code():
    from app.platform.identity.services.notification_catalogue import BY_CODE

    assert gbp_status.NOTIFICATION_EVENT in BY_CODE


def test_an_interruption_tells_the_managers_once_with_its_own_notice_never_the_generic_one(
    client, db_session, google, client_connection
):
    from tests.platform.test_kan223_status_page import _active

    dealership_id, _ = _active(client, db_session)
    _bound(db_session, dealership_id)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED", at=dt.datetime.now(dt.UTC))
    dealership = db_session.get(Dealership, dealership_id)

    run = status_notifications.check_dealership(db_session, dealership)
    db_session.commit()
    assert run.notified >= 1
    codes = [n.event_code for n in db_session.scalars(select(Notification)).all()]
    assert "administration.business_profile_interrupted" in codes
    assert "administration.status_item_degraded" not in codes
    # Still interrupted the next day: nothing more.
    assert status_notifications.check_dealership(db_session, dealership).notified == 0


# --- Anto, 2026-11-21, decision 1 A: «Erneut prüfen» -------------------------------------

CHECK = "/v1/integrations/business-profile/client-status/check"


def _active_binding(client, fake, dealership, manager, location, google_location=LOCATION_1) -> uuid.UUID:
    binding = _link(client, fake, dealership, manager, location)["binding"]
    picked = _pick(client, dealership, manager, binding, location=google_location)
    assert picked.status_code == 200, picked.text
    return uuid.UUID(binding["id"])


def _staff_check(client, *, mfa=True, user_id=None):
    return client.post(CHECK, headers=platform_admin_headers(mfa=mfa, user_id=user_id))


def test_a_check_after_the_fix_clears_every_betriebs_item(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, manager, location = betrieb
    _active_binding(client, fake, dealership, manager, location)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED", at=dt.datetime.now(dt.UTC))
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED

    response = _staff_check(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "answered"
    assert body["status"]["interrupted"] is False and body["status"]["refusal"] is None
    db_session.expire_all()
    assert _item(db_session, dealership.id).state == StatusState.OK
    # One real request, logged on the client under its own purpose, and audited.
    rows = db_session.scalars(
        select(IntegrationCallLog).where(IntegrationCallLog.purpose == gbp_client_check.PURPOSE)
    ).all()
    assert {row.capability for row in rows} == {"oauth2_token_refresh", "locations_get"}
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "business_profile_client_checked")).all()
    assert len(audit) == 1 and audit[0].after == {"outcome": "answered"} and audit[0].entity_id == client_connection.id


def test_a_check_while_google_still_refuses_keeps_the_item_blocked(
    client, db_session, google, client_connection, betrieb
):
    fake, _ = google
    dealership, manager, location = betrieb
    _active_binding(client, fake, dealership, manager, location)
    fake.project_refusal = "SERVICE_DISABLED"
    body = _staff_check(client).json()
    assert body["outcome"] == "refused"
    assert body["status"]["interrupted"] is True and body["status"]["refusal"] == "SERVICE_DISABLED"
    db_session.expire_all()
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED


def test_a_refused_grant_is_the_bindings_and_the_next_binding_is_asked(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb
    first = _active_binding(client, fake, dealership, manager, location)
    second = _active_binding(
        client, fake, dealership, manager, _location(db_session, dealership.id, name="Bern"), LOCATION_2
    )
    fake.refused_refresh_tokens.add(secrets_store.values[(first, "refresh_token")])

    body = _staff_check(client).json()
    assert body["outcome"] == "answered"
    db_session.expire_all()
    assert db_session.get(GbpLocationBinding, first).status == GbpBindingStatus.ERROR
    assert db_session.get(GbpLocationBinding, second).status == GbpBindingStatus.ACTIVE


def test_a_check_needs_a_client_an_active_binding_and_a_closed_breaker(
    client, db_session, google, client_connection, betrieb
):
    fake, _ = google
    dealership, manager, location = betrieb
    response = _staff_check(client)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_no_active_binding"

    _active_binding(client, fake, dealership, manager, location)
    for _ in range(resilience._FAILURE_THRESHOLD):
        resilience.record_failure(client_connection.id)
    response = _staff_check(client)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_breaker_open"

    resilience._CIRCUITS.clear()
    client_connection.enabled = False
    db_session.commit()
    response = _staff_check(client)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_not_configured"


def test_only_staff_with_a_second_factor_check(client, db_session, google, client_connection, betrieb):
    _, manager, _ = betrieb
    assert client.post(CHECK, headers=_as(manager)).status_code == 403
    assert _staff_check(client, mfa=False).status_code == 403


# --- the second review (2026-11-21): only a deciding answer raises or clears it ----------


@pytest.mark.parametrize(
    ("capability", "http_status", "provider_error"),
    [
        ("accounts_list", None, None),  # a timeout or transport failure
        ("locations_get", 503, None),  # Google failed to answer
        ("locations_get", 429, "RATE_LIMIT_EXCEEDED"),  # a quota passes by itself
        ("oauth2_token_refresh", 200, None),  # a token says nothing about the project
        ("oauth2_token_refresh", 400, "invalid_grant"),  # one binding's grant
        # KAN-780: a 401 / 403 / 429 whose identifier names neither the client
        # nor the project is not Google judging them.
        ("locations_get", 401, "UNAUTHENTICATED"),  # the token refused first
        ("locations_get", 429, "RESOURCE_EXHAUSTED"),  # a 429 with no known quota reason
        ("locations_get", 403, "PERMISSION_DENIED"),  # one location's access
        ("locations_get", 403, None),  # no identifier at all
    ],
)
def test_a_row_that_proves_nothing_neither_clears_nor_raises_a_refusal(
    db_session, google, client_connection, capability, http_status, provider_error
):
    dealership = _make_dealership(db_session)
    _bound(db_session, dealership.id)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED", at=NOW - dt.timedelta(minutes=5))
    row = _call(db_session, client_connection, provider_error=provider_error, http_status=http_status)
    row.capability = capability
    db_session.commit()
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED
    health = gbp_status.client_health(db_session)
    assert health.refusal == "SERVICE_DISABLED" and health.last_call_at is not None


def test_an_invalid_client_at_the_token_endpoint_interrupts(db_session, google, client_connection):
    dealership = _make_dealership(db_session)
    _bound(db_session, dealership.id)
    row = _call(db_session, client_connection, provider_error="invalid_client", http_status=401)
    row.capability = "oauth2_token_refresh"
    db_session.commit()
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED


def test_a_check_while_google_fails_to_answer_is_unavailable_and_keeps_the_item_blocked(
    client, db_session, google, client_connection, betrieb
):
    fake, _ = google
    dealership, manager, location = betrieb
    _active_binding(client, fake, dealership, manager, location)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED", at=dt.datetime.now(dt.UTC))
    fake.project_refusal, fake.refusal_status = "SERVICE_DISABLED", 503
    body = _staff_check(client).json()
    assert body["outcome"] == "unavailable"
    assert body["status"]["interrupted"] is True and body["status"]["refusal"] == "SERVICE_DISABLED"
    db_session.expire_all()
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED


def test_a_check_with_every_grant_refused_keeps_the_item_blocked(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb
    binding = _active_binding(client, fake, dealership, manager, location)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED", at=dt.datetime.now(dt.UTC))
    fake.refused_refresh_tokens.add(secrets_store.values[(binding, "refresh_token")])
    staff_id = uuid.uuid4()
    response = _staff_check(client, user_id=staff_id)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_no_active_binding"
    db_session.expire_all()
    assert _item(db_session, dealership.id).state == StatusState.BLOCKED
    refused = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "business_profile_grant_refused")).all()
    # KAN-780: exactly one event, the staff user who checked as its actor.
    assert [event.actor_id for event in refused] == [staff_id]


def test_an_unreadable_grant_is_the_bindings_and_the_next_binding_is_asked(
    client, db_session, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, manager, location = betrieb
    first = _active_binding(client, fake, dealership, manager, location)
    second = _active_binding(
        client, fake, dealership, manager, _location(db_session, dealership.id, name="Bern"), LOCATION_2
    )
    del secrets_store.values[(first, "refresh_token")]
    body = _staff_check(client).json()
    assert body["outcome"] == "answered"
    db_session.expire_all()
    assert db_session.get(GbpLocationBinding, first).status == GbpBindingStatus.ACTIVE
    assert db_session.get(GbpLocationBinding, second).status == GbpBindingStatus.ACTIVE


def test_an_incomplete_client_is_not_configured(client, db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, manager, location = betrieb
    _active_binding(client, fake, dealership, manager, location)
    client_connection.config = {}
    db_session.commit()
    response = _staff_check(client)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_not_configured"


def test_an_item_naming_no_known_notification_gets_the_generic_one(client, db_session):
    from app.core.status import StatusItem
    from tests.platform.test_kan223_status_page import _active

    dealership_id, boss = _active(client, db_session)
    manager_id = uuid.UUID(boss["id"]) if isinstance(boss, dict) else boss.id
    item = StatusItem(
        key="x", source="x", state=StatusState.BLOCKED, message_key="x", link=None, notification_event="x.unknown"
    )
    subject = status_notifications.notifications.NotificationSubject(type="status_item", id=uuid.uuid4(), link="/")
    status_notifications._deliver(db_session, item, user_id=manager_id, dealership_id=dealership_id, subject=subject)
    db_session.flush()
    codes = [n.event_code for n in db_session.scalars(select(Notification)).all()]
    assert codes == ["administration.status_item_degraded"]


@pytest.mark.parametrize("http_status", [200, 404])
def test_a_success_or_an_answer_for_the_request_clears_a_refusal(db_session, google, client_connection, http_status):
    """KAN-780: what clears a refusal is Google answering for the request
    itself — a success, or a 400 / 404 / 422 for the location."""

    dealership = _make_dealership(db_session)
    _bound(db_session, dealership.id)
    _call(db_session, client_connection, provider_error="SERVICE_DISABLED", at=NOW - dt.timedelta(minutes=5))
    row = _call(
        db_session,
        client_connection,
        provider_error=None if http_status == 200 else "NOT_FOUND",
        http_status=http_status,
        status=CallStatus.SUCCESS if http_status == 200 else CallStatus.ERROR,
    )
    row.capability = "locations_get"
    db_session.commit()
    assert gbp_status.client_health(db_session).refusal is None
    assert _item(db_session, dealership.id).state == StatusState.OK


def test_an_unreadable_client_secret_is_not_configured_and_no_binding_is_touched(
    client, db_session, google, client_connection, betrieb
):
    """KAN-780: the client's secret reference exists but the secrets manager
    cannot resolve it. That is Nexotec's client not set up, never «no active
    binding»; the bindings stay as they are."""

    fake, secrets_store = google
    dealership, manager, location = betrieb
    first = _active_binding(client, fake, dealership, manager, location)
    second = _active_binding(
        client, fake, dealership, manager, _location(db_session, dealership.id, name="Thun"), LOCATION_2
    )
    del secrets_store.values[(client_connection.id, "client_secret")]
    response = _staff_check(client)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "business_profile_not_configured"
    db_session.expire_all()
    assert db_session.get(GbpLocationBinding, first).status == GbpBindingStatus.ACTIVE
    assert db_session.get(GbpLocationBinding, second).status == GbpBindingStatus.ACTIVE
