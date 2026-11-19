"""KAN-767: `integration_call_log` records what the provider answered.

Three nullable columns, no backfill (rows written before stay NULL):

- `http_status` — the provider's HTTP status for the attempt; NULL when no
  HTTP answer arrived (a transport failure, a timeout) or the provider
  speaks no HTTP (the AutoScout24 FTP feed, the demo provider).
- `purpose` — the caller's name for what it asked (`call_capability`'s
  `purpose`, else its `capability`), beside `capability`, the provider's own
  operation name (KAN-486).
- `provider_error` — the provider's short error identifier (a json:api
  `title`, an OAuth `error` code), one line of at most 120 characters, never
  its free prose: still metadata in the ADR-024 tier-1 sense.

The downgrade drops the three columns and what they hold.

Revision ID: 2301d25c7b07
Revises: 3d8a3c0fb827
Create Date: 2026-11-19 11:40:23.280566

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2301d25c7b07"
down_revision: str | Sequence[str] | None = "3d8a3c0fb827"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integration_call_log",
        sa.Column(
            "http_status",
            sa.SmallInteger(),
            nullable=True,
            comment="The provider's HTTP status for this attempt; NULL when no HTTP answer arrived or the provider speaks no HTTP.",
        ),
    )
    op.add_column(
        "integration_call_log",
        sa.Column(
            "purpose",
            sa.String(length=64),
            nullable=True,
            comment="The caller's name for what it asked (call_capability's purpose, else its capability); capability is the provider's operation.",
        ),
    )
    op.add_column(
        "integration_call_log",
        sa.Column(
            "provider_error",
            sa.String(length=120),
            nullable=True,
            comment="The provider's short error identifier (json:api title, OAuth error code), one line, never its free prose.",
        ),
    )


def downgrade() -> None:
    op.drop_column("integration_call_log", "provider_error")
    op.drop_column("integration_call_log", "purpose")
    op.drop_column("integration_call_log", "http_status")
