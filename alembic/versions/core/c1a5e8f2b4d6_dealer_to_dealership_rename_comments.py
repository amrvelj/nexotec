"""Dealer -> Dealership rename: comment + audit-log fan-out (WP-3 PR-1)

The rename itself (table `dealer` -> `dealership`) is a platform-chain
migration. Two things in the CORE chain reference the old name and don't
belong in that migration:

  - outbox_message.tenant_id's FK-comment says "(Dealer)" — cosmetic, but
    every other FK-comment in the codebase is being updated in lockstep, and
    a stale one here would be the one place that quietly disagreed.
  - audit_event.entity_type has historical rows tagged "dealer" (written by
    app.platform.services.dealer, now app.platform.services.dealership).
    Bumping the code's own entity_type literal to "dealership" without
    backfilling existing rows would leave the stored tag and the code
    identifier disagreeing forever — a single UPDATE, not a row-by-row walk.

Revision ID: c1a5e8f2b4d6
Revises: dec89ed305a2
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c1a5e8f2b4d6'
down_revision: Union[str, Sequence[str], None] = 'dec89ed305a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "outbox_message",
        "tenant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        comment="Owned by the platform context (Dealership). No DB-level FK.",
        existing_comment="Owned by the platform context (Dealer). No DB-level FK.",
        existing_nullable=True,
    )

    bind = op.get_bind()
    audit_event = sa.table("audit_event", sa.column("entity_type", sa.String))
    bind.execute(audit_event.update().where(audit_event.c.entity_type == "dealer").values(entity_type="dealership"))


def downgrade() -> None:
    bind = op.get_bind()
    audit_event = sa.table("audit_event", sa.column("entity_type", sa.String))
    bind.execute(audit_event.update().where(audit_event.c.entity_type == "dealership").values(entity_type="dealer"))

    op.alter_column(
        "outbox_message",
        "tenant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        comment="Owned by the platform context (Dealer). No DB-level FK.",
        existing_comment="Owned by the platform context (Dealership). No DB-level FK.",
        existing_nullable=True,
    )
