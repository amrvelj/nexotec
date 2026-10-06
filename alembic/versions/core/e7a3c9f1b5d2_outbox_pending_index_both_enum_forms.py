"""outbox pending index matches both enum forms (KAN-86 step 1)

KAN-86 moves every enum column from storing the member NAME to storing
.value, in three steps (app/core/enum_type.py). From step 1 on, the
poller's `OutboxMessage.status == OutboxStatus.PENDING` compiles to
`status IN ('PENDING', 'pending')`, and Postgres only uses a partial index
whose predicate the query's filter implies — `status = 'PENDING'` no longer
is. Rebuilt with the same predicate the filter now has; KAN-86 step 3 narrows
it to `status = 'pending'`.

Revision ID: e7a3c9f1b5d2
Revises: d4f7a2b8e1c9
Create Date: 2026-10-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a3c9f1b5d2'
down_revision: Union[str, Sequence[str], None] = 'd4f7a2b8e1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_outbox_message_pending_next_attempt_at"


def _rebuild(predicate: str) -> None:
    op.drop_index(_INDEX, table_name="outbox_message")
    op.create_index(
        _INDEX,
        "outbox_message",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text(predicate),
    )


def upgrade() -> None:
    _rebuild("status IN ('PENDING', 'pending')")


def downgrade() -> None:
    _rebuild("status = 'PENDING'")
