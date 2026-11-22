"""KAN-781: `ix_integration_call_log_connection_id` is gone, and every read
of `integration_call_log` by connection is served by KAN-777's
`ix_integration_call_log_connection_id_created_at` — the latest-call reads of
`gbp_status` and the two usage reads of `connections.get_usage`."""

import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import insert, inspect, text

from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.services import connections, gbp_status

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="index use is Postgres-only")

COMPOSITE = "ix_integration_call_log_connection_id_created_at"
DROPPED = "ix_integration_call_log_connection_id"
START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _seed(db_session, connection_id: uuid.UUID, count: int) -> None:
    db_session.execute(
        insert(IntegrationCallLog),
        [
            {
                "id": uuid.uuid4(),
                "connection_id": connection_id,
                "capability": "caption_code" if n % 2 else "oauth_token",
                "status": CallStatus.SUCCESS,
                "http_status": 200,
                "duration_ms": 10,
                "tokens_in": n % 5,
                "correlation_id": uuid.uuid4(),
                "created_at": START + dt.timedelta(minutes=n),
            }
            for n in range(count)
        ],
    )


def _plan(db_session, query) -> str:
    compiled = query.compile(dialect=db_session.get_bind().dialect, compile_kwargs={"literal_binds": True})
    return "\n".join(row[0] for row in db_session.execute(text(f"EXPLAIN {compiled}")))


def test_only_the_composite_index_leads_with_connection_id(db_session):
    indexes = {
        index["name"]: index["column_names"]
        for index in inspect(db_session.get_bind()).get_indexes("integration_call_log")
    }
    assert DROPPED not in indexes
    assert [name for name, columns in indexes.items() if columns[:1] == ["connection_id"]] == [COMPOSITE]


def _window():
    return START + dt.timedelta(days=1), START + dt.timedelta(days=2)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda c: gbp_status._latest_call(c), id="gbp_status._latest_call"),
        pytest.param(lambda c: gbp_status._latest_deciding_call(c), id="gbp_status._latest_deciding_call"),
        pytest.param(lambda c: connections._usage_counts_query(c, *_window()), id="connections.usage_counts"),
        pytest.param(lambda c: connections._usage_tokens_query(c, *_window()), id="connections.usage_tokens"),
    ],
)
def test_every_read_by_connection_uses_the_composite_index(db_session, build):
    target = uuid.uuid4()
    _seed(db_session, target, 3000)
    for _ in range(8):
        _seed(db_session, uuid.uuid4(), 3000)
    db_session.flush()
    db_session.execute(text("ANALYZE integration_call_log"))

    plan = _plan(db_session, build(target))

    assert COMPOSITE in plan, plan
    assert "Seq Scan" not in plan, plan
    db_session.rollback()
