"""WP-6 PR-2: the mock adapter and the provider-gateway itself — realistic
shapes, one call-log row per call, no business data written from here.
"""

import datetime as dt
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import list_audit_events
from app.core.auth import create_access_token
from app.core.config import get_settings
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.errors import ProviderGatewayError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway, metering, resilience
from app.vehicle.models.catalogue import Brand
from tests.token_support import dealer_group_of

# The gateway's own session is a second database connection. On the SQLite
# fast lane every session shares one connection (StaticPool), so these
# tests can only run on Postgres, the lane of record (ADR-011).
_postgres_only = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="needs a second database connection; SQLite shares one across sessions (ADR-011)",
)


def _make_provider(db_session, **overrides) -> IntegrationProvider:
    defaults = {
        "provider_code": "auto_i_dat_mock",
        "category": "vehicle_data",
        "display_name": "auto-i-dat (mock)",
        "auth_type": "none",
        "required_secret_slots": [],
        "capability_codes": ["vehicle_data", "images", "packages", "valuation", "forecast"],
    }
    defaults.update(overrides)
    provider = IntegrationProvider(**defaults)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider, *, tenant_id=None, enabled=True):
    connection = connection_service.create_connection(
        db_session,
        tenant_id=tenant_id or uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )
    if not enabled:
        connection = connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    return connection


# --- the mock adapter itself -------------------------------------------------


def test_mock_adapter_returns_realistic_shapes():
    adapter = MockAutoIDatAdapter()
    master = adapter.fetch_vehicle_master_data("FZ100001")
    assert master.brand_display_name == "Alfa Romeo"
    assert master.base_price is not None

    # KAN-38 PR 1: fetch_options needs the model year (Optionen.Jahr is
    # obligatorisch); fetch_colours keys on Werkscode, not FzKey.
    options = adapter.fetch_options("FZ100001", model_year=master.model_year_from)
    assert len(options) >= 1
    assert options[0].description  # never empty, "as delivered" text
    assert master.werkscode is not None
    assert isinstance(adapter.fetch_colours(werkscode=master.werkscode), list)

    watermark = adapter.get_system_watermark()
    assert watermark.update_date is not None


def test_mock_adapter_system_watermark_is_injectable_for_staleness_tests():
    stale_date = dt.date(2020, 1, 1)
    adapter = MockAutoIDatAdapter(system_watermark_date=stale_date)
    assert adapter.get_system_watermark().update_date == stale_date


def test_mock_adapter_unknown_fz_key_raises():
    adapter = MockAutoIDatAdapter()
    try:
        adapter.fetch_vehicle_master_data("NOPE")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


# --- the gateway --------------------------------------------------------------


def test_call_capability_writes_one_call_log_row_with_status_duration_correlation_id(db_session):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    correlation_id = uuid.uuid4()

    with gateway.call_capability(
        db_session, connection=connection, capability="vehicle_data", correlation_id=correlation_id
    ) as adapter:
        adapter.fetch_vehicle_master_data("FZ100001")

    logs = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert len(logs) == 1
    assert logs[0].status == CallStatus.SUCCESS
    # KAN-486: the request is named by its Datenname, and counts one unit.
    assert logs[0].capability == "Fahrzeuge"
    assert logs[0].cost_units == Decimal(1)
    assert logs[0].correlation_id == correlation_id
    assert logs[0].duration_ms >= 0


# --- KAN-486: one call-log row per provider request -----------------------------


def test_every_request_in_a_block_is_its_own_row_and_the_breaker_is_charged_once(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    successes = []
    monkeypatch.setattr(resilience, "record_success", successes.append)

    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
        adapter.fetch_vehicle_master_data("FZ100001")
        adapter.fetch_options("FZ100001", model_year=2023)
        adapter.fetch_images("FZ100001")

    logs = db_session.scalars(
        select(IntegrationCallLog).filter_by(connection_id=connection.id).order_by(IntegrationCallLog.created_at)
    ).all()
    assert [(log.capability, log.status) for log in logs] == [
        ("Fahrzeuge", CallStatus.SUCCESS), ("Optionen", CallStatus.SUCCESS), ("Bilder", CallStatus.SUCCESS),
    ]
    assert len({log.correlation_id for log in logs}) == 1  # one block, one correlation
    assert successes == [connection.id]


def test_a_block_that_sends_no_request_still_writes_its_one_row(db_session):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data"):
        pass

    logs = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert [(log.capability, log.status, log.cost_units) for log in logs] == [("vehicle_data", CallStatus.SUCCESS, None)]


def test_a_retried_request_is_two_rows(db_session, monkeypatch):
    """`call_with_retry` around one request sends it twice: the provider
    counts two, so the log does."""

    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0, 0))
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    answers = iter([TimeoutError("no answer"), "<ok/>"])

    def send():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with (
        gateway.call_capability(db_session, connection=connection, capability="vehicle_data"),
        metering.provider_request("Fahrzeuge", is_timeout=lambda exc: isinstance(exc, TimeoutError)) as request,
    ):
        assert resilience.call_with_retry(lambda: request.attempt(send)) == "<ok/>"

    logs = db_session.scalars(
        select(IntegrationCallLog).filter_by(connection_id=connection.id).order_by(IntegrationCallLog.created_at)
    ).all()
    assert [(log.capability, log.status) for log in logs] == [
        ("Fahrzeuge", CallStatus.TIMEOUT), ("Fahrzeuge", CallStatus.SUCCESS),
    ]


def test_an_answer_refused_after_it_arrived_is_that_requests_error(db_session):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    with (
        pytest.raises(ProviderGatewayError),
        gateway.call_capability(db_session, connection=connection, capability="vehicle_data"),
        metering.provider_request("Fahrzeuge") as request,
    ):
        request.attempt(lambda: "")
        raise ProviderGatewayError("empty answer: rejected login")

    logs = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert [(log.capability, log.status) for log in logs] == [("Fahrzeuge", CallStatus.ERROR)]


def test_a_block_failing_after_its_answer_arrived_marks_that_request_failed(db_session, monkeypatch):
    """The answer arrived (a SUCCESS on the wire), then the caller refused it
    — a `Fahrzeuge` miss read through `.first`, say. The block is charged to
    the breaker, and the log must not show that request as a success."""

    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    charged = []
    monkeypatch.setattr(resilience, "record_failure", charged.append)

    with (
        pytest.raises(ProviderGatewayError),
        gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter,
    ):
        adapter.fetch_options("FZ100001", model_year=2023)
        adapter.fetch_vehicle_master_data("FZ100001")
        raise ProviderGatewayError("Fahrzeuge: no row for this key")

    logs = db_session.scalars(
        select(IntegrationCallLog).filter_by(connection_id=connection.id).order_by(IntegrationCallLog.created_at)
    ).all()
    # Two requests, two rows; the last is the one whose answer was refused.
    assert [(log.capability, log.status) for log in logs] == [
        ("Optionen", CallStatus.SUCCESS), ("Fahrzeuge", CallStatus.ERROR),
    ]
    assert charged == [connection.id]


def test_one_mock_request_is_one_row(db_session):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
        adapter.list_model_groups_short(fz_art="1")

    logs = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert [log.capability for log in logs] == ["ModellGruppenKurz"]


def test_an_adapter_used_outside_a_block_meters_nothing(db_session):
    assert MockAutoIDatAdapter().fetch_vehicle_master_data("FZ100001").fz_key == "FZ100001"
    assert db_session.query(IntegrationCallLog).count() == 0


# --- KAN-489: a sandbox connection is never used on the production server -------


@pytest.mark.parametrize("deployment", ["production", "Production", "prod", "staging", "development"])
def test_a_sandbox_connection_is_refused_only_on_a_production_deployment(db_session, monkeypatch, deployment):
    monkeypatch.setattr(get_settings(), "environment", deployment)
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)  # environment=SANDBOX
    actor_id = uuid.uuid4()

    charged = []
    monkeypatch.setattr(resilience, "record_failure", charged.append)

    if deployment.lower().startswith("prod"):
        with pytest.raises(gateway.SandboxConnectionOnProductionError), gateway.call_capability(
            db_session, connection=connection, capability="vehicle_data", actor_id=actor_id
        ) as adapter:
            adapter.fetch_vehicle_master_data("FZ100001")
        # Refused before the provider: no call-log row, the breaker untouched,
        # and an audit row naming the refusal.
        assert db_session.query(IntegrationCallLog).count() == 0
        assert charged == []
        events = list_audit_events(
            db_session, entity_type="integration_connection", entity_id=connection.id, tenant_id=None
        )
        refusal = [event for event in events if event.action == "sandbox_refused_in_production"]
        assert len(refusal) == 1
        assert refusal[0].actor_id == actor_id
        assert refusal[0].tenant_id == connection.tenant_id
    else:
        with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
            adapter.fetch_vehicle_master_data("FZ100001")
        assert db_session.query(IntegrationCallLog).count() == 1


def test_a_production_connection_is_used_on_a_production_deployment(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")
    provider = _make_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.PRODUCTION),
        actor_id=uuid.uuid4(),
    )

    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
        adapter.fetch_vehicle_master_data("FZ100001")

    assert db_session.query(IntegrationCallLog).count() == 1


def test_test_connection_shows_the_production_refusal_on_the_card(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    tested = gateway.test_connection(db_session, connection=connection)

    assert tested.status == ConnectionStatus.ERROR
    assert "never used on the production server" in tested.last_error


def test_call_capability_logs_error_status_and_reraises_on_failure(db_session):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    raised = False
    try:
        with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
            adapter.fetch_vehicle_master_data("NOPE")
    except KeyError:
        raised = True
    assert raised

    logs = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert len(logs) == 1
    assert logs[0].status == CallStatus.ERROR


def test_call_capability_refuses_a_disabled_connection(db_session):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider, enabled=False)

    try:
        with gateway.call_capability(db_session, connection=connection, capability="vehicle_data"):
            pass
        raise AssertionError("expected ConnectionDisabledError")
    except gateway.ConnectionDisabledError:
        pass

    # Refusing before ever resolving an adapter means no call log at all —
    # "no business data" extends to not even logging a call that never
    # reached the provider.
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 0


def test_call_capability_unknown_provider_code_raises(db_session):
    provider = _make_provider(db_session, provider_code="not_registered")
    connection = _make_connection(db_session, provider)

    try:
        with gateway.call_capability(db_session, connection=connection, capability="vehicle_data"):
            pass
        raise AssertionError("expected UnknownProviderError")
    except gateway.UnknownProviderError:
        pass


def test_gateway_writes_no_business_data_only_call_log(db_session):
    """The provider-gateway's own "no business data" rule — this module
    must never write anything but integration_call_log."""

    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
        master = adapter.fetch_vehicle_master_data("FZ100001")
        adapter.fetch_options("FZ100001", model_year=master.model_year_from)
        adapter.fetch_colours(werkscode=master.werkscode)

    # Nothing in app.vehicle's own tables was touched — no ModelVariant/
    # VariantOption row exists anywhere, since resolving provider codes
    # into canonical catalogue rows is app.vehicle's own job (PR-4), never
    # this module's.
    from app.vehicle.models.catalogue import ModelVariant

    assert db_session.query(ModelVariant).count() == 0


@_postgres_only
def test_a_failed_call_never_commits_the_callers_half_written_rows(db_session, engine):
    """ADR-047 (KAN-90): the gateway's log row is integration's own write
    and is committed on its own; a row the caller wrote inside the block
    belongs to the caller's transaction and dies with the caller's
    rollback. Before KAN-90, record_call committed the caller's session, so
    a catalogue sync that failed half-way kept its half-written rows."""

    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    try:
        with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
            db_session.add(Brand(code="half-written", display_name="Half written"))
            db_session.flush()
            adapter.fetch_vehicle_master_data("NOPE")
        raise AssertionError("expected KeyError")
    except KeyError:
        db_session.rollback()

    with Session(engine) as fresh:
        assert fresh.query(Brand).filter_by(code="half-written").count() == 0
        logs = fresh.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
        assert [log.status for log in logs] == [CallStatus.ERROR]


@_postgres_only
def test_a_successful_call_leaves_the_callers_work_for_the_caller_to_commit(db_session, engine):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
        db_session.add(Brand(code="callers-own", display_name="Caller's own"))
        db_session.flush()
        adapter.fetch_vehicle_master_data("FZ100001")

    with Session(engine) as fresh:
        assert fresh.query(Brand).filter_by(code="callers-own").count() == 0  # not committed by the gateway
        assert fresh.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 1
    db_session.commit()
    with Session(engine) as fresh:
        assert fresh.query(Brand).filter_by(code="callers-own").count() == 1


@_postgres_only
def test_a_captured_payload_is_committed_with_the_log_not_with_the_callers_work(db_session, engine):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    with gateway.call_capability(
        db_session, connection=connection, capability="vehicle_data", capture_raw_payload=lambda: "<raw/>"
    ) as adapter:
        db_session.add(Brand(code="pending", display_name="Pending"))
        db_session.flush()
        adapter.fetch_vehicle_master_data("FZ100001")

    with Session(engine) as fresh:
        log = fresh.scalars(select(IntegrationCallLog).filter_by(connection_id=connection.id)).one()
        assert fresh.scalars(select(IntegrationCallPayload).filter_by(call_log_id=log.id)).one().payload == "<raw/>"
        assert fresh.query(Brand).filter_by(code="pending").count() == 0
    db_session.rollback()


@_postgres_only
def test_a_caller_locking_its_own_connection_row_is_logged_without_waiting(db_session, monkeypatch):
    """Until KAN-470 the log row's foreign key waited on the connection row
    from the gateway's own session, so a caller holding that row locked
    waited on itself (bounded by `_LOG_LOCK_TIMEOUT`, then an error). The
    call log has no foreign key now: the call is logged at once. A wait
    reintroduced later still fails here with the bound's lock timeout
    instead of hanging."""

    from app.integration.models.call_log import IntegrationCallLog
    from app.integration.models.connection import IntegrationConnection

    monkeypatch.setattr(gateway, "_LOG_LOCK_TIMEOUT", "200ms")
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    db_session.scalars(select(IntegrationConnection).filter_by(id=connection.id).with_for_update()).one()

    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
        adapter.fetch_vehicle_master_data("FZ100001")
    db_session.rollback()

    logs = db_session.scalars(select(IntegrationCallLog).filter_by(connection_id=connection.id)).all()
    assert [log.capability for log in logs] == ["Fahrzeuge"]  # the request's Datenname (KAN-486)


def test_test_connection_endpoint_updates_status_and_last_verified(db_session):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    assert connection.status == ConnectionStatus.NOT_CONFIGURED

    updated = gateway.test_connection(db_session, connection=connection)
    assert updated.status == ConnectionStatus.CONNECTED
    assert updated.last_verified_at is not None
    assert updated.last_error is None


def test_test_connection_marks_error_status_when_the_provider_is_unregistered(db_session):
    provider = _make_provider(db_session, provider_code="not_registered")
    connection = _make_connection(db_session, provider)

    updated = gateway.test_connection(db_session, connection=connection)
    assert updated.status == ConnectionStatus.ERROR
    assert updated.last_error is not None


# --- API surface ---------------------------------------------------------------


def _token(tenant_id: uuid.UUID, *, is_dealer_manager: bool = True) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_test_connection_endpoint(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "auto-i-dat", "environment": "sandbox"},
        headers=_bearer(token),
    ).json()

    tested = client.post(f"/v1/integrations/connections/{created['id']}/test", headers=_bearer(token))
    assert tested.status_code == 200, tested.text
    assert tested.json()["status"] == "connected"
    assert tested.json()["lastVerifiedAt"] is not None
