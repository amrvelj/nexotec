"""the provider caption store (KAN-547, ADR-111 (3))

`integration_provider_caption`: a provider's own vocabulary — what each
code it sends means, in DE/FR/IT/EN — fetched by «Test connection» from the
identAPI's `GET /caption/code?lang=` and stored globally (vocabulary, not
vehicle data; no `tenant_id`). One row per (provider, caption group, list,
code); the four label columns are filled one language at a time, so a
language not fetched yet is NULL. The model is
`app/integration/models/caption.py`.

DDL only; nothing to skip offline.

Revision ID: 3f1c9a7e2b54
Revises: 6b98739fe743
Create Date: 2026-11-07 22:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.types import GUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "3f1c9a7e2b54"
down_revision: Union[str, Sequence[str], None] = "6b98739fe743"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_provider_caption",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("caption_group", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label_de", sa.String(length=200), nullable=True),
        sa.Column("label_fr", sa.String(length=200), nullable=True),
        sa.Column("label_it", sa.String(length=200), nullable=True),
        sa.Column("label_en", sa.String(length=200), nullable=True),
        sa.Column("label_short_de", sa.String(length=80), nullable=True),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        # TimestampMixin's actor columns: always NULL here — Test connection writes, never a user.
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "caption_group", "kind", "code", name="uq_integration_provider_caption_natural_key"
        ),
    )
    op.create_index(
        op.f("ix_integration_provider_caption_provider"), "integration_provider_caption", ["provider"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_integration_provider_caption_provider"), table_name="integration_provider_caption")
    op.drop_table("integration_provider_caption")
