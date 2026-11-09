"""KAN-550 — the `aes_key` secret slot leaves `integration_secret_ref.slot` (ADR-111)

`aes_key` was the SOAP «Webservice Fahrzeuge» account's second secret (its
answers were AES-encrypted). The SOAP adapter is gone (KAN-550 PR 1) and no
provider names the slot: the identAPI's is `client_secret` (KAN-488), the
AutoScout24 feed's `password`. `SecretSlot.AES_KEY` is removed (Anto,
2026-11-09, D2), so the CHECK is replaced with the four remaining values
(`tests/test_migrated_enum_checks.py` compares it with the model's).

**No row is deleted here.** KAN-488's `6b98739fe743` deleted every
`auto_i_dat` connection's secret references — the provider that required the
slot. `set_secret` does not check a slot against the provider's
`required_secret_slots` (KAN-602), so until this release, which drops the slot
from the enum, any connection could take `aes_key`; none is known. Should one exist, the upgrade **refuses** with the connections
it names instead of dropping a reference to secret material it cannot reach
(the secrets manager is outside the database). Clear it **on the release still
running** (`DELETE /v1/integrations/connections/{id}/secrets/aes_key`, or delete
the connection): the new release can neither load such a row nor name the slot
in a request. An offline `--sql`
render skips that check — it reads rows — and says so in a comment; the
CHECK's DDL renders as usual (KAN-174; `tests/test_integration_chain_kan550_renders_offline.py`).

Downgrade restores the five-value CHECK; no data changes either way.

Revision ID: db5f1414cbce
Revises: 3b3faa39c5e2
Create Date: 2026-11-09 12:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "db5f1414cbce"
down_revision: str | Sequence[str] | None = "3b3faa39c5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "integration_secret_ref"
_CHECK = "ck_integration_secret_ref_slot_enum"
_RETIRED = "aes_key"
# The model's member order (`SecretSlot`), as `enum_check_condition` words it.
_SLOTS_AFTER = ("password", "client_secret", "refresh_token", "certificate")
_SLOTS_BEFORE = ("password", "aes_key", "client_secret", "refresh_token", "certificate")


def _condition(values: Sequence[str]) -> str:
    return '"slot" IN (' + ", ".join(f"'{value}'" for value in values) + ")"


def refuse_if_the_slot_is_held(conn: sa.engine.Connection) -> None:
    """Raises naming the connections that still hold an `aes_key` reference."""

    held = conn.execute(
        sa.text(f"SELECT DISTINCT connection_id FROM {_TABLE} WHERE slot = :slot ORDER BY connection_id"),
        {"slot": _RETIRED},
    ).scalars().all()
    if held:
        raise RuntimeError(
            f"{len(held)} connection(s) still hold an '{_RETIRED}' secret reference "
            f"({', '.join(str(connection_id) for connection_id in held)}). On the release still running, "
            "clear the slot (DELETE /v1/integrations/connections/{id}/secrets/aes_key) or delete the "
            "connection, then upgrade again: the new release cannot load such a row, and this migration does "
            "not drop a reference to secret material it cannot reach (KAN-550)."
        )


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(f"-- KAN-550 '{_RETIRED}' rows check skipped: it reads rows and needs a live database")
    else:
        refuse_if_the_slot_is_held(op.get_bind())
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _condition(_SLOTS_AFTER))


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _condition(_SLOTS_BEFORE))
