"""KAN-777: the Business Profile status read's two «latest call of the
client connection» queries (`gbp_status.client_health`) are answered by
`ix_integration_call_log_connection_id_created_at`, newest first, instead of
sorting every call-log row of the connection."""

import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import inspect, insert, text

from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.services import gbp_status

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="index use is Postgres-only")

INDEX = "ix_integration_call_log_connection_id_created_at"
START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _seed(db_session, connection_id: uuid.UUID, count: int) -> None:
    db_session.execute(
        insert(IntegrationCallLog),
        [
            {
                "id": uuid.uuid4(),
                "connection_id": connection_id,
                "capability": "accounts_list",
                "status": CallStatus.SUCCESS if n % 7 else CallStatus.ERROR,
                "http_status": 200 if n % 7 else 403,
                "provider_error": None if n % 7 else "SERVICE_DISABLED",
                "duration_ms": 10,
                "correlation_id": uuid.uuid4(),
                "created_at": START + dt.timedelta(minutes=n),
            }
            for n in range(count)
        ],
    )


def test_the_index_is_declared_with_the_migrations_shape(db_session):
    indexes = {index["name"]: index["column_names"] for index in inspect(db_session.get_bind()).get_indexes("integration_call_log")}
    assert indexes.get(INDEX) == ["connection_id", "created_at"]


@pytest.mark.parametrize("query", [gbp_status._latest_call, gbp_status._latest_deciding_call])
def test_the_latest_call_reads_use_the_index_and_sort_nothing(db_session, query):
    client = uuid.uuid4()
    _seed(db_session, client, 3000)
    for _ in range(5):
        _seed(db_session, uuid.uuid4(), 400)
    db_session.flush()
    db_session.execute(text("ANALYZE integration_call_log"))

    compiled = query(client).compile(dialect=db_session.get_bind().dialect, compile_kwargs={"literal_binds": True})
    plan = "\n".join(row[0] for row in db_session.execute(text(f"EXPLAIN {compiled}")))

    assert INDEX in plan, plan
    assert "Index Scan Backward" in plan, plan
    # The full sort of the connection's rows is what the index removes; an
    # incremental sort breaks only ties on `id` within one `created_at`.
    assert "\n  ->  Sort" not in plan and not plan.lstrip().startswith("Sort"), plan
    db_session.rollback()


def test_the_reads_still_answer_the_latest_rows(db_session):
    client = uuid.uuid4()
    _seed(db_session, client, 50)
    _seed(db_session, uuid.uuid4(), 80)
    db_session.flush()

    assert db_session.scalar(gbp_status._latest_call(client)) == START + dt.timedelta(minutes=49)
    deciding = db_session.execute(gbp_status._latest_deciding_call(client)).first()
    assert deciding.created_at == START + dt.timedelta(minutes=49)
    assert deciding.provider_error == "SERVICE_DISABLED"  # minute 49 is a refusal row (49 % 7 == 0)
