"""seed the real auto_i_dat integration_provider row (WP-6 PR-3)

Genuinely separate from `auto_i_dat_mock` (PR-2) — mock and real are
never a runtime flag on one connection, same "never a flag" posture
rule 7 already applies to sandbox/production. `required_secret_slots`
names both slots the real account needs (`password` + `aes_key`) — the
descriptive field PR-7's dealer-facing "Connect" form reads to know
which secret fields to render; nothing server-side rejects a connection
missing one yet (a real call against it would simply fail to resolve
the missing secret at call time).

Revision ID: f3c9a1e6d8b2
Revises: a7c3dfebefd4
Create Date: 2026-08-31 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = 'f3c9a1e6d8b2'
down_revision: Union[str, Sequence[str], None] = 'a7c3dfebefd4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVIDER_CODE = "auto_i_dat"


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
            category="vehicle_data",
            display_name="auto-i-dat",
            auth_type="soap_password_aes",
            required_secret_slots=["password", "aes_key"],
            capability_codes=["vehicle_data", "images", "packages", "valuation", "forecast"],
            docs_url=None,
            supports_sandbox=True,
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM integration_provider WHERE provider_code = :code"), {"code": _PROVIDER_CODE})
