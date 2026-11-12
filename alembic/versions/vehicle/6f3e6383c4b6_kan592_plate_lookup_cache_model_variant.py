"""KAN-592: the plate-lookup cache remembers the variant the provider named

Decided as a technical question (ADR-126, architect's call, board clean-up
2026-11-11: option A). A plate lookup's Vehicle Build answer names the
registered car's catalogue variant (FzKey); the 30-day cache kept only the
Typenschein, so a repeat lookup within 30 days could be a picker over every
stored variant sharing it where the live lookup had been one candidate.

**Schema.** `vehicle_plate_lookup_cache.model_variant_id` — nullable UUID,
the stored `vehicle_model_variant.id` (same context) the answer named. No
foreign key, like `vehicle_configuration.catalogue_variant_id`: a variant the
catalogue no longer holds reads as none and the lookup continues at the
Typenschein. No index: the table is read by exact plate or Stammnummer only.

**Data.** None. Rows written before this migration hold no variant and keep
behaving as before until they are refetched or expire (30 days); nothing
needs a backfill, and the provider is never asked again just to fill one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6f3e6383c4b6"
down_revision: str | Sequence[str] | None = "8e2b6c4f1a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vehicle_plate_lookup_cache",
        sa.Column("model_variant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vehicle_plate_lookup_cache", "model_variant_id")
