"""KAN-550 — retire the SOAP-era codes from the code-map tables (ADR-111)

Data only — no schema change. KAN-550 retired the SOAP «Webservice Fahrzeuge»
adapter and reshaped the demo provider (`auto_i_dat_mock`) to send the
identAPI's own codes: vehicle kind `01` where its fixtures sent the SOAP-era
`1`, gearbox `1`/`5` where they sent `M`/`A`. Three steps, each plain DML with
its constants bound on the statement (no row is read in Python, and an offline
`--sql` render writes the values — `tests/test_vehicle_chain_kan550_renders_offline.py`):

1. **The demo provider's code map follows its vehicle kind.** Rows seeded for
   `auto_i_dat_mock` under kind `1` (`b0a125a0a583`'s colour/axle/tyre codes,
   `scripts/seed_demo_provider_code_map.py`'s option groups, any row an admin
   resolved) move to kind `01`; where the same key already exists under `01`
   (the demo copy script copies the real provider's map), the `01` row wins
   and the `1` row goes. Rows that only ever keyed a SOAP-era code go: the
   self-referential vehicle kind `1` and the gearbox codes `A`/`M`.
2. **The demo provider's recorded raw codes and gaps go.** Every
   `auto_i_dat_mock` row of `vehicle_variant_provider_code`,
   `vehicle_variant_option_provider_code` and `vehicle_mapping_gap` was
   written from the SOAP-era fixtures; the next lookup records the identAPI's
   codes again (the tables are a cache of what the provider last sent, KAN-83 /
   KAN-250). Demo data only: production never carries a mock connection
   (KAN-489).
3. **Mapping gaps keyed by a numeric CodeGrpNr go**, for every provider. KAN-194
   (`ff5b97c11665`) re-keyed the ones the seed's CodeGrpNr table maps and left
   the rest (014, 110, 500, 550, …) visible but unresolvable (`resolve_mapping_gap`
   refuses a numeric code group with a 409); no adapter can write one any more.

**What stays, by Anto's ruling (2026-11-09, D1).** The `auto_i_dat` code map
seeded by `7c4e9a2b6d13` is *not* removed, although KAN-550's text asked for
it: its rows are keyed by vehicle kind (`01`/`02`/`03`) and semantic field,
never by CodeGrpNr, and since KAN-547 they are the identAPI's live map (Anto,
2026-11-07: "not re-keyed — the identAPI's codes are auto-i's own"). The CI
check that every seeded row — `112 → engine_cycle` among them — survives a
migration run (`scripts/verify_migration_smoke_test_seed.py`) stays with it:
`engine_cycle` is filled from a motorcycle's `drive` code, which the identAPI
sends.

**Downgrade is a deliberate no-op.** The deleted rows are a cache the next
lookup rewrites, gaps nothing can resolve, and demo map rows for a code no
adapter sends; recreating them would only recreate the SOAP vocabulary.

Revision ID: bb6e760d9464
Revises: a7c2d94e1b35
Create Date: 2026-11-09 10:42:40.769783

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bb6e760d9464"
down_revision: str | Sequence[str] | None = "a7c2d94e1b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_PROVIDER = "auto_i_dat_mock"
SOAP_KIND = "1"
IDENTAPI_KIND = "01"
SOAP_GEARBOX_CODES = ("A", "M")


def _statement(sql: str) -> sa.TextClause:
    """The step's constants bound on the statement itself, so an offline
    `--sql` render (`literal_binds`, `alembic/env.py`) writes their values;
    parameters passed to `execute` alongside would render as NULL there."""

    params = {
        "provider": sa.bindparam("provider", DEMO_PROVIDER),
        "soap_kind": sa.bindparam("soap_kind", SOAP_KIND),
        "kind": sa.bindparam("kind", IDENTAPI_KIND),
        "soap_gearbox_codes": sa.bindparam("soap_gearbox_codes", list(SOAP_GEARBOX_CODES), expanding=True),
    }
    return sa.text(sql).bindparams(*(param for name, param in params.items() if f":{name}" in sql))


def retire_demo_soap_codes(conn: Connection) -> None:
    """Steps 1 and 2. Takes a connection so tests can run it against the
    create_all schema after `b0a125a0a583`'s own `_seed`."""

    conn.execute(
        _statement(
            "DELETE FROM vehicle_provider_code_map WHERE provider = :provider AND ("
            " (vehicle_kind = :soap_kind AND code_group = 'vehicle_kind')"
            " OR (code_group = 'transmission' AND provider_code IN :soap_gearbox_codes))"
        )
    )
    conn.execute(
        _statement(
            "DELETE FROM vehicle_provider_code_map WHERE provider = :provider AND vehicle_kind = :soap_kind"
            " AND EXISTS (SELECT 1 FROM vehicle_provider_code_map AS kept"
            "  WHERE kept.provider = vehicle_provider_code_map.provider AND kept.vehicle_kind = :kind"
            "  AND kept.code_group = vehicle_provider_code_map.code_group"
            "  AND kept.provider_code = vehicle_provider_code_map.provider_code)"
        )
    )
    conn.execute(
        _statement(
            "UPDATE vehicle_provider_code_map SET vehicle_kind = :kind, updated_at = CURRENT_TIMESTAMP"
            " WHERE provider = :provider AND vehicle_kind = :soap_kind"
        )
    )
    for table in ("vehicle_variant_option_provider_code", "vehicle_variant_provider_code", "vehicle_mapping_gap"):
        conn.execute(_statement(f"DELETE FROM {table} WHERE provider = :provider"))


def purge_codegrpnr_gaps(conn: Connection) -> None:
    """Step 3: every gap whose code group is all digits."""

    if conn.dialect.name == "postgresql":
        condition = "code_group ~ '^[0-9]+$'"
    else:  # the SQLite fast lane
        condition = "code_group <> '' AND code_group NOT GLOB '*[^0-9]*'"
    conn.execute(sa.text(f"DELETE FROM vehicle_mapping_gap WHERE {condition}"))


def upgrade() -> None:
    conn = op.get_bind()
    retire_demo_soap_codes(conn)
    purge_codegrpnr_gaps(conn)


def downgrade() -> None:
    # Deliberately a no-op — see the module docstring.
    pass
