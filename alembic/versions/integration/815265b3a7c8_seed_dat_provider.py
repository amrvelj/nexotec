"""seed the dat integration_provider row (KAN-36)

The DAT sub-account (`DAT VIN Account`, `DAT Benutzer`, `DAT Passwort`) is
issued alongside every auto-i-dat account but is a genuinely separate
account that rotates independently — never folded into the `auto_i_dat`
connection's own config/secrets, same "never a flag on one connection"
posture rule 7 already applies elsewhere in this registry. It is what
entitles VIN decode (`vin_decode`, derived in
app/integration/services/connections.py — never hand-declared).

No adapter is registered for this provider_code: there is no DAT
webservice specification of any kind in this repo (not just for VIN
decode), so a `dat` connection's own "Test connection" action correctly
fails with the existing UnknownProviderError until a future ticket adds a
real adapter once that spec is obtained. capability_codes carries the two
account-sheet counters DAT-backed calls are billed under (VIN, VINIdentDB)
— see the sibling migration 49be490e2077 for the full eight-code mapping.

Revision ID: 815265b3a7c8
Revises: 49be490e2077
Create Date: 2026-09-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = '815265b3a7c8'
down_revision: Union[str, Sequence[str], None] = '49be490e2077'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVIDER_CODE = "dat"


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
            category="vehicle_data",
            display_name="DAT",
            auth_type="username_password",
            required_secret_slots=["password"],
            required_config_keys=["accountNumber", "username"],
            capability_codes=["vin", "vin_ident_db"],
            docs_url=None,
            supports_sandbox=False,
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM integration_provider WHERE provider_code = :code"), {"code": _PROVIDER_CODE})
