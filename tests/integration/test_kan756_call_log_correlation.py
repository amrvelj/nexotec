"""KAN-756: a provider call's `integration_call_log` row carries the correlation id of the
request or event that made it (KAN-125's context), not one minted for the call alone. An id
the caller passes still wins; outside any context the row gets an id of its own."""

import uuid

from sqlalchemy import select

from app.core.correlation import correlation_scope
from app.integration.models.call_log import IntegrationCallLog
from app.integration.services import gateway
from tests.integration.test_integration_gateway import _bearer, _make_connection, _make_provider, _postgres_only, _token

pytestmark = _postgres_only


def _rows(db_session, connection_id):
    db_session.expire_all()
    return db_session.scalars(select(IntegrationCallLog).filter_by(connection_id=connection_id)).all()


def test_a_call_inside_a_handlers_context_carries_the_parent_messages_correlation_id(db_session):
    connection = _make_connection(db_session, _make_provider(db_session))
    parent = uuid.uuid4()

    with correlation_scope(parent, causation_id=uuid.uuid4()):
        with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
            adapter.fetch_variant("FZ100001")

    assert [row.correlation_id for row in _rows(db_session, connection.id)] == [parent]


def test_a_row_recorded_outside_a_block_inside_a_context_carries_its_id(db_session):
    connection = _make_connection(db_session, _make_provider(db_session))
    parent = uuid.uuid4()

    with correlation_scope(parent):
        row = gateway.record_call(
            db_session, connection=connection, capability="vehicle_data",
            status=gateway.CallStatus.SUCCESS, duration_ms=1,
        )

    assert row.correlation_id == parent


def test_an_id_the_caller_passes_still_wins_over_the_context(db_session):
    connection = _make_connection(db_session, _make_provider(db_session))
    own = uuid.uuid4()

    with correlation_scope(uuid.uuid4()):
        with gateway.call_capability(
            db_session, connection=connection, capability="vehicle_data", correlation_id=own
        ) as adapter:
            adapter.fetch_variant("FZ100001")

    assert [row.correlation_id for row in _rows(db_session, connection.id)] == [own]


def test_a_call_outside_any_context_gets_an_id_of_its_own(db_session):
    connection = _make_connection(db_session, _make_provider(db_session))

    for _ in range(2):
        with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
            adapter.fetch_variant("FZ100001")

    ids = [row.correlation_id for row in _rows(db_session, connection.id)]
    assert len(ids) == 2 and None not in ids and ids[0] != ids[1]


def test_a_call_made_by_an_http_request_carries_the_requests_correlation_id(client, db_session):
    provider = _make_provider(db_session)
    token = _token(uuid.uuid4())
    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "auto-i-dat", "environment": "sandbox"},
        headers=_bearer(token),
    ).json()
    request_id = uuid.uuid4()

    tested = client.post(
        f"/v1/integrations/connections/{created['id']}/test",
        headers={**_bearer(token), "X-Correlation-Id": str(request_id)},
    )

    assert tested.status_code == 200, tested.text
    rows = _rows(db_session, uuid.UUID(created["id"]))
    assert rows and {row.correlation_id for row in rows} == {request_id}
