"""the administration family's first event: an integration nearing its expiry (KAN-222)

One `notification_event` row, `administration.integration_expiring` (FR-A-22,
D-A-40): T-30/14/7 before an integration connection expires, its Betrieb's
managers are told in the app. Raised by platform's consumer of integration's
`integration.connection.expiry_approaching` fact. Immediate by default and
switchable (the security family alone is locked). The row is a literal here
(ADR-133); `tests/test_migrated_notification_catalogue.py` keeps it equal to
`app/platform/services/notification_catalogue.py`.

Revision ID: 7d2e5b9c4a18
Revises: f348a218c4a4
Create Date: 2026-11-14 12:30:00.000000

"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.types import GUID

# revision identifiers, used by Alembic.
revision: str = "7d2e5b9c4a18"
down_revision: Union[str, Sequence[str], None] = "f348a218c4a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODE = "administration.integration_expiring"
_EVENT_ID = uuid.UUID("019a8e3c-5d21-7b4e-9a6f-2c8d4e1b7a30")


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
                "title_key": "notifications.events.integrationExpiring.title",
                "body_key": "notifications.events.integrationExpiring.body",
            }
        ],
    )


def downgrade() -> None:
    # The inbox rows name the code (a foreign key): they go first.
    op.execute(sa.text("DELETE FROM notification WHERE event_code = :code").bindparams(code=_CODE))
    op.execute(sa.text("DELETE FROM notification_event WHERE code = :code").bindparams(code=_CODE))
