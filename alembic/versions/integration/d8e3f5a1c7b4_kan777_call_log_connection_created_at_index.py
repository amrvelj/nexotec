"""KAN-777: index integration_call_log (connection_id, created_at)

`app/integration/services/gbp_status.py::client_health` reads the latest
call-log row of Nexotec's Business Profile client, and its latest deciding
row: `WHERE connection_id = … ORDER BY created_at DESC, id DESC LIMIT 1`.
Only `connection_id` was indexed, so Postgres sorted every row of that one
platform-scope connection (every Betrieb's Business Profile calls share it,
retained 24 months) on each status-page read and once per bound Betrieb in
the daily status job. With this index the read walks the connection's rows
newest first and stops at the first match.

`integration_call_log` is written on every provider call and is large: the
index is built CONCURRENTLY in an autocommit block, in a migration of its own,
after a `DROP INDEX CONCURRENTLY IF EXISTS` so a rerun repairs an INVALID one
(ADR-134; migrations.md). `ix_integration_call_log_connection_id` stays: the
call log's other reads are not touched here. DDL only, no data step.

Revision ID: d8e3f5a1c7b4
Revises: b2be6d0c6a85
Create Date: 2026-11-21 06:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e3f5a1c7b4"
down_revision: str | Sequence[str] | None = "b2be6d0c6a85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_integration_call_log_connection_id_created_at"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.execute(f"CREATE INDEX CONCURRENTLY {_INDEX} ON integration_call_log (connection_id, created_at)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
