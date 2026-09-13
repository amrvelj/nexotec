"""ImageRef.image_url (KAN-43, Configurator C-E, FR-C-09)

`Bilder`'s `BildURL` was being discarded at ingestion — `image_key` (the
sync's own natural key) is the URL's basename only, kept short enough for
its 160-char column; the full URL had nowhere to go, an explicit WP-6
Open Item (see `app/vehicle/models/catalogue_mirror.py::ImageRef`'s own
docstring). Without it, no caller — including the Configurator's own
images tab — could actually render a photo, only reference an opaque key.

The table was empty in every environment before this ticket (no caller
had ever populated more than `bild_typ`/`bild_art`/`image_key`/`sequence`),
so no backfill of `image_url` is needed.

Revision ID: 90dc1077a569
Revises: 9d15e6a4c2f0
Create Date: 2026-09-13 00:00:00.000003

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "90dc1077a569"
down_revision: Union[str, Sequence[str], None] = "9d15e6a4c2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vehicle_image_ref", sa.Column("image_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicle_image_ref", "image_url")
