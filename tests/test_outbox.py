"""Transactional outbox (PR-4, ADR-006). Uses a test-only event type and a
handler that writes to tests/demo_models.py's DemoWidget as its "side
effect" — proves the mechanism end to end without touching domain code
(no real business events or handlers ship in this PR; that's PR-5).

Runs on both lanes (db_session is DB-agnostic, per conftest.py) except
where a test is explicitly about SKIP LOCKED under real concurrency —
that's proven separately, by the CI job that runs the real
`python -m app.worker` entrypoint against real Postgres, not from here.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.outbox import OutboxEvent, dead_letter_count, oldest_pending_age_seconds, publish, replay
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_transport import DeliveryError, InProcessTransport
from app.core.outbox_worker import MAX_ATTEMPTS, PollResult, compute_backoff, poll_once
from app.core.processed_event_model import ProcessedEvent
from tests.demo_models import DemoWidget


def _probe_event(**overrides) -> OutboxEvent:
    payload = {"event_type": "test.probe.pinged", "tenant_id": None, "producer": "test", "aggregate_type": "probe", "aggregate_id": uuid.uuid4(), "payload": {"n": 1}}
    payload.update(overrides)
    return OutboxEvent(**payload)


def _widget_handler(db, message):
    db.add(DemoWidget(tenant_id=message.aggregate_id, name=str(message.id)))


# --- publish / transaction boundary -----------------------------------------------


def test_rolled_back_publish_leaves_no_outbox_row(db_session):
    event = _probe_event()
    message = publish(db_session, event)
    message_id = message.id
    db_session.rollback()

    assert db_session.get(OutboxMessage, message_id) is None


def test_publish_commits_with_the_callers_own_commit(db_session):
    event = _probe_event()
    message = publish(db_session, event)
    db_session.commit()

    db_session.expire_all()
    row = db_session.get(OutboxMessage, message.id)
    assert row is not None
    assert row.status == OutboxStatus.PENDING
    assert row.attempts == 0


def test_publish_defaults_correlation_id_when_not_given(db_session):
    message = publish(db_session, _probe_event())
    db_session.commit()
    assert message.correlation_id is not None


# --- clean delivery, idempotent redelivery ----------------------------------------


def test_clean_delivery_publishes_and_writes_processed_event(db_session, engine):
    message = publish(db_session, _probe_event())
    db_session.commit()

    transport = InProcessTransport(_session_factory(engine))
    transport.register("test.probe.pinged", consumer_name="test.widget", handler=_widget_handler)

    result = poll_once(db_session, transport)

    assert result == PollResult(claimed=1, published=1, retried=0, dead=0)
    db_session.expire_all()
    row = db_session.get(OutboxMessage, message.id)
    assert row.status == OutboxStatus.PUBLISHED
    assert row.published_at is not None
    assert db_session.get(ProcessedEvent, (message.id, "test.widget")) is not None
    assert len(db_session.scalars(select(DemoWidget)).all()) == 1


def test_redelivering_a_processed_event_does_not_rerun_the_handler(db_session, engine):
    message = publish(db_session, _probe_event())
    db_session.commit()

    calls: list[uuid.UUID] = []

    def counting_handler(db, msg):
        calls.append(msg.id)
        _widget_handler(db, msg)

    transport = InProcessTransport(_session_factory(engine))
    transport.register("test.probe.pinged", consumer_name="test.widget", handler=counting_handler)

    poll_once(db_session, transport)
    assert calls == [message.id]
    assert len(db_session.scalars(select(DemoWidget)).all()) == 1

    # Simulate redelivery directly (same message, already processed).
    db_session.expire_all()
    row = db_session.get(OutboxMessage, message.id)
    transport.deliver(row)

    assert calls == [message.id]  # handler did not run a second time
    assert len(db_session.scalars(select(DemoWidget)).all()) == 1  # no second side effect


def test_zero_registered_handlers_still_publishes(db_session, engine):
    """No consumers for an event type isn't a failure — same as a broker
    topic with no subscribers. Locking this in as intended behaviour, not
    an accident: app.worker ships with an empty handler registry in PR-4.
    """

    message = publish(db_session, _probe_event(event_type="test.probe.nobody_listens"))
    db_session.commit()

    transport = InProcessTransport(_session_factory(engine))
    result = poll_once(db_session, transport)

    assert result.published == 1
    db_session.expire_all()
    assert db_session.get(OutboxMessage, message.id).status == OutboxStatus.PUBLISHED


# --- partial failure: the composite-key retry proof ---------------------------------


def test_partial_failure_retry_only_reruns_the_failed_handler(db_session, engine):
    """The most likely bug in this PR, per review: a partial-failure retry
    that re-runs every handler instead of only the one that failed. This
    is exactly what (event_id, consumer_name) as a composite key buys.
    """

    message = publish(db_session, _probe_event(event_type="test.probe.partial"))
    db_session.commit()

    a_calls: list[uuid.UUID] = []
    b_calls: list[uuid.UUID] = []
    b_fail_once = {"failed": False}

    def handler_a(db, msg):
        a_calls.append(msg.id)

    def handler_b(db, msg):
        b_calls.append(msg.id)
        if not b_fail_once["failed"]:
            b_fail_once["failed"] = True
            raise RuntimeError("B fails on first attempt")

    transport = InProcessTransport(_session_factory(engine))
    transport.register("test.probe.partial", consumer_name="test.a", handler=handler_a)
    transport.register("test.probe.partial", consumer_name="test.b", handler=handler_b)

    result = poll_once(db_session, transport)
    assert result.retried == 1
    assert a_calls == [message.id]
    assert b_calls == [message.id]
    # A's side effect committed independently and left a processed_event row.
    assert db_session.get(ProcessedEvent, (message.id, "test.a")) is not None
    assert db_session.get(ProcessedEvent, (message.id, "test.b")) is None

    db_session.expire_all()
    row = db_session.get(OutboxMessage, message.id)
    row.next_attempt_at = utcnow() - dt.timedelta(seconds=1)
    db_session.commit()

    result = poll_once(db_session, transport)
    assert result.published == 1
    assert a_calls == [message.id], "A ran again on retry — the composite-key skip is broken"
    assert b_calls == [message.id, message.id], "B did not rerun on retry"


# --- retry, backoff, dead-letter --------------------------------------------------


def test_failing_handler_retries_with_backoff_not_immediately(db_session, engine):
    message = publish(db_session, _probe_event(event_type="test.probe.slow_fail"))
    db_session.commit()

    def failing_handler(db, msg):
        raise RuntimeError("nope")

    transport = InProcessTransport(_session_factory(engine))
    transport.register("test.probe.slow_fail", consumer_name="test.fail", handler=failing_handler)

    result = poll_once(db_session, transport)
    assert result.retried == 1

    db_session.expire_all()
    row = db_session.get(OutboxMessage, message.id)
    assert row.status == OutboxStatus.PENDING
    assert row.attempts == 1
    assert row.next_attempt_at > utcnow()  # backoff actually delays the next attempt

    # A poll right now must not reclaim it — next_attempt_at hasn't arrived.
    result = poll_once(db_session, transport)
    assert result.claimed == 0


def test_dead_letters_after_five_attempts_with_error_message(db_session, engine):
    message = publish(db_session, _probe_event(event_type="test.probe.always_fails"))
    db_session.commit()

    def failing_handler(db, msg):
        raise RuntimeError("deliberately broken")

    transport = InProcessTransport(_session_factory(engine))
    transport.register("test.probe.always_fails", consumer_name="test.fail", handler=failing_handler)

    for _ in range(MAX_ATTEMPTS):
        db_session.expire_all()
        row = db_session.get(OutboxMessage, message.id)
        row.next_attempt_at = utcnow() - dt.timedelta(seconds=1)
        db_session.commit()
        poll_once(db_session, transport)

    db_session.expire_all()
    row = db_session.get(OutboxMessage, message.id)
    assert row.status == OutboxStatus.DEAD
    assert row.attempts == MAX_ATTEMPTS
    assert "deliberately broken" in row.last_error
    assert "test.fail" in row.last_error


def test_compute_backoff_grows_and_caps():
    delays = [compute_backoff(n).total_seconds() for n in range(1, 8)]
    # Strictly increasing before the cap (jitter is at most +20%, base doubles each step).
    assert delays[0] < delays[1] < delays[2]

    # attempts=15 -> 2**15=32768s uncapped, nowhere near a ~1h retry — must be capped.
    capped_delays = [compute_backoff(n).total_seconds() for n in (15, 16, 17)]
    for d in capped_delays:
        assert 3600 <= d <= 3600 * 1.2 + 1  # cap, plus up to 20% jitter, small epsilon


# --- replay ------------------------------------------------------------------------


def test_replay_resets_a_dead_message_to_pending(db_session):
    message = publish(db_session, _probe_event(event_type="test.probe.dead_for_replay"))
    db_session.commit()
    row = db_session.get(OutboxMessage, message.id)
    row.status = OutboxStatus.DEAD
    row.attempts = MAX_ATTEMPTS
    row.last_error = "boom"
    db_session.commit()

    replayed = replay(db_session, message.id)
    db_session.commit()

    assert replayed.status == OutboxStatus.PENDING
    assert replayed.attempts == 0
    assert replayed.last_error is None


def test_replay_raises_on_a_non_dead_message(db_session):
    message = publish(db_session, _probe_event())
    db_session.commit()

    with pytest.raises(ValueError):
        replay(db_session, message.id)


# --- observability (Decision 8) -----------------------------------------------------


def test_oldest_pending_age_and_dead_letter_count_are_queryable(db_session):
    assert oldest_pending_age_seconds(db_session) is None
    assert dead_letter_count(db_session) == 0

    message = publish(db_session, _probe_event(event_type="test.probe.lag"))
    db_session.commit()

    assert oldest_pending_age_seconds(db_session) is not None
    assert oldest_pending_age_seconds(db_session) >= 0

    row = db_session.get(OutboxMessage, message.id)
    row.status = OutboxStatus.DEAD
    db_session.commit()

    assert dead_letter_count(db_session) == 1
    assert oldest_pending_age_seconds(db_session) is None  # nothing pending any more


# --- consume_once, directly ---------------------------------------------------------


def test_consume_once_skips_a_second_call_for_the_same_pair(db_session):
    message = publish(db_session, _probe_event())
    db_session.commit()

    calls = []

    def handler(db, msg):
        calls.append(msg.id)

    ran_first = consume_once(db_session, message=message, consumer_name="test.direct", handler=handler)
    ran_second = consume_once(db_session, message=message, consumer_name="test.direct", handler=handler)

    assert ran_first is True
    assert ran_second is False
    assert calls == [message.id]


# --- transport contract, directly ---------------------------------------------------


def test_deliver_raises_delivery_error_with_every_failure_when_a_handler_fails(db_session, engine):
    message = publish(db_session, _probe_event(event_type="test.probe.transport_contract"))
    db_session.commit()

    def failing_handler(db, msg):
        raise RuntimeError("transport contract check")

    transport = InProcessTransport(_session_factory(engine))
    transport.register("test.probe.transport_contract", consumer_name="test.fail", handler=failing_handler)

    with pytest.raises(DeliveryError) as exc_info:
        transport.deliver(message)

    assert exc_info.value.message is message
    assert [name for name, _exc in exc_info.value.failures] == ["test.fail"]
    assert "transport contract check" in str(exc_info.value)


def _session_factory(engine):
    """A genuine per-call session factory bound to the test engine — NOT
    the db_session fixture's own session reused. That distinction matters
    here specifically: InProcessTransport must open a session that's
    independent of poll_once()'s own claim session (see
    app.core.outbox_worker's module docstring on session boundaries), and
    a test that fakes this by handing back the same session every time
    would stop testing that boundary at all. The test engine uses
    StaticPool (conftest.py) — a single shared connection — so separate
    Session objects from this factory still see the same data as
    db_session; they're genuinely separate transactions on it, which is
    exactly what's being verified.
    """

    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
