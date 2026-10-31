"""Branding assets, the secondary brand colour and the logo placements (KAN-205)

Dealer Administration PRD, Logo and brand assets:

- `branding_asset` — one row per logo variant (`primary` · `mono_dark` ·
  `favicon`) and dealership: the object-store key and what was judged on
  upload (type, pixel size, bytes, who and when). Never the bytes, never
  base64. The FK to dealership is real — both ends are platform's.
- `dealership.brand_secondary_color` — optional, #RRGGBB.
- `dealership.logo_on_*` — one switch per placement (document header, price
  tag, marketplace listing, e-mail signature, app header). Every existing
  Betrieb gets the shipped defaults from the server default in one statement:
  on, except the app header ("optionally the app header"). The server
  defaults stay, as the models declare them.

No data step: an existing `dealership.logo_url` is an external URL, and a
migration makes no network call. `scripts/migrate_logo_urls_to_branding_assets.py`
moves those into the store (dry run by default, a report either way); the
renderer keeps reading `logo_url` for a Betrieb it has not moved yet.

Revision ID: 444ddb84d8c9
Revises: 83d8634ff337
Create Date: 2026-10-31 14:25:36.467877

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "444ddb84d8c9"
down_revision: str | Sequence[str] | None = "83d8634ff337"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording,
# tests/test_migrated_enum_checks.py).
_KINDS = ("primary", "mono_dark", "favicon")

_PLACEMENTS = (
    ("logo_on_document_header", True),
    ("logo_on_price_tag", True),
    ("logo_on_listing", True),
    ("logo_on_email_signature", True),
    ("logo_on_app_header", False),
)


def upgrade() -> None:
    op.create_table(
        "branding_asset",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dealership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dealership.id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=32), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            '"kind" IN (' + ", ".join(f"'{kind}'" for kind in _KINDS) + ")", name="ck_branding_asset_kind_enum"
        ),
        sa.UniqueConstraint("dealership_id", "kind", name="uq_branding_asset_dealership_kind"),
    )
    op.add_column("dealership", sa.Column("brand_secondary_color", sa.String(length=7), nullable=True))
    for column, default in _PLACEMENTS:
        op.add_column(
            "dealership",
            sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.true() if default else sa.false()),
        )


def downgrade() -> None:
    for column, _default in reversed(_PLACEMENTS):
        op.drop_column("dealership", column)
    op.drop_column("dealership", "brand_secondary_color")
    op.drop_table("branding_asset")
