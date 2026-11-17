"""KAN-643: ck_vehicle_party_period — a party period never ends before it starts

`vehicle_party` had no database rule against an inverted period
(`effective_to < effective_from`). KAN-583 fixed the two code paths that wrote
one; this CHECK makes such a row impossible on every path (ADR-064: an
inverted row is a period that never existed; rule 10). A merge copy closed at
its own start (`effective_to = effective_from`, KAN-177) is allowed.

Staging held 0 inverted rows of 3 (read-only count, 2026-11-15, on KAN-643);
there is no production. The upgrade still counts first and stops with a
message naming the rows' count rather than letting `ADD CONSTRAINT` fail
opaquely: correcting such a row rewrites history, which is Anto's decision
(KAN-643's exit criterion), never this migration's.

Revision ID: c7e2a9f4b1d3
Revises: a4c1e6b9d2f7
Create Date: 2026-11-17 08:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "c7e2a9f4b1d3"
down_revision: str | Sequence[str] | None = "a4c1e6b9d2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_vehicle_party_period"
_CONDITION = "effective_to IS NULL OR effective_to >= effective_from"


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-643 inverted-period count skipped: it reads rows and needs a live database")
    else:
        inverted = op.get_bind().execute(
            sa.text("SELECT count(*) FROM vehicle_party WHERE effective_to < effective_from")
        ).scalar_one()
        if inverted:
            raise RuntimeError(
                f"KAN-643: vehicle_party holds {inverted} row(s) whose effective_to is before effective_from. "
                "Correcting them rewrites a party's history - a decision for Anto (KAN-643), not for this "
                "migration. No constraint was added."
            )
    op.create_check_constraint(_CHECK, "vehicle_party", _CONDITION)


def downgrade() -> None:
    op.drop_constraint(_CHECK, "vehicle_party", type_="check")
