"""KAN-206: the letterhead layout on the document template, the Betrieb's
e-mail and website, and a location's own letterhead logo

Dealer Administration PRD, "Letterhead and the document template"; Anto's
answers of 2026-11-12:

- `document_template` gains the layout of the one shared template:
  `header_layout` (logo left / right / centred), `logo_size` (S/M/L = 30/40/50
  mm wide, 20 mm high at most), `address_window` (left or right, default right
  — Q1 B), `footer_columns` (the ordered master-data blocks, at most four),
  `draft_watermark` and `page_numbers`. Every one is NOT NULL with a server
  default, so every existing row reads the shipped defaults with no data
  step; the server defaults stay, as the model declares them.
- `dealership.email` and `dealership.website` (Q4 C), optional, for the
  footer's contact column — read at render time, never copied.
- `branding_asset.location_id` (nullable, FK to `location`): a site's own
  letterhead logo. The single unique (dealership, kind) becomes two partial
  unique indexes — the Betrieb's own variants (location_id NULL) and one per
  kind per location. Every existing row is a Betrieb's own, so the first
  index holds exactly what the old constraint held.

The downgrade removes any location logo row first (the old constraint
cannot hold two `primary` rows of one Betrieb); its file stays in the object
store, unreferenced — as a replaced logo's would after a failed delete.

Revision ID: f348a218c4a4
Revises: c7c0901ad259
Create Date: 2026-11-14 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f348a218c4a4"
down_revision: str | Sequence[str] | None = "c7c0901ad259"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording,
# tests/test_migrated_enum_checks.py).
_ENUMS: tuple[tuple[str, int, str, tuple[str, ...]], ...] = (
    ("header_layout", 16, "logo_left", ("logo_left", "logo_right", "logo_centred")),
    ("logo_size", 1, "m", ("s", "m", "l")),
    ("address_window", 8, "right", ("left", "right")),
)


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    for column, length, default, values in _ENUMS:
        op.add_column(
            "document_template",
            sa.Column(column, sa.String(length=length), nullable=False, server_default=default),
        )
        op.create_check_constraint(f"ck_document_template_{column}_enum", "document_template", _check(column, values))
    op.add_column(
        "document_template",
        sa.Column(
            "footer_columns",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("""'["company", "contact", "banking", "register"]'"""),
        ),
    )
    op.add_column(
        "document_template", sa.Column("draft_watermark", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column("document_template", sa.Column("page_numbers", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.add_column("dealership", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("dealership", sa.Column("website", sa.String(length=200), nullable=True))

    op.add_column("branding_asset", sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "branding_asset_location_id_fkey", "branding_asset", "location", ["location_id"], ["id"]
    )
    op.drop_constraint("uq_branding_asset_dealership_kind", "branding_asset", type_="unique")
    op.create_index(
        "uq_branding_asset_dealership_kind",
        "branding_asset",
        ["dealership_id", "kind"],
        unique=True,
        postgresql_where=sa.text("location_id IS NULL"),
    )
    op.create_index(
        "uq_branding_asset_location_kind",
        "branding_asset",
        ["location_id", "kind"],
        unique=True,
        postgresql_where=sa.text("location_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_branding_asset_location_kind", table_name="branding_asset")
    op.drop_index("uq_branding_asset_dealership_kind", table_name="branding_asset")
    op.execute("DELETE FROM branding_asset WHERE location_id IS NOT NULL")
    op.create_unique_constraint("uq_branding_asset_dealership_kind", "branding_asset", ["dealership_id", "kind"])
    op.drop_constraint("branding_asset_location_id_fkey", "branding_asset", type_="foreignkey")
    op.drop_column("branding_asset", "location_id")

    op.drop_column("dealership", "website")
    op.drop_column("dealership", "email")

    op.drop_column("document_template", "page_numbers")
    op.drop_column("document_template", "draft_watermark")
    op.drop_column("document_template", "footer_columns")
    for column, _length, _default, _values in reversed(_ENUMS):
        op.drop_constraint(f"ck_document_template_{column}_enum", "document_template", type_="check")
        op.drop_column("document_template", column)
