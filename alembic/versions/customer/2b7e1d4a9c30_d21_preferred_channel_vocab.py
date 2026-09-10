"""D-21 — customer.preferred_channel vocabulary (KAN-54)

Ruled 2026-09-07: the enum becomes `email` / `phone` / `post` / `whatsapp`.
Three values map 1:1 and are renamed here:

    mail   -> email
    call   -> phone
    letter -> post

`message` has NO clean target — it meant SMS and WhatsApp is not SMS. It is
left untouched; the row count is reported below so a product decision can be
made. `whatsapp` is new (no existing data).

Stored as a plain string (SAEnum(native_enum=False) -> VARCHAR), so this is
three UPDATEs, no type DDL.

Revision ID: 2b7e1d4a9c30
Revises: 1f0a5c7d9e42
Create Date: 2026-09-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b7e1d4a9c30"
down_revision: Union[str, Sequence[str], None] = "1f0a5c7d9e42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RENAMES_UP = {"mail": "email", "call": "phone", "letter": "post"}
_RENAMES_DOWN = {v: k for k, v in _RENAMES_UP.items()}


def _rename(mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    for old, new in mapping.items():
        result = bind.execute(
            sa.text("UPDATE customer SET preferred_channel = :new WHERE preferred_channel = :old"),
            {"new": new, "old": old},
        )
        print(f"D-21: customer.preferred_channel {old!r} -> {new!r}: {result.rowcount} row(s)")

    orphans = bind.execute(
        sa.text("SELECT COUNT(*) FROM customer WHERE preferred_channel = 'message'")
    ).scalar_one()
    print(
        f"D-21 REPORT: {orphans} customer row(s) still hold preferred_channel = 'message' "
        "(SMS — no clean target, left unmapped pending a product decision, KAN-54)"
    )


def upgrade() -> None:
    _rename(_RENAMES_UP)


def downgrade() -> None:
    _rename(_RENAMES_DOWN)
    bind = op.get_bind()
    result = bind.execute(
        sa.text("UPDATE customer SET preferred_channel = NULL WHERE preferred_channel = 'whatsapp'")
    )
    print(
        f"D-21 downgrade: cleared preferred_channel for {result.rowcount} 'whatsapp' row(s) "
        "(the value did not exist before D-21)"
    )
