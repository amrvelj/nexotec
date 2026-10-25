"""The per-Betrieb switch for Google Places Autocomplete (KAN-37, ADR-101)

`dealership_settings.address_autocomplete_enabled`, boolean, NOT NULL, on
for every existing Betrieb (Anto, 2026-10-25: default on, the manager
switches it off). The server default backfills the existing rows in one
statement and stays, as the model declares it (`server_default=true()`):
the KAN-203 backfill's INSERT … SELECT names its columns and is immutable,
so a NOT NULL column added after it needs a default the database supplies
(tests/test_kan203_business_master_data.py runs that INSERT on the current
schema).

The key behind the feature is platform configuration
(`DMS_GOOGLE_PLACES_BROWSER_KEY`), never a column.

Revision ID: bda9fef79e94
Revises: e1f7a3c9b2d4
Create Date: 2026-10-25 17:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bda9fef79e94"
down_revision: str | Sequence[str] | None = "e1f7a3c9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dealership_settings",
        sa.Column("address_autocomplete_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("dealership_settings", "address_autocomplete_enabled")
