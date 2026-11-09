"""retire the dat provider and the vin_decode cache rows (KAN-549, ADR-113)

The `dat` provider row (seeded by 815265b3a7c8) stood for the DAT
sub-account that the SOAP account sheet showed VIN traffic through. On the
identAPI, VIN identification is the licence option `vin` of the one
`auto_i_dat` connection (ADR-113), probed on «Test connection» like every
option, so the row, any connection of it and the `vin_decode` entitlement
rows `compute_vin_decode_entitlement` cached on other connections go.

Deleted, in dependency order, inside the integration context only:
1. the entitlement and secret-reference rows of every `dat` connection,
2. those connections,
3. the `vin_decode` entitlement rows of any connection (a display cache of
   the retired derivation; `vin_decode` is no licence option),
4. the `dat` provider row.

Kept: call-log rows and notifications naming a deleted connection — they
carry no foreign key and outlive a deleted connection by design (KAN-470).
A secret value a `dat` connection held in the secrets manager is not reached
from here; no such connection exists outside tests.

Every step is a plain DELETE that reads no rows, so an offline `--sql`
render emits it as SQL (KAN-174).

Revision ID: 3b3faa39c5e2
Revises: 3f1c9a7e2b54
Create Date: 2026-11-09 06:46:53.338343

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3b3faa39c5e2'
down_revision: Union[str, Sequence[str], None] = '3f1c9a7e2b54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVIDER_CODE = "dat"
_DAT_CONNECTIONS = (
    "SELECT c.id FROM integration_connection c "
    "JOIN integration_provider p ON p.id = c.provider_id WHERE p.provider_code = 'dat'"
)


def upgrade() -> None:
    op.execute(f"DELETE FROM integration_entitlement WHERE connection_id IN ({_DAT_CONNECTIONS})")
    op.execute(f"DELETE FROM integration_secret_ref WHERE connection_id IN ({_DAT_CONNECTIONS})")
    op.execute(f"DELETE FROM integration_connection WHERE id IN ({_DAT_CONNECTIONS})")
    op.execute("DELETE FROM integration_entitlement WHERE capability_code = 'vin_decode'")
    op.execute(f"DELETE FROM integration_provider WHERE provider_code = '{_PROVIDER_CODE}'")


def downgrade() -> None:
    """Deliberately a no-op. The deleted connections, their secret references
    and entitlements, and the `vin_decode` cache rows cannot be recovered, and
    the provider row is not put back: the code before this revision reads a
    missing `dat` row as "no such connection", which is what every tenant had
    (no `dat` adapter was ever registered, so no `dat` connection could be
    healthy). Re-seeding a provider for an account model ADR-113 retired would
    offer it again on the Integrations screen."""
