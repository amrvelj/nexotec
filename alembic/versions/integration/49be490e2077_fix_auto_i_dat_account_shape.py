"""fix the auto_i_dat account shape (KAN-36)

An auto-i-dat account is issued as seven fields in two groups (Emil Frey
Info / API Spezifikationen / Auto I Webservice Konti.xlsx — a real account
sheet; no value from it is ever copied into this repo, only field shapes).
`required_config_keys` was empty; it should require `username`,
`benutzerNr` (small integer, the account number) and `benutzerInfo` (the
project label — one dealer can hold several auto-i-dat accounts,
distinguished only by this field). `capability_codes` carried an invented
vocabulary (["vehicle_data", "images", "packages", "valuation",
"forecast"]) never grounded in anything auto-i-dat actually bills by;
replaced with the account sheet's own eight counters, each commented to
its sheet column below. Both changes are confirmed safe: `capability_codes`
is a display-only catalogue field, read by no application code and
rendered by no frontend screen (grepped clean across app/ and frontend/).

Existing `auto_i_dat` connections predate `benutzerNr` and cannot have it
invented for them — each such connection is marked ConnectionStatus.ERROR
with a descriptive last_error, the only "needs attention" mechanism the
Integrations screen has today (there is no dedicated "incomplete" status).
The dealer manager sees this on their next visit to the Integrations
screen and updates the connection's config themselves.

Revision ID: 49be490e2077
Revises: b4522b27b146
Create Date: 2026-09-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '49be490e2077'
down_revision: Union[str, Sequence[str], None] = 'b4522b27b146'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The account sheet's own per-account monthly counters — this is what a
# dealer's own auto-i-dat bill is itemised by, so these are the codes,
# never a vocabulary invented in this repo. Column names as printed on
# the sheet:
#   fahrzeuge    -> Fzg         (vehicle master data)
#   optionen     -> Opt         (options/packages)
#   kontrollschild -> KS        (plate lookup)
#   pneu         -> Pneu        (tyre specs)
#   bewertung    -> Bew         (valuation)
#   vin          -> VIN         (VIN decode)
#   vin_ident_db -> VINIdentDB  (VIN identification database)
#   ins_tc       -> InsTC       (Typenschein insertion)
_AUTO_I_DAT_CAPABILITY_CODES = [
    "fahrzeuge", "optionen", "kontrollschild", "pneu", "bewertung", "vin", "vin_ident_db", "ins_tc",
]
_AUTO_I_DAT_REQUIRED_CONFIG_KEYS = ["username", "benutzerNr", "benutzerInfo"]
_OLD_CAPABILITY_CODES = ["vehicle_data", "images", "packages", "valuation", "forecast"]

_INCOMPLETE_LAST_ERROR = (
    "Missing benutzerNr — update this connection with the account number issued by Emil Frey (KAN-36)."
)


def upgrade() -> None:
    bind = op.get_bind()

    integration_provider = sa.table(
        "integration_provider",
        sa.column("provider_code", sa.String()),
        sa.column("required_config_keys", sa.JSON()),
        sa.column("capability_codes", sa.JSON()),
    )
    bind.execute(
        integration_provider.update()
        .where(integration_provider.c.provider_code == "auto_i_dat")
        .values(required_config_keys=_AUTO_I_DAT_REQUIRED_CONFIG_KEYS, capability_codes=_AUTO_I_DAT_CAPABILITY_CODES)
    )
    bind.execute(
        integration_provider.update()
        .where(integration_provider.c.provider_code == "auto_i_dat_mock")
        .values(capability_codes=_AUTO_I_DAT_CAPABILITY_CODES)
    )

    result = bind.execute(
        sa.text(
            """
            UPDATE integration_connection
            SET status = 'ERROR', last_error = :last_error
            WHERE provider_id IN (SELECT id FROM integration_provider WHERE provider_code = 'auto_i_dat')
              AND (config ->> 'benutzerNr') IS NULL
            """
        ),
        {"last_error": _INCOMPLETE_LAST_ERROR},
    )
    print(f"KAN-36: marked {result.rowcount} existing auto_i_dat connection(s) as needing benutzerNr.")


def downgrade() -> None:
    bind = op.get_bind()
    integration_provider = sa.table(
        "integration_provider",
        sa.column("provider_code", sa.String()),
        sa.column("required_config_keys", sa.JSON()),
        sa.column("capability_codes", sa.JSON()),
    )
    bind.execute(
        integration_provider.update()
        .where(integration_provider.c.provider_code == "auto_i_dat")
        .values(required_config_keys=[], capability_codes=_OLD_CAPABILITY_CODES)
    )
    bind.execute(
        integration_provider.update()
        .where(integration_provider.c.provider_code == "auto_i_dat_mock")
        .values(capability_codes=_OLD_CAPABILITY_CODES)
    )
    # The ERROR status this upgrade assigned to incomplete connections is
    # never reverted — we don't know what status they held before, and a
    # downgrade guessing "connected" back would be worse than leaving it.
