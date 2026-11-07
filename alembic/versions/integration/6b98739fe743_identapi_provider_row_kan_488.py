"""the auto_i_dat provider row describes the identAPI (KAN-488, ADR-111)

The licence Nexotec holds is auto-i-dat's identAPI: a REST / json:api
service behind OAuth 2.0 (auto-i's User Enrollment Grant, scope `ident`).
The `auto_i_dat` row still described the SOAP *Webservice Fahrzeuge*
account: `auth_type = soap_password_aes`, two secret slots (`password`,
`aes_key`), three config keys (`username`, `benutzerNr`, `benutzerInfo`),
and the eight counters of a SOAP account sheet as `capability_codes`
(49be490e2077). This migration restates the row:

* `auth_type = oauth2_user_enrollment` — auto-i's instruction for Nexotec
  (mail of 2026-11-07): metadata discovery plus the User Enrollment Grant,
  not the client-credentials grant ADR-111 first named (amended the same day).
* one secret slot, `client_secret`; one config key, `clientId` (the client
  id is an identifier, not a secret, and is the card's account identifier).
* `capability_codes` = the identAPI's licence options as the test access
  shows them: `ident_basis` (identification with list price), `ausstattung`
  (options, packages, images), `bewertung` (valuation / residual value),
  `vin` (VIN queries — off in the test licence, §8), `pneu` (tyres and
  rims), `energielabel`. Each is probed by its own identAPI call on "Test
  connection" (`services/entitlement_probes.py`). The mock provider carries
  the same vocabulary so `integration_entitlement` has one.
* `docs_url` points at auto-i's own OpenAPI document.

Existing `auto_i_dat` connections carry SOAP credentials the identAPI cannot
use: their secret references and entitlement rows are deleted, their config
emptied and their status set to `not_configured` with a `last_error` that
tells the dealer manager what to enter. The mock's connections keep their
config and lose only their entitlement rows (keyed by the old vocabulary).
Every step is a data step, so the whole migration is skipped in an offline
`--sql` render (KAN-174) — there is no DDL to render. Only staging's demo connection
exists (no production dealer has one). The secret *material* those
references pointed at stays in the secrets manager under the connection's
path until the connection is deleted or the slot is set again: this
migration cannot reach the secrets manager, and says so here.

The `dat` provider row is untouched: the VIN ticket (KAN-549) retires it.

Revision ID: 6b98739fe743
Revises: 9c4485cabd87
Create Date: 2026-11-07 18:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = '6b98739fe743'
down_revision: Union[str, Sequence[str], None] = '9c4485cabd87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVIDER_CODE = "auto_i_dat"
_MOCK_PROVIDER_CODE = "auto_i_dat_mock"

# The identAPI's licence options (read by tests/architecture/
# test_capability_codes_are_in_the_vocabulary.py as the vocabulary).
_IDENTAPI_CAPABILITY_CODES = ["ident_basis", "ausstattung", "bewertung", "vin", "pneu", "energielabel"]

_NEW = {
    "display_name": "auto-i-dat (identAPI)",
    "auth_type": "oauth2_user_enrollment",
    "required_secret_slots": ["client_secret"],
    "required_config_keys": ["clientId"],
    "capability_codes": _IDENTAPI_CAPABILITY_CODES,
    "docs_url": "https://api.ident.autoi.ch/docs/index.html?url=/swagger/v1/swagger.json",
}
# What f3c9a1e6d8b2 + 49be490e2077 left on the row, for `downgrade`.
_OLD = {
    "display_name": "auto-i-dat",
    "auth_type": "soap_password_aes",
    "required_secret_slots": ["password", "aes_key"],
    "required_config_keys": ["username", "benutzerNr", "benutzerInfo"],
    "capability_codes": ["fahrzeuge", "optionen", "kontrollschild", "pneu", "bewertung", "vin", "vin_ident_db", "ins_tc"],
    "docs_url": None,
}

_NOT_CONFIGURED_LAST_ERROR = (
    "auto-i-dat is now reached through the identAPI (KAN-488): enter the client id and the client secret "
    "of the service account auto-i-dat issued, then test the connection."
)


def _provider_table() -> sa.TableClause:
    return sa.table(
        "integration_provider",
        sa.column("provider_code", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("auth_type", sa.String()),
        sa.column("required_secret_slots", sa.JSON()),
        sa.column("required_config_keys", sa.JSON()),
        sa.column("capability_codes", sa.JSON()),
        sa.column("docs_url", sa.String()),
    )


def upgrade() -> None:
    if context.is_offline_mode():
        # KAN-174: every step here is a data step (JSON provider rows, the
        # connection reset with its row counts) and needs a live database;
        # there is no DDL to render.
        op.execute("-- KAN-488 (6b98739fe743): data-only migration skipped in offline mode; needs a live database")
        return

    bind = op.get_bind()
    provider = _provider_table()
    bind.execute(provider.update().where(provider.c.provider_code == _PROVIDER_CODE).values(**_NEW))
    bind.execute(
        provider.update()
        .where(provider.c.provider_code == _MOCK_PROVIDER_CODE)
        .values(capability_codes=_IDENTAPI_CAPABILITY_CODES)
    )

    connection_ids = sa.text(
        "SELECT id FROM integration_connection "
        "WHERE provider_id IN (SELECT id FROM integration_provider WHERE provider_code = :code)"
    )
    refs = bind.execute(
        sa.text(f"DELETE FROM integration_secret_ref WHERE connection_id IN ({connection_ids.text})"), {"code": _PROVIDER_CODE}
    )
    entitlements = bind.execute(
        sa.text(f"DELETE FROM integration_entitlement WHERE connection_id IN ({connection_ids.text})"),
        {"code": _PROVIDER_CODE},
    )
    # The mock's connections keep their config (no credentials), but their
    # entitlement rows are keyed by the SOAP sheet lines, now outside the
    # vocabulary: dropped, re-probed on the next Test connection.
    mock_entitlements = bind.execute(
        sa.text(f"DELETE FROM integration_entitlement WHERE connection_id IN ({connection_ids.text})"),
        {"code": _MOCK_PROVIDER_CODE},
    )
    connections = bind.execute(
        sa.text(
            "UPDATE integration_connection "
            "SET status = 'not_configured', last_error = :last_error, config = '{}'::json, "
            "    rotated_at = NULL, last_verified_at = NULL, version = version + 1 "
            "WHERE provider_id IN (SELECT id FROM integration_provider WHERE provider_code = :code)"
        ),
        {"code": _PROVIDER_CODE, "last_error": _NOT_CONFIGURED_LAST_ERROR},
    )
    print(
        f"KAN-488: reset {connections.rowcount} auto_i_dat connection(s) to not_configured; "
        f"deleted {refs.rowcount} SOAP secret reference(s), {entitlements.rowcount} entitlement row(s) "
        f"and {mock_entitlements.rowcount} mock entitlement row(s). "
        "Secret material under those references stays in the secrets manager until the slot is set again."
    )


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-488 (6b98739fe743): data-only downgrade skipped in offline mode; needs a live database")
        return
    bind = op.get_bind()
    provider = _provider_table()
    bind.execute(provider.update().where(provider.c.provider_code == _PROVIDER_CODE).values(**_OLD))
    bind.execute(
        provider.update()
        .where(provider.c.provider_code == _MOCK_PROVIDER_CODE)
        .values(capability_codes=_OLD["capability_codes"])
    )
    # The deleted secret references, entitlement rows and the emptied config
    # of existing connections are not restored: the SOAP credentials they
    # pointed at are not known here, and a connection left `not_configured`
    # is honest about that. The dealer manager re-enters them.
