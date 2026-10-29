"""the call log outlives its connection (KAN-470)

Deleting a connection that had made one provider call was a 500: the
foreign key integration_call_log.connection_id -> integration_connection.id
refused the connection's delete. Anto ruled (2026-10-26, option A; this
migration plan approved 2026-10-28): the call log stays until ADR-024
retention purges it.

- Drops integration_call_log_connection_id_fkey. connection_id stays the
  owner's id, a plain column with a comment saying so.
- Adds connection_label and connection_label_refreshed_at, the three-column
  pattern inside the context: the connection's name at the time of the
  call, never refreshed on rename. Backfilled from each row's connection
  (every row has one while the foreign key still holds); the backfill reads
  no rows into Python, so it renders offline as plain SQL.
- Downgrade puts the foreign key back. It refuses while a row names a
  connection that no longer exists: the old constraint cannot hold such a
  row, and deleting call-log evidence to make it fit is not a downgrade's
  call.

Revision ID: 9c4485cabd87
Revises: 148d12324c83
Create Date: 2026-10-29 04:08:09.622082

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "9c4485cabd87"
down_revision: str | Sequence[str] | None = "148d12324c83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT = "IntegrationConnection.id. No DB-level FK: the row outlives a deleted connection (KAN-470)."


def upgrade() -> None:
    op.add_column("integration_call_log", sa.Column("connection_label", sa.String(length=200), nullable=True))
    op.add_column(
        "integration_call_log", sa.Column("connection_label_refreshed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE integration_call_log AS log
           SET connection_label = connection.display_name,
               connection_label_refreshed_at = now()
          FROM integration_connection AS connection
         WHERE connection.id = log.connection_id
        """
    )
    op.alter_column(
        "integration_call_log", "connection_id", existing_type=sa.UUID(), comment=_COMMENT, existing_nullable=False
    )
    op.drop_constraint("integration_call_log_connection_id_fkey", "integration_call_log", type_="foreignkey")


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- orphan check skipped: it reads rows and needs a live database")
    else:
        orphans = op.get_bind().scalar(
            sa.text(
                """
                SELECT count(*) FROM integration_call_log AS log
                 WHERE NOT EXISTS (SELECT 1 FROM integration_connection AS c WHERE c.id = log.connection_id)
                """
            )
        )
        if orphans:
            raise RuntimeError(
                f"{orphans} integration_call_log row(s) name a deleted connection; the foreign key "
                "cannot be restored over them. They are retention data (ADR-024): wait for the purge "
                "or remove them deliberately before downgrading."
            )
    op.create_foreign_key(
        "integration_call_log_connection_id_fkey",
        "integration_call_log",
        "integration_connection",
        ["connection_id"],
        ["id"],
    )
    op.alter_column(
        "integration_call_log",
        "connection_id",
        existing_type=sa.UUID(),
        comment=None,
        existing_comment=_COMMENT,
        existing_nullable=False,
    )
    op.drop_column("integration_call_log", "connection_label_refreshed_at")
    op.drop_column("integration_call_log", "connection_label")
