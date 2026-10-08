"""idempotency_record in-flight claim (KAN-119)

`app/core/idempotent_route.py` claims an Idempotency-Key BEFORE the route
does its work: it commits a row with no response yet, so a second request
carrying the same key while the first is still running finds the claim and
gets a 409 instead of doing the work twice. The row is completed with the
response once the route succeeds, or deleted when it fails.

"No response yet" is the in-flight state: `response_status IS NULL`.
`response_body` becomes nullable with it, because an in-flight row has no
body and a completed one may legitimately have none (an empty 2xx).

Every existing row is a completed one (status and body set), so the upgrade
rewrites nothing.

Downgrade deletes the in-flight rows before restoring NOT NULL — there is no
response to put in them. A request still running at that moment loses its
claim and stays unprotected against a concurrent twin, which is what the
pre-KAN-119 code it would be running under did anyway. A completed row whose
body is NULL (an empty 2xx) gets the JSON literal `null`, which the column
held for such responses before.

Revision ID: 5c5236cacf7e
Revises: 65aa495aaa91
Create Date: 2026-10-07 17:59:05.002065

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c5236cacf7e"
down_revision: Union[str, Sequence[str], None] = "65aa495aaa91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("idempotency_record", "response_status", existing_type=sa.Integer(), nullable=True)
    op.alter_column("idempotency_record", "response_body", existing_type=sa.JSON(), nullable=True)


def downgrade() -> None:
    conn = op.get_bind()
    deleted = conn.execute(sa.text("DELETE FROM idempotency_record WHERE response_status IS NULL"))
    print(f"KAN-119 downgrade: {deleted.rowcount} in-flight idempotency claim(s) deleted")
    conn.execute(sa.text("UPDATE idempotency_record SET response_body = 'null' WHERE response_body IS NULL"))
    op.alter_column("idempotency_record", "response_body", existing_type=sa.JSON(), nullable=False)
    op.alter_column("idempotency_record", "response_status", existing_type=sa.Integer(), nullable=False)
