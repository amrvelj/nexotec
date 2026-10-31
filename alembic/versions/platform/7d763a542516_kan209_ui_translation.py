"""KAN-209 — UI translations served at runtime (D-A-23, FR-A-30…32)

Three platform-owned tables (`app/platform/models/ui_translation.py`):

- `ui_translation` — one row per (namespace, key, language): the live value,
  the bundle text it was seeded from and its source (`bundle_seed` /
  `platform`), versioned for If-Match.
- `ui_translation_revision` — the one global version the runtime endpoint's
  ETag is built from; seeded here with its single row at 0 (a plain INSERT,
  which an offline `--sql` render prints as it is — no row is read).
- `missing_translation` — the log of keys a browser rendered as the loud
  marker.

The rows themselves are not seeded here: the bundles live in the frontend
tree, which the backend image does not carry, and a migration that read them
would write different rows depending on when it ran.
`scripts/import_ui_translation_bundles.py` is the one-off (re-runnable) import.

Revision ID: 7d763a542516
Revises: c4f1a9e2b7d3
Create Date: 2026-10-31 14:15:38.024572

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d763a542516"
down_revision: str | Sequence[str] | None = "c4f1a9e2b7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "missing_translation",
        sa.Column("key", sa.String(length=255), nullable=False, comment="The full dotted key the SPA asked for."),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("report_count", sa.Integer(), nullable=False),
        sa.Column("first_reported_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("last_reported_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.CheckConstraint("language IN ('de', 'fr', 'it', 'en')", name="ck_missing_translation_language_enum"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "language", name="uq_missing_translation_key_language"),
    )
    op.create_table(
        "ui_translation",
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "seed_value",
            sa.Text(),
            nullable=False,
            comment="The bundle text this row was seeded from: the panel's diff and a revert's target.",
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", app.core.types.GUID(), nullable=True),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.CheckConstraint("language IN ('de', 'fr', 'it', 'en')", name="ck_ui_translation_language_enum"),
        sa.CheckConstraint("source IN ('bundle_seed', 'platform')", name="ck_ui_translation_source_enum"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "key", "language", name="uq_ui_translation_namespace_key_language"),
    )
    op.create_index("ix_ui_translation_language_source", "ui_translation", ["language", "source"], unique=False)
    op.create_table(
        "ui_translation_revision",
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope"),
    )
    # The one row the service bumps (UI_TRANSLATION_SCOPE). An INSERT reads
    # nothing, so it renders offline as it runs online.
    op.execute("INSERT INTO ui_translation_revision (scope, version, updated_at) VALUES ('ui', 0, now())")


def downgrade() -> None:
    op.drop_table("ui_translation_revision")
    op.drop_index("ix_ui_translation_language_source", table_name="ui_translation")
    op.drop_table("ui_translation")
    op.drop_table("missing_translation")
