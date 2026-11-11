"""KAN-229: per-Betrieb session policy (D-A-37, FR-AU-28)

A Betrieb may make its sessions stricter than the platform's, never looser
(Anto, 2026-11-11, option 1): idle 15–120 minutes and an absolute maximum of
1–12 hours, the session ending at whichever comes first — login + the
Betrieb's hours, or the next midnight Europe/Zurich. Two nullable columns on
`dealership_auth_config` (NULL = the platform value), and one on `session`:
the idle window fixed on the row when it was minted (or tightened by a
switch), so `touch` never re-reads the Betrieb's config per request. NULL on
an existing row is the platform's idle window, so no backfill is needed.

Revision ID: a7d2c5e9f041
Revises: 5d1f8a3c7e42
Create Date: 2026-11-11 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d2c5e9f041"
down_revision: str | Sequence[str] | None = "5d1f8a3c7e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dealership_auth_config", sa.Column("session_idle_minutes", sa.Integer(), nullable=True))
    op.add_column("dealership_auth_config", sa.Column("session_absolute_hours", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_dealership_auth_config_session_idle_minutes",
        "dealership_auth_config",
        "session_idle_minutes IS NULL OR session_idle_minutes BETWEEN 15 AND 120",
    )
    op.create_check_constraint(
        "ck_dealership_auth_config_session_absolute_hours",
        "dealership_auth_config",
        "session_absolute_hours IS NULL OR session_absolute_hours BETWEEN 1 AND 12",
    )
    op.add_column("session", sa.Column("idle_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("session", "idle_minutes")
    op.drop_constraint("ck_dealership_auth_config_session_absolute_hours", "dealership_auth_config", type_="check")
    op.drop_constraint("ck_dealership_auth_config_session_idle_minutes", "dealership_auth_config", type_="check")
    op.drop_column("dealership_auth_config", "session_absolute_hours")
    op.drop_column("dealership_auth_config", "session_idle_minutes")
