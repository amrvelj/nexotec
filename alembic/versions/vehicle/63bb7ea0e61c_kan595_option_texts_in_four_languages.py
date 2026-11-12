"""KAN-595: catalogue and configuration options keep the identAPI's four texts

The identAPI's `VehicleOptionData` carries `descriptionDe/Fr/It/En`; the
option row kept one text (`description`, the language asked for). Technical
shape under ADR-126 (ADR-143).

**Schema.** Four nullable `Text` columns `description_de/fr/it/en` on
`vehicle_variant_option` (the Betrieb's catalogue row, filled by the loader)
and on `vehicle_configuration_option` (copied by the server from the
catalogue row the option names; NULL on a hand-typed option).
`description` stays NOT NULL, the delivered default every reader falls back to.

**Data.** None. Rows stored before stay single-language until the Betrieb's
next «Neu laden» of that car (ADR-123: no automatic refresh); configurations
saved before keep their one text. Readers fall back to `description`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "63bb7ea0e61c"
down_revision: str | Sequence[str] | None = "b9a896524725"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("vehicle_variant_option", "vehicle_configuration_option")
_LANGUAGES = ("de", "fr", "it", "en")


def upgrade() -> None:
    for table in _TABLES:
        for language in _LANGUAGES:
            op.add_column(table, sa.Column(f"description_{language}", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        for language in _LANGUAGES:
            op.drop_column(table, f"description_{language}")
