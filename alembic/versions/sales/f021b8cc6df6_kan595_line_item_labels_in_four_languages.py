"""KAN-595: a configured car's factory-option lines freeze four texts

A configuration's option price line now carries the provider's text in each
language (vehicle chain `63bb7ea0e61c`). Sales freezes it with the line
(ADR-041) so the printed offer names each option in the document's language
— the customer's correspondence language (ADR-063) — and never in German
only. Technical shape under ADR-126 (ADR-143).

**Schema.** Four nullable `String(200)` columns `label_de/fr/it/en` on
`sales_line_item`, cut like `label`. NULL on accessories and on the colour
and wheels lines (one text, typed by the advisor).

**Data.** None. Lines frozen before keep their one `label`, which the
document prints as before: a generated document is never re-rendered, and a
draft re-freezes its lines on its configuration's next version (FR-C-12).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f021b8cc6df6"
down_revision: str | Sequence[str] | None = "a5d3f1c7e9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LANGUAGES = ("de", "fr", "it", "en")


def upgrade() -> None:
    for language in _LANGUAGES:
        op.add_column("sales_line_item", sa.Column(f"label_{language}", sa.String(length=200), nullable=True))


def downgrade() -> None:
    for language in _LANGUAGES:
        op.drop_column("sales_line_item", f"label_{language}")
