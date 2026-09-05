"""integration_provider.required_config_keys (KAN-36)

The non-secret counterpart to required_secret_slots — a provider-specific
required field on IntegrationConnection.config (an account number, a
project label) is a catalogue row, never a schema change, same posture as
the secrets column it mirrors.

Revision ID: b4522b27b146
Revises: a1d5c7e9f2b4
Create Date: 2026-09-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b4522b27b146'
down_revision: Union[str, Sequence[str], None] = 'a1d5c7e9f2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration_provider",
        sa.Column("required_config_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.alter_column("integration_provider", "required_config_keys", server_default=None)


def downgrade() -> None:
    op.drop_column("integration_provider", "required_config_keys")
