"""KAN-270: a configuration records when a host first took it

Anto, 2026-11-03 (KAN-270, option A then C): a configuration no host ever
took is deleted 30 days after it was created; one a host took and let go is
kept for good. A configuration knows nothing about its hosts (rule 2), so
vehicle learns that one took it from the hosts' past-tense facts and stamps
it here — never cleared.

- `vehicle_configuration.first_taken_at` (nullable, no default): NULL until a
  host takes the configuration.
- `ix_vehicle_configuration_untaken_created_at`: the daily purge's scan,
  partial on `first_taken_at IS NULL`.

No data step: which existing configurations a host took cannot be read from
here (rule 2 — the references live in Sales, Stock and Valuation). Every run
of `vehicle.configurations.purge_unattached` asks the hosts which unstamped
configurations they hold now and stamps those, so the first run after this
release stamps every configuration a host holds at that moment; one a host
took and let go before this release cannot be known and is purged with the
never taken once it is 30 days old (nothing is live yet).

Inside the migration transaction, deliberately not CONCURRENTLY — KAN-92's
advisory lock (alembic/env.py) is transaction-scoped. Every existing row is
NULL, so the build indexes the whole table once (a SHARE lock on
vehicle_configuration for its duration); it shrinks as hosts take them.

Revision ID: ddc30e4347ba
Revises: 645e0b870286
Create Date: 2026-11-03 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.core.types

# revision identifiers, used by Alembic.
revision: str = "ddc30e4347ba"
down_revision: Union[str, Sequence[str], None] = "645e0b870286"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_vehicle_configuration_untaken_created_at"


def upgrade() -> None:
    op.add_column(
        "vehicle_configuration",
        sa.Column(
            "first_taken_at",
            app.core.types.UTCDateTime(),
            nullable=True,
            comment="KAN-270: when a host first took this configuration; never cleared. "
            "NULL past 30 days: purged daily.",
        ),
    )
    op.create_index(
        _INDEX,
        "vehicle_configuration",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("first_taken_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="vehicle_configuration", postgresql_where=sa.text("first_taken_at IS NULL"))
    op.drop_column("vehicle_configuration", "first_taken_at")
