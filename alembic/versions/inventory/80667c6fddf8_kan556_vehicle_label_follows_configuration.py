"""A configured stock car is named by its configuration (KAN-556, ADR-117 (2))

`stock_item.vehicle_label` — the stock list's «Fahrzeug» and the detail
title — was the client's free text, set once at the intake and never
refreshed; `configuration_label` (rule 2's copy of the configuration's
label) follows the configuration since KAN-534. ADR-117 (2): on an item with
a configuration the name *is* that label. From this revision on the server
writes it (create, «Konfiguration zuordnen», the nightly
`inventory.configuration_labels` refresh); this data step brings the rows
written before into line.

Every item with a configuration and a label copy takes that copy as its
name. It reads Stock's own column only — never the vehicle context's table
(rule 2; a migration never touches another context's tables): a copy that is
stale is corrected by the next nightly refresh, which now sets both. An item
without a configuration (a legacy or hand-typed one) keeps its text; one
with a configuration but no copy yet keeps its text until that refresh.
`updated_at` moves on a changed row (what the API returns changed, as the
refresh does); `version` does not (a copy no client writes).

One UPDATE, no rows read, so it renders under `--sql` too (KAN-174). The
downgrade is deliberately a no-op: the free text each row held before is
not kept anywhere, and the old code reads the derived name as any text.

Revision ID: 80667c6fddf8
Revises: f143ae1513b6
Create Date: 2026-11-04 11:05:57.378070

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '80667c6fddf8'
down_revision: Union[str, Sequence[str], None] = 'f143ae1513b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE stock_item
           SET vehicle_label = configuration_label,
               updated_at = now()
         WHERE configuration_id IS NOT NULL
           AND configuration_label IS NOT NULL
           AND vehicle_label IS DISTINCT FROM configuration_label
        """
    )


def downgrade() -> None:
    # Deliberately a no-op: the free text the rows held before is not kept
    # (see the module docstring).
    pass
