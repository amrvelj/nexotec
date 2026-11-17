"""KAN-223 (D-A-38, FR-A-43; Anto, 2026-11-14, Q1 A, Q2 A): the Betrieb's
status page is derived on every read from its sources — number ranges,
integration connections (a contribution through `app.core.status`) and
Nexotec's own incidents — readable by every role of the Betrieb, 404 for
another; staff write the incidents; the daily job tells the managers once
per degradation until it clears."""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.permissions import ModuleCode
from app.core.status import (
    StatusItem,
    StatusState,
    register_status_provider,
    status_providers,
    unregister_status_provider,
)
from app.integration.models.connection import (
    ConnectionEnvironment,
    ConnectionScope,
    ConnectionStatus,
    IntegrationConnection,
)
from app.integration.models.notification import IntegrationNotification, NotificationKind
from app.integration.models.provider import IntegrationProvider
from app.integration.public import IntegrationStatusProvider
from app.platform.administration.models.status_page import IncidentSeverity, PlatformStatusIncident, StatusItemState
from app.platform.administration.services import status_notifications, status_page
from app.platform.identity.models.notification import Notification
from app.platform.identity.services.notification_catalogue import NotificationEventCode
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.models.number_range import NumberRangeDocumentType
from app.platform.organisation.services import number_range as number_range_service
from tests import setup_checklist_support as s
from tests.identity_support import platform_admin_headers
from tests.test_customer_vehicle_party_close_rule import _postgres_only, _run, _wait_until_a_backend_waits_on_a_lock
from tests.token_support import dealer_group_of

NOW = dt.datetime(2026, 11, 15, 10, 0, tzinfo=dt.UTC)
TEXTS = {"de": "Störung", "fr": "Panne", "it": "Guasto", "en": "Outage"}


def _active(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    db_session.get(Dealership, dealership_id).status = DealershipStatus.ACTIVE
    db_session.commit()
    return dealership_id, boss


def _provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code=f"demo_{uuid.uuid4().hex[:8]}",
        category="marketplace",
        display_name="AutoScout24",
        auth_type="api_key",
        required_secret_slots=[],
        capability_codes=[],
    )
    db_session.add(provider)
    db_session.commit()
    return provider


def _connection(db_session, tenant_id, *, status=ConnectionStatus.CONNECTED, expires_at=None, enabled=True, name="Inserate"):
    row = IntegrationConnection(
        tenant_id=tenant_id,
        provider_id=_provider(db_session).id,
        scope=ConnectionScope.TENANT,
        display_name=name,
        environment=ConnectionEnvironment.PRODUCTION,
        status=status,
        expires_at=expires_at,
        enabled=enabled,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _range(db_session, dealership_id, document_type=NumberRangeDocumentType.OFFER, *, last_value, last_year=None):
    row = number_range_service.get_or_seed(db_session, dealership_id=dealership_id, document_type=document_type, lock=False)
    row.last_value = last_value
    row.last_year = last_year or number_range_service.swiss_year()
    db_session.commit()
    return row


def _incident(db_session, *, severity=IncidentSeverity.BLOCKED, starts_at=None, ends_at=None):
    row = PlatformStatusIncident(
        severity=severity,
        title=TEXTS,
        body=TEXTS,
        starts_at=starts_at or NOW - dt.timedelta(hours=1),
        ends_at=ends_at,
        created_by=uuid.uuid4(),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _items(db_session, dealership_id, now=NOW) -> dict[str, StatusItem]:
    return {item.key: item for item in status_page.derive(db_session, dealership_id, now=now).items}


def _headers(user, dealership_id, *, roles=frozenset({AccessRole.SALES}), manager=False):
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=dealership_id,
        group_id=dealer_group_of(dealership_id),
        roles=roles,
        is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


# --- Derivation ---------------------------------------------------------------------------


def test_the_page_composes_number_ranges_integrations_and_nexotec(client, db_session):
    dealership_id, _ = _active(client, db_session)
    connection = _connection(db_session, dealership_id)
    page = status_page.derive(db_session, dealership_id, now=NOW)
    sources = {item.source for item in page.items}
    assert sources == {"number_ranges", "integration", "nexotec"}
    keys = {item.key for item in page.items}
    assert "number_range.offer" in keys
    assert f"integration.connection.{connection.id}" in keys
    assert "nexotec.platform" in keys
    assert page.state == StatusState.OK  # all green


def test_the_integration_contribution_is_registered_by_the_composition_root():
    import app.status_registry  # noqa: F401 — the root registers on import

    assert "integration" in {provider.source for provider in status_providers()}


def test_number_ranges_follow_the_held_modules(client, db_session):
    workshop = s.pending_dealership(client, db_session, modules=(ModuleCode.AFTERSALES,))
    keys = set(_items(db_session, workshop))
    assert "number_range.offer" not in keys
    assert "number_range.service_order" in keys


def test_a_number_range_warns_at_ninety_percent_and_blocks_when_full(client, db_session):
    dealership_id, _ = _active(client, db_session)  # offer: O-{counter:6}, capacity 999'999
    _range(db_session, dealership_id, last_value=899_999)
    assert _items(db_session, dealership_id)["number_range.offer"].state == StatusState.OK
    _range(db_session, dealership_id, last_value=900_000)
    item = _items(db_session, dealership_id)["number_range.offer"]
    assert (item.state, item.params["percent"]) == (StatusState.WARNING, 90)
    assert item.link == "/administration/number-ranges"
    _range(db_session, dealership_id, last_value=999_999)
    item = _items(db_session, dealership_id)["number_range.offer"]
    assert (item.state, item.message_key) == (StatusState.BLOCKED, "statusPage.items.numberRange.exhausted")


def test_a_yearly_range_full_last_year_is_green_again(client, db_session):
    dealership_id, _ = _active(client, db_session)  # order confirmation: AB-{year}-{counter:5}, yearly
    year = number_range_service.swiss_year()
    _range(db_session, dealership_id, NumberRangeDocumentType.ORDER_CONFIRMATION, last_value=99_999, last_year=year - 1)
    assert _items(db_session, dealership_id)["number_range.order_confirmation"].state == StatusState.OK
    _range(db_session, dealership_id, NumberRangeDocumentType.ORDER_CONFIRMATION, last_value=99_999, last_year=year)
    assert _items(db_session, dealership_id)["number_range.order_confirmation"].state == StatusState.BLOCKED


@pytest.mark.parametrize(
    ("status", "expires_in_days", "state", "message", "by_source"),
    [
        (ConnectionStatus.CONNECTED, None, StatusState.OK, "ok", False),
        (ConnectionStatus.CONNECTED, 31, StatusState.OK, "ok", False),
        (ConnectionStatus.CONNECTED, 30, StatusState.WARNING, "expiring", False),  # no notice sent yet
        (ConnectionStatus.CONNECTED, 0, StatusState.BLOCKED, "expired", False),
        (ConnectionStatus.EXPIRED, None, StatusState.BLOCKED, "expired", False),
        (ConnectionStatus.ERROR, None, StatusState.BLOCKED, "error", False),
        (ConnectionStatus.NOT_CONFIGURED, None, StatusState.WARNING, "notTested", False),
    ],
)
def test_each_connection_state_maps_to_an_item(client, db_session, status, expires_in_days, state, message, by_source):
    dealership_id, _ = _active(client, db_session)
    expires_at = NOW + dt.timedelta(days=expires_in_days) if expires_in_days is not None else None
    connection = _connection(db_session, dealership_id, status=status, expires_at=expires_at)
    item = _items(db_session, dealership_id)[f"integration.connection.{connection.id}"]
    assert item.state == state
    assert item.message_key == f"statusPage.items.integration.{message}"
    assert item.notified_by_source is by_source
    assert item.link == "/administration/integrations"
    assert item.params["provider"] == "AutoScout24"


def test_a_switched_off_or_foreign_connection_is_not_an_item(client, db_session):
    dealership_id, _ = _active(client, db_session)
    other, _ = _active(client, db_session)
    _connection(db_session, dealership_id, status=ConnectionStatus.DISABLED, enabled=False)
    _connection(db_session, other, status=ConnectionStatus.ERROR)
    assert not [key for key in _items(db_session, dealership_id) if key.startswith("integration.")]


def test_a_current_incident_shows_on_every_betrieb_and_an_ended_one_does_not(client, db_session):
    first, _ = _active(client, db_session)
    second, _ = _active(client, db_session)
    current = _incident(db_session, severity=IncidentSeverity.WARNING)
    _incident(db_session, ends_at=NOW - dt.timedelta(minutes=1))
    _incident(db_session, starts_at=NOW + dt.timedelta(hours=1))
    for dealership_id in (first, second):
        page = status_page.derive(db_session, dealership_id, now=NOW)
        nexotec = [item for item in page.items if item.source == "nexotec"]
        assert [item.key for item in nexotec] == [f"nexotec.incident.{current.id}"]
        assert nexotec[0].state == StatusState.WARNING
        assert page.state == StatusState.WARNING
        assert page.incidents[nexotec[0].key].id == current.id


class _Failing:
    source = "failing"

    def items(self, db, *, dealership_id, now):
        raise RuntimeError("source down")


def test_a_failing_source_is_a_blocked_item_never_green(client, db_session):
    dealership_id, _ = _active(client, db_session)
    register_status_provider(_Failing())
    try:
        page = status_page.derive(db_session, dealership_id, now=NOW)
    finally:
        unregister_status_provider("failing")
    item = next(item for item in page.items if item.source == "failing")
    assert (item.state, item.message_key) == (StatusState.BLOCKED, "statusPage.items.sourceUnavailable")
    assert page.state == StatusState.BLOCKED
    assert any(item.source == "nexotec" for item in page.items)  # the sources after it still answer


# --- The endpoint -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roles",
    [
        frozenset({AccessRole.SALES}),
        frozenset({AccessRole.AFTERSALES}),
        frozenset({AccessRole.TECHNICIAN}),
        frozenset({AccessRole.FINANCE}),
        frozenset({AccessRole.INVENTORY}),
        frozenset({AccessRole.PARTS}),
    ],
)
def test_every_role_reads_the_page(client, db_session, roles):
    dealership_id, boss = _active(client, db_session)
    response = client.get(f"/v1/dealerships/{dealership_id}/status", headers=_headers(boss, dealership_id, roles=roles))
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "ok"


def test_the_page_names_what_is_not_green_and_counts_it(client, db_session):
    dealership_id, boss = _active(client, db_session)
    _range(db_session, dealership_id, last_value=999_999)
    _connection(db_session, dealership_id, status=ConnectionStatus.ERROR)
    incident = _incident(db_session, severity=IncidentSeverity.WARNING, starts_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))

    body = client.get(f"/v1/dealerships/{dealership_id}/status", headers=_headers(boss, dealership_id, manager=True)).json()
    assert body["state"] == "blocked"
    assert body["attentionCount"] == 3
    by_key = {item["key"]: item for item in body["items"]}
    assert by_key["number_range.offer"]["state"] == "blocked"
    assert by_key["number_range.offer"]["params"] == {"documentType": "offer", "percent": 100}
    nexotec = by_key[f"nexotec.incident.{incident.id}"]
    assert nexotec["incident"]["title"] == TEXTS
    assert nexotec["link"] is None


def test_another_betriebs_page_is_404(client, db_session):
    dealership_id, boss = _active(client, db_session)
    other, _ = _active(client, db_session)
    response = client.get(f"/v1/dealerships/{other}/status", headers=_headers(boss, dealership_id, manager=True))
    assert response.status_code == 404


def test_a_read_never_notifies(client, db_session):
    dealership_id, boss = _active(client, db_session)
    _connection(db_session, dealership_id, status=ConnectionStatus.ERROR)
    client.get(f"/v1/dealerships/{dealership_id}/status", headers=_headers(boss, dealership_id, manager=True))
    assert db_session.scalars(select(StatusItemState)).all() == []
    assert _degraded_notifications(db_session) == []


# --- Nexotec's incidents (staff) ----------------------------------------------------------


def _incident_body(**overrides):
    return {"severity": "blocked", "title": TEXTS, "body": {**TEXTS, "en": "We are on it."}, **overrides}


def test_staff_open_edit_and_end_an_incident_audited(client, db_session):
    created = client.post("/v1/platform/status-incidents", json=_incident_body(), headers=platform_admin_headers())
    assert created.status_code == 201, created.text
    incident = created.json()
    assert incident["endsAt"] is None and incident["version"] == 1

    without = client.patch(
        f"/v1/platform/status-incidents/{incident['id']}", json={"severity": "warning"}, headers=platform_admin_headers()
    )
    assert without.status_code == 400  # If-Match required
    stale = client.patch(
        f"/v1/platform/status-incidents/{incident['id']}",
        json={"severity": "warning"},
        headers={**platform_admin_headers(), "If-Match": "7"},
    )
    assert stale.status_code == 409

    ended_at = (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)).isoformat()
    ended = client.patch(
        f"/v1/platform/status-incidents/{incident['id']}",
        json={"severity": "warning", "endsAt": ended_at},
        headers={**platform_admin_headers(), "If-Match": "1"},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["severity"] == "warning"
    assert ended.json()["endsAt"] is not None and ended.json()["version"] == 2

    listed = client.get("/v1/platform/status-incidents", headers=platform_admin_headers()).json()
    assert [row["id"] for row in listed["items"]] == [incident["id"]]
    actions = [
        row.action
        for row in db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "platform_status_incident").order_by(AuditEvent.created_at)
        )
    ]
    assert actions == ["status_incident_opened", "status_incident_updated"]


def test_an_incident_needs_all_four_languages(client, db_session):
    response = client.post(
        "/v1/platform/status-incidents",
        json=_incident_body(title={**TEXTS, "it": "  "}),
        headers=platform_admin_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "status_incident_language_missing"
    missing = client.post(
        "/v1/platform/status-incidents",
        json=_incident_body(title={"de": "Störung", "fr": "Panne", "it": "Guasto"}),
        headers=platform_admin_headers(),
    )
    assert missing.status_code == 422


def test_an_incident_ends_after_it_starts(client, db_session):
    response = client.post(
        "/v1/platform/status-incidents",
        json=_incident_body(startsAt="2026-11-15T10:00:00Z", endsAt="2026-11-15T09:00:00Z"),
        headers=platform_admin_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "status_incident_ends_before_start"


def test_only_staff_with_a_factor_write_incidents(client, db_session):
    dealership_id, boss = _active(client, db_session)
    dealer = client.post(
        "/v1/platform/status-incidents", json=_incident_body(), headers=_headers(boss, dealership_id, manager=True)
    )
    assert dealer.status_code == 403
    assert client.get("/v1/platform/status-incidents", headers=_headers(boss, dealership_id, manager=True)).status_code == 403
    no_factor = client.post("/v1/platform/status-incidents", json=_incident_body(), headers=platform_admin_headers(mfa=False))
    assert no_factor.status_code == 403


# --- Notifications ------------------------------------------------------------------------


def _degraded_notifications(db_session) -> list[Notification]:
    return list(
        db_session.scalars(
            select(Notification).where(Notification.event_code == NotificationEventCode.STATUS_ITEM_DEGRADED.value)
        ).all()
    )


def test_one_notification_per_degradation_none_while_degraded_and_a_new_one_after_it_cleared(client, db_session):
    dealership_id, boss = _active(client, db_session)
    connection = _connection(db_session, dealership_id)
    first = status_notifications.run(db_session)
    assert (first.degraded, first.notified) == (0, 0)

    connection.status = ConnectionStatus.NOT_CONFIGURED  # warning
    db_session.commit()
    run = status_notifications.run(db_session)
    assert (run.degraded, run.notified) == (1, 1)
    notices = _degraded_notifications(db_session)
    assert len(notices) == 1
    assert notices[0].user_id == uuid.UUID(boss["id"])
    assert notices[0].dealership_id == dealership_id
    assert notices[0].link == "/administration/status"
    assert notices[0].params == {"source": "integration", "state": "warning"}

    # Still degraded — worse, even — the next night: nothing more.
    connection.status = ConnectionStatus.ERROR
    db_session.commit()
    assert status_notifications.run(db_session).notified == 0
    assert status_notifications.run(db_session).notified == 0
    assert len(_degraded_notifications(db_session)) == 1
    state = db_session.scalar(select(StatusItemState).where(StatusItemState.item_key == f"integration.connection.{connection.id}"))
    assert state.state == StatusState.BLOCKED

    # Green again, then degraded again: a new notification.
    connection.status = ConnectionStatus.CONNECTED
    db_session.commit()
    assert status_notifications.run(db_session).cleared == 1
    connection.status = ConnectionStatus.ERROR
    db_session.commit()
    assert status_notifications.run(db_session).notified == 1
    assert len(_degraded_notifications(db_session)) == 2


def _expiry_notice_sent(db_session, connection, *, days_ago=0, threshold=30):
    """The record integration writes when it really sends a T-30/14/7 notice."""

    db_session.add(
        IntegrationNotification(
            connection_id=connection.id,
            tenant_id=connection.tenant_id,
            kind=NotificationKind.EXPIRY_WARNING,
            threshold_days=threshold,
            sent_date=dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days_ago),
            recipient="administration notification",
            summary="expires",
        )
    )
    db_session.commit()


def test_a_warning_the_source_really_announced_is_not_notified_twice_but_its_expiry_is(client, db_session):
    dealership_id, _ = _active(client, db_session)
    connection = _connection(db_session, dealership_id, expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=14))
    _expiry_notice_sent(db_session, connection, threshold=14)  # integration's T-14 notice went out today
    item = _items(db_session, dealership_id, now=dt.datetime.now(dt.UTC))[f"integration.connection.{connection.id}"]
    assert item.notified_by_source is True
    run = status_notifications.run(db_session)
    assert (run.degraded, run.notified) == (1, 0)
    row = db_session.scalar(select(StatusItemState))
    assert row.state == StatusState.WARNING and row.notified_at is None

    # It expires: integration says nothing at expiry, so the status job does — once.
    connection.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    db_session.commit()
    assert status_notifications.run(db_session).notified == 1
    assert status_notifications.run(db_session).notified == 0
    assert [notice.params["state"] for notice in _degraded_notifications(db_session)] == ["blocked"]


def test_an_expiry_nobody_announced_is_notified_once_through_to_expired(client, db_session):
    """Review finding: three days left is no T-30/14/7 day, so integration sends nothing."""

    dealership_id, _ = _active(client, db_session)
    connection = _connection(db_session, dealership_id, expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=3))
    assert status_notifications.run(db_session).notified == 1
    connection.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    db_session.commit()
    assert status_notifications.run(db_session).notified == 0  # told already, at the warning
    assert [notice.params["state"] for notice in _degraded_notifications(db_session)] == ["warning"]


def test_a_notice_for_an_earlier_expiry_does_not_count_for_this_one(client, db_session):
    dealership_id, _ = _active(client, db_session)
    connection = _connection(db_session, dealership_id, expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=10))
    _expiry_notice_sent(db_session, connection, days_ago=200)  # the previous credential's expiry
    item = _items(db_session, dealership_id, now=dt.datetime.now(dt.UTC))[f"integration.connection.{connection.id}"]
    assert item.notified_by_source is False
    assert status_notifications.run(db_session).notified == 1


class _IntegrationDown:
    """Integration's source failing: same source name, so it replaces the real one."""

    source = "integration"

    def items(self, db, *, dealership_id, now):
        raise RuntimeError("integration unavailable")


def test_a_source_that_cannot_be_asked_keeps_its_items_so_recovery_is_no_new_degradation(client, db_session):
    """Review finding: an unavailable source used to drop its rows as «gone»."""

    dealership_id, _ = _active(client, db_session)
    connection = _connection(db_session, dealership_id, status=ConnectionStatus.ERROR)
    assert status_notifications.run(db_session).notified == 1

    register_status_provider(_IntegrationDown())
    try:
        down = status_notifications.run(db_session)
        still_down = status_notifications.run(db_session)
    finally:
        register_status_provider(IntegrationStatusProvider())
    assert down.notified == 1  # the blocked «unavailable» item is announced once (exit criterion)
    assert still_down.notified == 0  # and not again while it stays down
    keys = {row.item_key for row in db_session.scalars(select(StatusItemState))}
    assert f"integration.connection.{connection.id}" in keys  # kept, not gone

    assert status_notifications.run(db_session).notified == 0  # recovered, still degraded: no new notice
    assert len(_degraded_notifications(db_session)) == 2  # the connection once, the outage once
    keys = {row.item_key for row in db_session.scalars(select(StatusItemState))}
    assert "integration.unavailable" not in keys


def test_two_concurrent_runs_for_one_betrieb_notify_once(engine, client, db_session, monkeypatch):
    """Review finding: two runs both read the row as ok and both notified."""

    _postgres_only(engine)
    dealership_id, _ = _active(client, db_session)
    connection = _connection(db_session, dealership_id)
    status_notifications.run(db_session)  # the row exists as ok
    connection.status = ConnectionStatus.ERROR
    db_session.commit()

    reached, release = threading.Event(), threading.Event()
    real_derive = status_page.derive

    def _derive_then_pause(db, dealership_id, *, now):
        page = real_derive(db, dealership_id, now=now)
        if threading.current_thread().name == "status-first":
            reached.set()
            release.wait(timeout=10)
        return page

    monkeypatch.setattr(status_page, "derive", _derive_then_pause)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _check():
        with factory() as session:
            status_notifications.check_dealership(session, session.get(Dealership, dealership_id))
            session.commit()

    first = _run("status-first", errors, _check)
    try:
        assert reached.wait(timeout=10), "the first run never reached its pause"
        second = _run("status-second", errors, _check)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the second waits on the Betrieb's lock
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
    finally:
        release.set()
    assert errors == []
    db_session.expire_all()
    assert len(_degraded_notifications(db_session)) == 1


def test_an_item_that_is_gone_drops_its_state(client, db_session):
    dealership_id, _ = _active(client, db_session)
    connection = _connection(db_session, dealership_id, status=ConnectionStatus.ERROR)
    status_notifications.run(db_session)
    assert len(db_session.scalars(select(StatusItemState)).all()) == 1
    db_session.delete(connection)
    db_session.commit()
    assert status_notifications.run(db_session).cleared == 1
    assert db_session.scalars(select(StatusItemState)).all() == []


def test_an_incident_notifies_the_managers_of_every_active_betrieb_once(client, db_session):
    _first, first_boss = _active(client, db_session)
    _second, second_boss = _active(client, db_session)
    pending = s.pending_dealership(client, db_session)
    s.manager(client, db_session, pending)
    _incident(db_session, starts_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5))
    assert status_notifications.run(db_session).notified == 2
    assert status_notifications.run(db_session).notified == 0
    assert {notice.user_id for notice in _degraded_notifications(db_session)} == {
        uuid.UUID(first_boss["id"]),
        uuid.UUID(second_boss["id"]),
    }


def test_a_colleague_who_is_not_a_manager_is_not_notified(client, db_session):
    dealership_id, boss = _active(client, db_session)
    clerk = s.manager(client, db_session, dealership_id, isDealerManager=False)
    _connection(db_session, dealership_id, status=ConnectionStatus.ERROR)
    status_notifications.run(db_session)
    recipients = {notice.user_id for notice in _degraded_notifications(db_session)}
    assert recipients == {uuid.UUID(boss["id"])}
    assert uuid.UUID(clerk["id"]) not in recipients


def test_the_daily_job_is_registered():
    from app.core.daily_scheduler import registered_job_names
    from app.worker import register_daily_jobs

    register_daily_jobs()
    assert "platform.status_page.notifications" in registered_job_names()
