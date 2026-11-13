"""KAN-540: Sales' own offer counter is retired

Since KAN-207 (ADR-109) an offer's number comes from the Betrieb's `offer`
number range in Platform. During the switch Sales kept its own `offer` row in
`sales_number_sequence` locked and in step, as a floor under the range; it
also numbered the legacy transaction import, deleted by KAN-79. Anto,
2026-11-13: «we have currently no productive data, therefore it can be
dropped.» Nothing reads or writes the `offer` series any more, so its rows
go. The table stays: it still holds the `contract` series, which numbers
contracts until they switch to their range.

**Schema.** None.

**Data.** Every `series = 'offer'` row is deleted. A plain DELETE, which an
offline `--sql` render emits as it is (it reads no rows).

**Downgrade.** Deliberately a no-op: the deleted counters were test data,
and the code before this revision recreates a missing `offer` row at 1 on
the next offer and then numbers from the range alone, so no number repeats.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "976ea5f43dc0"
down_revision: str | Sequence[str] | None = "f021b8cc6df6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM sales_number_sequence WHERE series = 'offer'")


def downgrade() -> None:
    # A no-op on purpose (see the module docstring): nothing to restore.
    pass
