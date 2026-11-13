"""when each language of a provider's captions was last fetched (KAN-588)

`integration_provider_caption_fetch`: one row per (provider, language) —
`fetched_at` and how many codes came back — written with the captions by
«Test connection» and by platform staff's «Captions aktualisieren». A caption
row's own `fetched_at` is overwritten by each language in turn, so it cannot
say when one language was fetched. Global vocabulary, like the captions; the
model is `app/integration/models/caption.py`.

Data step: seeds one row per (provider, language) that has any label, from
this context's own `integration_provider_caption`: the latest `fetched_at`
among rows carrying that language. Every language of one «Test connection» is
fetched within seconds of the others, so that time is within seconds of the
real one. Skipped in offline mode (KAN-174).

Revision ID: b1f58e30a656
Revises: db5f1414cbce
Create Date: 2026-11-13 11:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

from app.core.types import GUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "b1f58e30a656"
down_revision: Union[str, Sequence[str], None] = "db5f1414cbce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LANGUAGES = ("de", "fr", "it", "en")


def upgrade() -> None:
    op.create_table(
        "integration_provider_caption_fetch",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("lang", sa.String(length=2), nullable=False),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("caption_count", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        # TimestampMixin's actor columns: always NULL here — the audit row names who refreshed.
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "lang", name="uq_integration_provider_caption_fetch_provider_lang"),
    )
    if context.is_offline_mode():
        op.execute("-- KAN-588 seed of integration_provider_caption_fetch skipped: it reads rows and needs a live database")
        return
    from app.core.uuid7 import uuid7

    bind = op.get_bind()
    fetch = sa.table(
        "integration_provider_caption_fetch",
        sa.column("id", GUID()), sa.column("provider", sa.String()), sa.column("lang", sa.String()),
        sa.column("fetched_at", UTCDateTime()), sa.column("caption_count", sa.Integer()),
        sa.column("created_at", UTCDateTime()), sa.column("updated_at", UTCDateTime()),
    )
    rows = []
    for lang in _LANGUAGES:
        for provider, fetched_at, count in bind.execute(
            sa.text(
                f"SELECT provider, MAX(fetched_at), COUNT(*) FROM integration_provider_caption "
                f"WHERE label_{lang} IS NOT NULL GROUP BY provider"
            )
        ):
            rows.append(
                {
                    "id": uuid7(), "provider": provider, "lang": lang, "fetched_at": fetched_at,
                    "caption_count": count, "created_at": fetched_at, "updated_at": fetched_at,
                }
            )
    if rows:
        op.bulk_insert(fetch, rows)


def downgrade() -> None:
    op.drop_table("integration_provider_caption_fetch")
