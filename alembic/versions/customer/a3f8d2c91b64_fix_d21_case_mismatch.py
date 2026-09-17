"""KAN-60 — fix D-21's case-mismatch: it never actually converted anything

`2b7e1d4a9c30` (D-21, "customer.preferred_channel vocabulary") ran
`UPDATE customer SET preferred_channel = 'email' WHERE preferred_channel = 'mail'`
(and call->phone, letter->post) — lowercase on both sides.

`Customer.preferred_channel` is `SAEnum(PreferredChannel, native_enum=False)`
with no `values_callable` override, so SQLAlchemy's default convention
applies: it stores the enum MEMBER NAME, not `.value` — confirmed by the
production traceback that surfaced this (KAN-60): a stored 'MAIL' failed to
decode with `Possible values: EMAIL, PHONE, POST, ..., MESSAGE` (uppercase
names). D-21's lowercase WHERE clause therefore never matched a single row,
on any environment it ran on — the rename was a complete, silent no-op, not
a fix that merely missed one straggler.

This migration does what D-21 intended, against the vocabulary actually
stored: MAIL -> EMAIL, CALL -> PHONE, LETTER -> POST. Same non-decision on
MESSAGE (still no clean target, KAN-54) — row count reported, not touched.

Revision ID: a3f8d2c91b64
Revises: 3c8f2a6b1e40
Create Date: 2026-09-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f8d2c91b64"
down_revision: Union[str, Sequence[str], None] = "3c8f2a6b1e40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RENAMES_UP = {"MAIL": "EMAIL", "CALL": "PHONE", "LETTER": "POST"}
_RENAMES_DOWN = {v: k for k, v in _RENAMES_UP.items()}


def _rename(conn, mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        result = conn.execute(
            sa.text("UPDATE customer SET preferred_channel = :new WHERE preferred_channel = :old"),
            {"new": new, "old": old},
        )
        print(f"KAN-60: customer.preferred_channel {old!r} -> {new!r}: {result.rowcount} row(s)")

    orphans = conn.execute(
        sa.text("SELECT COUNT(*) FROM customer WHERE preferred_channel = 'MESSAGE'")
    ).scalar_one()
    print(
        f"KAN-60 REPORT: {orphans} customer row(s) still hold preferred_channel = 'MESSAGE' "
        "(SMS — no clean target, left unmapped pending a product decision, KAN-54 — unchanged by this migration)"
    )


def upgrade() -> None:
    _rename(op.get_bind(), _RENAMES_UP)


def downgrade() -> None:
    _rename(op.get_bind(), _RENAMES_DOWN)
