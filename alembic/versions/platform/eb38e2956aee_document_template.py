"""document_template (WP-6b PR-2, ADR-044 tier 2)

One row per dealership (unique dealership_id), never one per document
type — see app.platform.models.document_template's own docstring. No row
is seeded for existing dealerships: a missing template is a valid, empty
state the render layer already tolerates, not a gap to backfill.

Revision ID: eb38e2956aee
Revises: 1688c10efea9
Create Date: 2026-08-29 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'eb38e2956aee'
down_revision: Union[str, Sequence[str], None] = '1688c10efea9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_template",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dealership_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dealership.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("header_note_de", sa.Text(), nullable=True),
        sa.Column("header_note_fr", sa.Text(), nullable=True),
        sa.Column("header_note_it", sa.Text(), nullable=True),
        sa.Column("header_note_en", sa.Text(), nullable=True),
        sa.Column("footer_text_de", sa.Text(), nullable=True),
        sa.Column("footer_text_fr", sa.Text(), nullable=True),
        sa.Column("footer_text_it", sa.Text(), nullable=True),
        sa.Column("footer_text_en", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_document_template_dealership_id", "document_template", ["dealership_id"])


def downgrade() -> None:
    op.drop_table("document_template")
