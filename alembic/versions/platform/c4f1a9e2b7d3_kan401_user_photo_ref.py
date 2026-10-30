"""KAN-401 — a user's photo (Dealer Administration, Users → Fields)

`user.photo_ref` is the object-store key of the photo, never the bytes — the
same shape as `signature_image_ref` (KAN-200). Nullable: nobody has a photo
until one is uploaded.

Revision ID: c4f1a9e2b7d3
Revises: b13e074523d7
Create Date: 2026-10-30 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f1a9e2b7d3"
down_revision: str | Sequence[str] | None = "b13e074523d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("photo_ref", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "photo_ref")
