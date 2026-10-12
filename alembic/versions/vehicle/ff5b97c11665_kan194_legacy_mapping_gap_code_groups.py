"""KAN-194 — open mapping gaps keyed by a numeric CodeGrpNr get their semantic code group

Data only — no schema change. `vehicle_mapping_gap.code_group` is the
semantic list code (`fuel_type`, `transmission`, …), never a numeric
auto-i-dat `CodeGrpNr` (see `ProviderCodeMap`'s docstring). Gaps recorded
before the semantic keys may carry one (`011`); `resolve_mapping_gap` refuses
them with a 409 (KAN-77, deliberately), so they could never be resolved.

**What this does.** Every OPEN gap whose code group is all digits is
rewritten to the semantic code group its CodeGrpNr stands for, using the
CodeGrpNr → list table frozen below (`LEGACY_CODE_GROUPS`, the same pairs as
`GROUPS` in `7c4e9a2b6d13`; `tests/test_kan194_legacy_mapping_gap_code_groups.py`
checks they agree). The lookup is by CodeGrpNr alone: a CodeGrpNr names one
vocabulary, whatever kind it was recorded for.

**Merges, never drops.** When the rewritten key (provider, vehicle_kind,
code_group, provider_code) already has a gap — open or resolved — or two
legacy gaps rewrite to the same key, they become one row: the existing
semantic gap if there is one (its resolution kept), otherwise the legacy row
seen first. The kept row gets the summed `occurrences`, the earliest
`first_seen_at` and the latest `last_seen_at`; the others are deleted. The
code itself is never lost — every merged row carried the same provider code.

**Left alone.** A numeric code group with no entry in the table (014, 110,
500, 550, or anything else) is not rewritten and not deleted: it is counted
and printed, and stays visible in the queue with its 409. Resolved legacy
gaps are not touched. No outbox event is published (a repair of stored
state, not a business fact), and no code map row is written: a rewritten gap
whose key the seed already maps is closed by the admin's resolve against
that row (`resolve_mapping_gap`'s existing path).

A no-op where no such rows exist; a second run changes nothing.

**Downgrade is a deliberate no-op.** The rewrite is irreversible: merged
rows are deleted and their numeric keys are gone, and restoring a key the
code refuses to resolve would only recreate the defect.

Revision ID: ff5b97c11665
Revises: 0ba433107313
Create Date: 2026-10-12 00:00:00.000000

"""

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "ff5b97c11665"
down_revision: str | Sequence[str] | None = "0ba433107313"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# CodeGrpNr -> semantic code group (== canonical list code). Frozen on purpose
# (a migration keeps meaning what it meant when it ran); the same pairs as
# `GROUPS` in 7c4e9a2b6d13. 112 is a stroke count, never a drivetrain (R-C-5).
LEGACY_CODE_GROUPS: dict[str, str] = {
    "010": "body_style",
    "020": "body_style",
    "011": "fuel_type",
    "021": "fuel_type",
    "111": "fuel_type",
    "012": "drivetrain",
    "022": "drivetrain",
    "112": "engine_cycle",
    "013": "transmission",
    "023": "transmission",
    "045": "equipment_feature",
    "041": "valuation_classification",
    "047": "option_relation_type",
    "065": "consumption_norm",
    "046": "option_group",
}


class RewriteResult(NamedTuple):
    rewritten: int  # legacy rows that now carry their semantic code group
    merged: int  # legacy rows folded into another row and deleted
    # (gap id, code group) of every open numeric gap with no mapping — left as it was
    unmapped: list[tuple[str, str]]


def _table() -> sa.TableClause:
    return sa.table(
        "vehicle_mapping_gap",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("provider", sa.String()),
        sa.column("vehicle_kind", sa.String()),
        sa.column("code_group", sa.String()),
        sa.column("provider_code", sa.String()),
        sa.column("first_seen_at", sa.DateTime(timezone=True)),
        sa.column("last_seen_at", sa.DateTime(timezone=True)),
        sa.column("occurrences", sa.Integer()),
        sa.column("resolved", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _rewrite(conn: Connection) -> RewriteResult:
    """Takes a connection so the tests can run it against the create_all
    schema without Alembic."""

    table = _table()
    columns = (
        table.c.id, table.c.provider, table.c.vehicle_kind, table.c.code_group, table.c.provider_code,
        table.c.first_seen_at, table.c.last_seen_at, table.c.occurrences,
    )
    legacy = [
        row
        for row in conn.execute(
            sa.select(*columns).where(table.c.resolved.is_(False)).order_by(table.c.first_seen_at, table.c.id)
        )
        if row.code_group.isdigit()
    ]

    unmapped: list[tuple[str, str]] = []
    by_key: dict[tuple[str, str, str, str], list[sa.Row]] = {}
    for row in legacy:
        semantic = LEGACY_CODE_GROUPS.get(row.code_group)
        if semantic is None:
            unmapped.append((str(row.id), row.code_group))
            continue
        by_key.setdefault((row.provider, row.vehicle_kind, semantic, row.provider_code), []).append(row)

    rewritten = merged = 0
    now = sa.func.now()
    for (provider, vehicle_kind, semantic, provider_code), rows in by_key.items():
        existing = conn.execute(
            sa.select(*columns).where(
                table.c.provider == provider, table.c.vehicle_kind == vehicle_kind,
                table.c.code_group == semantic, table.c.provider_code == provider_code,
            )
        ).first()
        keeper, folded = (existing, rows) if existing is not None else (rows[0], rows[1:])
        everything = [keeper, *folded]
        if folded:
            # Deleted before the keeper is re-keyed, so the unique key is never
            # held twice at once.
            conn.execute(table.delete().where(table.c.id.in_([row.id for row in folded])))
            merged += len(folded)
        conn.execute(
            table.update()
            .where(table.c.id == keeper.id)
            .values(
                code_group=semantic,
                occurrences=sum(row.occurrences for row in everything),
                first_seen_at=min(row.first_seen_at for row in everything),
                last_seen_at=max(row.last_seen_at for row in everything),
                updated_at=now,
            )
        )
        if existing is None:
            rewritten += 1
    return RewriteResult(rewritten, merged, unmapped)


def upgrade() -> None:
    result = _rewrite(op.get_bind())
    print(
        f"KAN-194: {result.rewritten} legacy mapping gaps re-keyed to their semantic code group, "
        f"{result.merged} merged into an existing gap, {len(result.unmapped)} left as they were (no mapping)."
    )
    for gap_id, code_group in result.unmapped:
        print(f"KAN-194: NOT rewritten gap {gap_id}: code group '{code_group}' has no semantic mapping.")


def downgrade() -> None:
    # Deliberately a no-op (see the module docstring): merged rows are gone,
    # and a numeric key is the defect this revision removes.
    pass
