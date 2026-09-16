"""seed the autoscout24 integration_provider row (KAN-27, WP-7, ADR-062)

Only AS24i has a specification on file ("SS 03 AutoScout24 Schnitt-
stellenbeschrieb Version 34.pdf") — no `IntegrationProvider` row is
seeded for Carmarket or Autolina; there is nothing to configure a
connection against for either yet (same posture as the still-missing VIN
webservice spec, KAN-36).

Revision ID: 2b6e4f8a9c1d
Revises: 815265b3a7c8
Create Date: 2026-09-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = '2b6e4f8a9c1d'
down_revision: Union[str, Sequence[str], None] = '815265b3a7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVIDER_CODE = "autoscout24"


def upgrade() -> None:
    bind = op.get_bind()
    integration_provider = sa.table(
        "integration_provider",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("version", sa.Integer()),
        sa.column("provider_code", sa.String()),
        sa.column("category", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("auth_type", sa.String()),
        sa.column("required_secret_slots", sa.JSON()),
        sa.column("required_config_keys", sa.JSON()),
        sa.column("capability_codes", sa.JSON()),
        sa.column("docs_url", sa.String()),
        sa.column("supports_sandbox", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = sa.func.now()
    bind.execute(
        integration_provider.insert().values(
            id=uuid7(),
            version=1,
            provider_code=_PROVIDER_CODE,
            category="marketplace",
            display_name="AutoScout24",
            auth_type="ftp_password",
            required_secret_slots=["password"],
            required_config_keys=["kundennummer", "ftpHost", "ftpUsername"],
            capability_codes=["marketplace_publish"],
            docs_url=None,
            supports_sandbox=False,
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM integration_provider WHERE provider_code = :code"), {"code": _PROVIDER_CODE})
