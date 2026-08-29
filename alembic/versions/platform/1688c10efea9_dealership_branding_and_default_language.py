"""Dealership branding + default correspondence language (WP-6b PR-1,
ADR-051)

Adds the three columns the shared document-template layer needs from
`Dealership` that don't already exist: `logo_url` (nullable — this package
builds no upload/hosting, a WP-6c admin-screen concern), `brand_primary_color`
(hex, defaults to purple[6] `#7C3AED`, the shipped frontend brand colour —
frontend/packages/ui-kit/src/tokens.ts), and `default_correspondence_language`
(the "dealership default" half of Customers FR-13's correspondence-language
rule, for when a document has no customer to read a language from).

Every existing dealership is backfilled to the same two defaults
(`#7C3AED` / `DE`) — genuine placeholders, not a considered per-dealership
choice, so this prints one REVIEW_MIGRATION_REPORT line per affected row
(same convention as a7c4e91f6b2d's manager-flag backfill) rather than
silently treating five rows' worth of "nobody has configured this yet" as
already resolved.

`DE`, not the lowercase enum *value* `de`: SQLAlchemy's
`Enum(native_enum=False)` persists the member NAME by default (the same
convention this file's neighbours already document —
a7c4e91f6b2d/_seed_dealer_old_schema's own comment on FranchiseType/
DealershipStatus) — a raw server_default bypasses the ORM's enum-aware
serialization entirely, so it has to spell the name out correctly by hand.
Caught by the "alembic upgrade from previously-deployed revision" CI job
reading a backfilled row back through the ORM, not by any test in the fast
SQLite lane (which never runs this migration at all).

Revision ID: 1688c10efea9
Revises: 6ba0a99ed5c4
Create Date: 2026-08-29 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1688c10efea9'
down_revision: Union[str, Sequence[str], None] = '6ba0a99ed5c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_BRAND_COLOR = "#7C3AED"
# The enum MEMBER NAME ("DE"), not its value ("de") — see this migration's
# own docstring for why that distinction matters here specifically.
_DEFAULT_LANGUAGE = "DE"


def upgrade() -> None:
    op.add_column("dealership", sa.Column("logo_url", sa.String(length=500), nullable=True))
    op.add_column(
        "dealership",
        sa.Column("brand_primary_color", sa.String(length=7), nullable=False, server_default=_DEFAULT_BRAND_COLOR),
    )
    op.add_column(
        "dealership",
        sa.Column(
            "default_correspondence_language", sa.String(length=8), nullable=False, server_default=_DEFAULT_LANGUAGE
        ),
    )
    # server_default above is what actually backfills existing rows in one
    # statement; drop it once done so future inserts must supply a value
    # explicitly via the ORM default instead of a DB-level default drifting
    # from app.core.i18n.SwissLanguage.DE / the model's own Python default.
    op.alter_column("dealership", "brand_primary_color", server_default=None)
    op.alter_column("dealership", "default_correspondence_language", server_default=None)

    bind = op.get_bind()
    dealership = sa.table("dealership", sa.column("id", sa.String), sa.column("legal_name", sa.String))
    for row in bind.execute(sa.select(dealership.c.id, dealership.c.legal_name)):
        print(
            f"REVIEW_MIGRATION_REPORT: dealership {row.id} ({row.legal_name}) defaulted to "
            f"brand_primary_color={_DEFAULT_BRAND_COLOR!r}, default_correspondence_language={_DEFAULT_LANGUAGE!r} "
            "— an admin should set these deliberately."
        )


def downgrade() -> None:
    op.drop_column("dealership", "default_correspondence_language")
    op.drop_column("dealership", "brand_primary_color")
    op.drop_column("dealership", "logo_url")
