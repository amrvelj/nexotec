"""KAN-125: an event carries the correlation id of the request or event that set it off,
and the id of the event that caused it.

- An HTTP write's outbox row carries the response's `X-Correlation-Id`; a header that is
  not a UUID is replaced by a generated id (the response says which), never stored.
- An event a consumer emits carries its parent's `correlationId`, and the parent's
  `eventId` as its `causationId`; a log line inside the handler carries the correlation id.
- Each daily job run, and each pass of the web's data export runner, starts a fresh one.

Postgres is the lane of record (ADR-011); nothing here needs a Postgres-only feature.
"""

import datetime as dt
import json
import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core import daily_scheduler
from app.core.auth import AccessRole, create_access_token
from app.core.observability import CORRELATION_ID_HEADER, JSONLogFormatter, get_correlation_id
from app.core.outbox import OutboxEvent, publish
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import DeliveryError, InProcessTransport
from app.platform.administration.services import data_export, data_export_runner
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

_ADDRESS = {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"}


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _manager_token(client) -> str:
    admin = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=dealer_group_of(str(uuid.uuid4())),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=False,
    )
    response = client.post(
        "/v1/dealerships",
        json={"legalName": "Garage Korrelation AG", "franchiseType": "independent", "address": _ADDRESS,
              "phone": "+41441234567", "uid": valid_uid()},
        headers=_headers(admin),
    )
    assert response.status_code == 201, response.text
    dealership = response.json()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.UUID(dealership["id"]), group_id=uuid.UUID(dealership["dealerGroupId"]),
        roles=frozenset(), is_dealer_manager=True,
    )


def _create_customer(client, token: str, **headers: str):
    payload = {"firstName": "Anna", "lastName": "Muster", "language": "de",
               "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}]}
    response = client.post("/v1/customers", json=payload, headers=_headers(token, **headers))
    assert response.status_code == 201, response.text
    return response


def _customer_created(db_session, customer_id: str) -> OutboxMessage:
    db_session.expire_all()
    rows = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "customer.created",
                                    OutboxMessage.aggregate_id == uuid.UUID(customer_id))
    ).all()
    assert len(rows) == 1
    return rows[0]


# --- exit criterion 1 and 3: the request's id reaches the outbox ---------------------------------------


def test_an_http_writes_outbox_row_carries_the_responses_correlation_id(client, db_session):
    token = _manager_token(client)
    response = _create_customer(client, token)
    returned = response.headers[CORRELATION_ID_HEADER]
    row = _customer_created(db_session, response.json()["id"])
    assert row.correlation_id == uuid.UUID(returned)
    assert row.causation_id is None  # a request is no event


def test_a_supplied_correlation_id_reaches_the_outbox(client, db_session):
    token = _manager_token(client)
    supplied = str(uuid.uuid4())
    response = _create_customer(client, token, **{CORRELATION_ID_HEADER: supplied})
    assert response.headers[CORRELATION_ID_HEADER] == supplied
    assert _customer_created(db_session, response.json()["id"]).correlation_id == uuid.UUID(supplied)


def test_a_header_that_is_not_a_uuid_is_replaced_by_a_generated_id_never_stored(client, db_session):
    token = _manager_token(client)
    response = _create_customer(client, token, **{CORRELATION_ID_HEADER: "kan-125 not a uuid"})
    returned = response.headers[CORRELATION_ID_HEADER]
    assert returned != "kan-125 not a uuid"
    assert _customer_created(db_session, response.json()["id"]).correlation_id == uuid.UUID(returned)


# --- exit criterion 2 and 3: an event a consumer emits -------------------------------------------------


def _probe(event_type: str, **ids) -> OutboxEvent:
    return OutboxEvent(event_type=event_type, tenant_id=None, producer="test", aggregate_type="probe",
                       aggregate_id=uuid.uuid4(), payload={}, **ids)


def test_an_event_a_consumer_emits_carries_its_parents_correlation_id_and_the_parent_as_cause(db_session, engine):
    parent_correlation = uuid.uuid4()
    parent = publish(db_session, _probe("test.kan125.parent_recorded", correlation_id=parent_correlation))
    db_session.commit()

    formatter = JSONLogFormatter()
    logged: list[dict] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logged.append(json.loads(formatter.format(record)))

    handler_logger = logging.getLogger("tests.kan125.handler")
    capture = _Capture()
    handler_logger.addHandler(capture)
    handler_logger.setLevel(logging.INFO)

    def _handler(db, message):
        handler_logger.info("handling the parent")
        publish(db, _probe("test.kan125.child_recorded"))  # no ids passed: the harness supplies them

    transport = InProcessTransport(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    transport.register("test.kan125.parent_recorded", consumer_name="test.kan125", handler=_handler)
    try:
        transport.deliver(parent)
    finally:
        handler_logger.removeHandler(capture)

    db_session.expire_all()
    child = db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_type == "test.kan125.child_recorded")).one()
    assert child.correlation_id == parent_correlation
    assert child.causation_id == parent.id
    assert [line["correlationId"] for line in logged] == [str(parent_correlation)]
    assert get_correlation_id() is None  # the handler's context ends with it


def test_a_failing_handler_leaves_no_context_behind(db_session, engine):
    parent = publish(db_session, _probe("test.kan125.failing_recorded", correlation_id=uuid.uuid4()))
    db_session.commit()

    def _handler(db, message):
        assert get_correlation_id() == str(message.correlation_id)
        raise RuntimeError("kan-125 probe failure")

    transport = InProcessTransport(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    transport.register("test.kan125.failing_recorded", consumer_name="test.kan125.failing", handler=_handler)
    with pytest.raises(DeliveryError):
        transport.deliver(parent)
    assert get_correlation_id() is None
    later = publish(db_session, _probe("test.kan125.later_recorded"))
    db_session.commit()
    assert later.correlation_id != parent.correlation_id and later.causation_id is None


def test_an_event_published_outside_any_request_or_handler_still_gets_its_own_id(db_session):
    message = publish(db_session, _probe("test.kan125.alone_recorded"))
    db_session.commit()
    assert message.correlation_id is not None
    assert message.causation_id is None


# --- exit criterion 2: each daily job run starts a fresh correlation id --------------------------------


@pytest.fixture()
def only_these_daily_jobs(monkeypatch):
    monkeypatch.setattr(daily_scheduler, "_REGISTRY", {})
    monkeypatch.setattr(daily_scheduler, "_EPISODES", {})
    return daily_scheduler.register_daily_job


def test_each_daily_job_run_starts_a_fresh_correlation_id(db_session, only_these_daily_jobs):
    seen: dict[str, str | None] = {}

    def _job(name: str):
        def run(db):
            seen[name] = get_correlation_id()
            publish(db, _probe(f"test.kan125.{name}_ran"))
        return run

    only_these_daily_jobs("kan125.first", _job("first"))
    only_these_daily_jobs("kan125.second", _job("second"))
    assert daily_scheduler.run_due_daily_jobs(db_session) == ["kan125.first", "kan125.second"]

    assert seen["first"] and seen["second"] and seen["first"] != seen["second"]
    db_session.expire_all()
    for name in ("first", "second"):
        row = db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_type == f"test.kan125.{name}_ran")).one()
        assert row.correlation_id == uuid.UUID(seen[name])
        assert row.causation_id is None
    assert get_correlation_id() is None


def test_a_daily_jobs_failure_and_its_recovery_log_under_their_own_runs_id(db_session, only_these_daily_jobs):
    lines: list[tuple[str, str | None]] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append((record.getMessage(), get_correlation_id()))

    attempts: list[str | None] = []

    def flaky(db):
        attempts.append(get_correlation_id())
        if len(attempts) == 1:
            raise RuntimeError("kan-125 first run fails")

    only_these_daily_jobs("kan125.flaky", flaky)
    capture = _Capture()
    daily_scheduler.logger.addHandler(capture)
    try:
        start = dt.datetime(2026, 11, 18, 3, 0, tzinfo=dt.UTC)
        assert daily_scheduler.run_due_daily_jobs(db_session, now=start) == []
        later = start + dt.timedelta(hours=2)
        assert daily_scheduler.run_due_daily_jobs(db_session, now=later) == ["kan125.flaky"]
    finally:
        daily_scheduler.logger.removeHandler(capture)

    assert len(attempts) == 2 and attempts[0] and attempts[1] and attempts[0] != attempts[1]
    recovered = [cid for message, cid in lines if message == "daily job recovered"]
    assert recovered == [attempts[1]]
    assert [cid for message, cid in lines if message != "daily job recovered"] == [attempts[0]]


# --- the web service's data export runner: a fresh id per pass ------------------------------------------


def test_each_export_runner_pass_has_its_own_correlation_id(engine, monkeypatch):
    seen: list[str | None] = []
    monkeypatch.setattr(data_export, "expire_downloads", lambda db, store=None: seen.append(get_correlation_id()) or 0)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    assert data_export_runner.run_once(factory) == 0
    assert data_export_runner.run_once(factory) == 0
    assert seen[0] and seen[1] and seen[0] != seen[1]
    assert get_correlation_id() is None
