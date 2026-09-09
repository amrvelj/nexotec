"""Backfill: re-elect a usable primary per contact type-group (KAN-46)

ADR-067 / PRD-Customers FR-07 require exactly one primary per
(customer, channel type) group, and FR-23 §2 requires that primary to be a
USABLE row (not closed, not do_not_use). Until KAN-46 the write path never
maintained that when the primary was closed, flagged do_not_use or deleted:
the group could be left with a dead row still flagged primary, or with no
primary at all, and the six read-model projections — which require
is_primary strictly — went null while a working second row sat on the
record.

The code fix is in app/customer/services/customer.py (re-election on the
write path plus an oldest-usable fallback in the projection). This
migration repairs the rows that history already left in the broken state,
by the same rule _fixup_single_primary now applies:

  * a closed or do_not_use row is never a valid primary -> demote it;
  * among the usable rows of the group, keep exactly one primary: the
    oldest by created_at when none or several are flagged;
  * a group with no usable row keeps no primary (the projection is null,
    and that is correct — forcing a dead row back to primary would put it
    on documents).

Data-only, no schema change. Counts are printed, split by table and by
fault, for the PR record.

Revision ID: a1c46e02b7f9
Revises: 8ef4267e5c5e
Create Date: 2026-09-09

"""
from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c46e02b7f9"
down_revision: Union[str, Sequence[str], None] = "8ef4267e5c5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, type column)
_CHANNELS = [
    ("customer_phone", "phone_type"),
    ("customer_email", "email_type"),
    ("customer_address", "address_type"),
]


def _repair_table(conn, table: str, type_column: str) -> dict[str, int]:
    rows = conn.execute(
        sa.text(
            f"SELECT id, customer_id, {type_column} AS type_value, is_primary, "
            f"valid_to, do_not_use, created_at FROM {table}"
        )
    ).fetchall()

    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[(row.customer_id, row.type_value)].append(row)

    demote_ids: list = []
    promote_ids: list = []
    counts = {"demoted_dead_primary": 0, "multiple_usable_primary": 0, "zero_usable_primary": 0}

    for group in groups.values():
        group.sort(key=lambda r: (r.created_at, str(r.id)))

        dead_primaries = [r for r in group if r.is_primary and (r.valid_to is not None or r.do_not_use)]
        for r in dead_primaries:
            demote_ids.append(r.id)
        if dead_primaries:
            counts["demoted_dead_primary"] += len(dead_primaries)

        usable = [r for r in group if r.valid_to is None and not r.do_not_use]
        usable_primaries = [r for r in usable if r.is_primary]

        if len(usable_primaries) > 1:
            for r in usable_primaries[1:]:
                demote_ids.append(r.id)
            counts["multiple_usable_primary"] += 1
        elif not usable_primaries and usable:
            promote_ids.append(usable[0].id)
            counts["zero_usable_primary"] += 1

    if demote_ids:
        conn.execute(
            sa.text(f"UPDATE {table} SET is_primary = false WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": demote_ids},
        )
    if promote_ids:
        conn.execute(
            sa.text(f"UPDATE {table} SET is_primary = true WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": promote_ids},
        )
    return counts


def upgrade() -> None:
    conn = op.get_bind()
    for table, type_column in _CHANNELS:
        counts = _repair_table(conn, table, type_column)
        print(
            f"[KAN-46] {table}: "
            f"{counts['zero_usable_primary']} group(s) with no usable primary repaired, "
            f"{counts['multiple_usable_primary']} group(s) with several usable primaries collapsed, "
            f"{counts['demoted_dead_primary']} dead row(s) demoted from primary"
        )


def downgrade() -> None:
    """No-op. The pre-repair state violated the ADR-067 one-usable-primary
    invariant and there is no record of which rows were changed — there is
    nothing coherent to restore to.
    """
