"""Backfill Customer.address_canton from postal code (D-13)

address_canton has existed on the customer table since Phase A but was
never populated — every write forced it to NULL (see the D-13 discussion in
app/services/customer.py). This migration only fixes the *data*; the write
path itself was already switched over to derive_canton() in the same PR.

Revision ID: a6c2f8de41b7
Revises: f3a8c1d5e9b2
Create Date: 2026-08-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.postal_codes import POSTAL_CODE_CANTON

revision: str = 'a6c2f8de41b7'
down_revision: Union[str, Sequence[str], None] = 'f3a8c1d5e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Data-only, no schema change. Touches only rows that are
    unambiguously resolvable — a Swiss address, a postal code the table
    covers, and no canton already set (never overwrites a value that got
    there some other way) — and leaves everything else untouched rather
    than guessing.

    Imports the frozen postal-code-to-canton table from
    app.core.postal_codes instead of duplicating ~3,400 entries inline.
    That module is a versioned, dated snapshot documented as such in its
    own docstring, not business logic that could silently change behaviour
    on a future re-run — same category as this codebase's other migrations
    importing app.core.uuid7 for seed data, not the kind of drift risk that
    made the Phase B migration freeze a local copy of normalise_phone.
    """

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, address_postal_code FROM customer"
            " WHERE address_country = 'CH' AND address_postal_code IS NOT NULL AND address_canton IS NULL"
        )
    ).fetchall()

    for row in rows:
        canton = POSTAL_CODE_CANTON.get(row.address_postal_code)
        if canton is not None:
            conn.execute(
                sa.text("UPDATE customer SET address_canton = :canton WHERE id = :id"),
                {"canton": canton, "id": row.id},
            )


def downgrade() -> None:
    """No-op. There is no record of which rows this migration touched
    versus a canton value written some other way afterward, so clearing
    every Swiss customer's canton on downgrade would destroy more than it
    restores. D-13 is additive and idempotent — re-running upgrade after a
    downgrade lands in the same state, which is the property that matters.
    """
