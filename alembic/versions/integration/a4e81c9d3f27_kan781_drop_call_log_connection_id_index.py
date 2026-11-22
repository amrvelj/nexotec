"""KAN-781: drop ix_integration_call_log_connection_id

KAN-777 added `ix_integration_call_log_connection_id_created_at`, whose
leading column is `connection_id`. Every read of `integration_call_log` by
connection — `gbp_status`'s two latest-call reads and the usage counts of
`connections.get_usage` — is served by it
(`tests/integration/test_kan781_call_log_connection_index.py` shows each
plan), so the single-column index only cost an extra write on every provider
call. The retention purge reads by `created_at` alone and never used it.

`integration_call_log` is large and hot: the index is dropped CONCURRENTLY in
an autocommit block, in a migration of its own (ADR-134; migrations.md). The
downgrade rebuilds it concurrently, after a `DROP INDEX CONCURRENTLY IF
EXISTS` so a rerun repairs an INVALID one. DDL only, no data step.

Revision ID: a4e81c9d3f27
Revises: 8c4f19b2d471
Create Date: 2026-11-22 06:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4e81c9d3f27"
down_revision: str | Sequence[str] | None = "8c4f19b2d471"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_integration_call_log_connection_id"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.execute(f"CREATE INDEX CONCURRENTLY {_INDEX} ON integration_call_log (connection_id)")
