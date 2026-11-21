"""the Business Profile item's own notification (KAN-776)

One `notification_event` row, `administration.business_profile_interrupted`
(Anto, 2026-11-21, KAN-776 decision 2 B): when Nexotec's Google Business
Profile client is interrupted, the managers of every Betrieb with a live
binding are told «we are on it, nothing to do on your side» — never the
generic status notice's «how to fix it». Raised by platform's daily status job
for the item that names it. Immediate by default and switchable, like the
administration family's other events. The row is a literal here (ADR-133);
`tests/platform/test_migrated_notification_catalogue.py` keeps it equal to
`app/platform/identity/services/notification_catalogue.py`.

Revision ID: ab39a4d71b26
Revises: b193aebca408
Create Date: 2026-11-21 07:54:37.396746

"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.types import GUID

# revision identifiers, used by Alembic.
revision: str = "ab39a4d71b26"
down_revision: Union[str, Sequence[str], None] = "b193aebca408"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODE = "administration.business_profile_interrupted"
_EVENT_ID = uuid.UUID("01a1fd19-a8cb-73a5-a920-567f8fb61901")


def _table() -> sa.Table:
    return sa.table(
        "notification_event",
        sa.column("id", GUID()),
        sa.column("code", sa.String()),
        sa.column("family", sa.String()),
        sa.column("default_timing", sa.String()),
        sa.column("switchable", sa.Boolean()),
        sa.column("title_key", sa.String()),
        sa.column("body_key", sa.String()),
    )


def upgrade() -> None:
    op.bulk_insert(
        _table(),
        [
            {
                "id": _EVENT_ID,
                "code": _CODE,
                "family": "administration",
                "default_timing": "immediate",
                "switchable": True,
                "title_key": "notifications.events.businessProfileInterrupted.title",
                "body_key": "notifications.events.businessProfileInterrupted.body",
            }
        ],
    )


def downgrade() -> None:
    # The inbox rows name the code (a foreign key): they go first.
    op.execute(sa.text("DELETE FROM notification WHERE event_code = :code").bindparams(code=_CODE))
    op.execute(sa.text("DELETE FROM notification_event WHERE code = :code").bindparams(code=_CODE))
