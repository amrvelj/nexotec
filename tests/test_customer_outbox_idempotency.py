"""WP-1 exit criterion: one event travels from a business write through the
outbox to a consumer and is provably not reprocessed on redelivery.

Runs on the Postgres lane only (ADR-011 — Postgres is the database of
record; SQLite may never be the only lane gating a merge). Skipped, not
failed, when DMS_TEST_DATABASE_URL is unset, since both CI lanes invoke the
same `pytest` command over the same test tree — this is the only thing that
distinguishes them from inside a test.

The "real consumer" here is a stand-in read-model projection (same pattern
tests/test_outbox.py uses with DemoWidget for the probe event) — no
downstream context actually consumes customer.created yet. What this test
proves is the mechanism: a real business-event type, drained by a real
handler, redelivered, and shown not to re-run.
"""

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.outbox_worker import poll_once
from app.core.processed_event_model import ProcessedEvent
from app.customer.models.customer import CustomerType, Language
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate
from app.customer.services.customer import create_customer
from tests.demo_models import DemoWidget

pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Idempotent-redelivery proof runs on the Postgres lane only (ADR-011).",
)

_CONSUMER_NAME = "test.customer_label_projection"


def _label_projection_handler(db, message: OutboxMessage) -> None:
    """Stand-in for a downstream context's denormalised-label projection —
    the actual side effect under test is irrelevant; what matters is that
    it runs exactly once per (event_id, consumer_name).
    """

    db.add(DemoWidget(tenant_id=message.aggregate_id, name=message.payload["customerNumber"]))


def test_customer_created_is_delivered_once_and_not_reprocessed_on_redelivery(db_session, engine):
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    customer = create_customer(
        db_session,
        tenant_id=tenant_id,
        data=CustomerCreate(
            customer_type=CustomerType.INDIVIDUAL,
            language=Language.DE,
            first_name="Anna",
            last_name="Muster",
            emails=[CustomerEmailCreate(email_type="private", email_address="anna@example.ch")],
        ),
        actor_id=actor_id,
    )

    message = db_session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "customer.created", OutboxMessage.aggregate_id == customer.id
        )
    )
    assert message is not None, "create_customer did not publish a customer.created outbox row"
    message_id = message.id

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(session_factory)
    calls: list[uuid.UUID] = []

    def counting_handler(db, msg):
        calls.append(msg.id)
        _label_projection_handler(db, msg)

    transport.register("customer.created", consumer_name=_CONSUMER_NAME, handler=counting_handler)

    # First delivery, through the real poll/claim/dispatch path.
    result = poll_once(db_session, transport)
    assert result.published == 1
    assert calls == [message_id]
    assert db_session.get(ProcessedEvent, (message_id, _CONSUMER_NAME)) is not None
    assert len(db_session.scalars(select(DemoWidget)).all()) == 1

    # Redelivery of the SAME eventId — simulates an at-least-once resend
    # (e.g. a worker crash after dispatch but before marking PUBLISHED).
    db_session.expire_all()
    redelivered = db_session.get(OutboxMessage, message_id)
    transport.deliver(redelivered)

    assert calls == [message_id], "handler ran a second time on redelivery — idempotency is broken"
    assert len(db_session.scalars(select(DemoWidget)).all()) == 1, "a second side effect was written on redelivery"
