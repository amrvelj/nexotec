"""KAN-587: `vehicle_class` is the size class; the type-approval class gets its own list

Anto's ruling (2026-11-10, option A): «Fahrzeugklasse» is the **size class**
a salesperson sells by — the one auto-i-dat sends for every identified car
(`FzKlasse`, the identAPI's `category` 1–6). The list `vehicle_class` was
seeded by `6ba0a99ed5c4` with the **type-approval classes** on the
Fahrzeugausweis (M1, N1, L3e, L1e): two different facts sharing one list, so
every identAPI category became a mapping gap with no right answer.

Data only — no schema change. Three steps:

1. **`homologation_class` (new list) takes the four type-approval classes.**
   The rows `m1`, `n1`, `l3e`, `l1e` are *moved*, not re-seeded: the same
   `reference_value` rows change their list, so a label platform staff edited
   and a value they switched off stay as they left them. (Where the new list
   already holds the code — a re-run after a hand repair — the list's own row
   wins and the `vehicle_class` one goes.)
2. **`vehicle_class` gets the six size classes** `micro`, `city`, `compact`,
   `large`, `executive`, `luxury` — the identAPI's own six categories, in its
   order — insert-if-absent, so a value an admin already added by hand under
   one of these codes is left as it is.
3. **Any other value an admin added to `vehicle_class` stays where it is** and
   is named in the migration's output: which of the two facts it meant is not
   this migration's to guess.

The vehicle chain's `8e2b6c4f1a97` moves the values variants and
configurations already store, and seeds the code map onto the six size
classes. It reads none of these rows, so no `depends_on`: the two chains may
run in either order.

Skipped offline, like `b13e074523d7`: every step reads rows.

**Downgrade** puts the four type-approval classes back into `vehicle_class`,
removes the six size classes and the `homologation_class` list. It works on
what this migration wrote; a value an admin added to `homologation_class`
since goes with its list.

Revision ID: 5d1f8a3c7e42
Revises: 4c8e1f2a9d57
Create Date: 2026-11-11 05:00:00.000000

"""

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from alembic import context, op
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "5d1f8a3c7e42"
down_revision: str | Sequence[str] | None = "4c8e1f2a9d57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIZE_LIST = "vehicle_class"
HOMOLOGATION_LIST = "homologation_class"

# The type-approval classes `6ba0a99ed5c4` seeded into `vehicle_class`.
HOMOLOGATION_CODES: tuple[str, ...] = ("m1", "n1", "l3e", "l1e")

# (value_code, label_de, label_fr, label_it, label_en) — the identAPI's
# `category` 1–6, in its order (Micro, City, Compact, Large, Executive,
# Luxury). The German wording is the one Anto ruled on (KAN-587, 2026-11-10).
SIZE_VALUES: list[tuple[str, str, str, str, str]] = [
    ("micro", "Kleinstwagen", "Micro-citadine", "Citycar", "Micro car"),
    ("city", "Kleinwagen", "Citadine", "Utilitaria", "Small car"),
    ("compact", "Kompaktklasse", "Compacte", "Compatta", "Compact"),
    ("large", "Mittelklasse", "Classe moyenne", "Classe media", "Mid-size"),
    ("executive", "Obere Mittelklasse", "Classe moyenne supérieure", "Classe medio-superiore", "Upper mid-size"),
    ("luxury", "Luxusklasse", "Classe de luxe", "Classe di lusso", "Luxury"),
]
SIZE_CODES: tuple[str, ...] = tuple(value[0] for value in SIZE_VALUES)


class Result(NamedTuple):
    moved: int  # type-approval rows that changed their list
    duplicates_removed: int  # ... that the new list already held
    inserted: int  # size classes added
    left_in_size_list: list[str]  # other values an admin added to `vehicle_class`


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    reference_list = sa.table(
        "reference_list",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    reference_value = sa.table(
        "reference_value",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_id", postgresql.UUID(as_uuid=True)),
        sa.column("value_code", sa.String()),
        sa.column("label_de", sa.String()),
        sa.column("label_fr", sa.String()),
        sa.column("label_it", sa.String()),
        sa.column("label_en", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("active", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    return reference_list, reference_value


def _list_id(conn: Connection, list_code: str, *, create: bool):
    reference_list, _ = _tables()
    list_id = conn.execute(
        sa.select(reference_list.c.id).where(reference_list.c.list_code == list_code)
    ).scalar_one_or_none()
    if list_id is None and create:
        list_id = uuid7()
        now = sa.func.now()
        conn.execute(reference_list.insert().values(id=list_id, list_code=list_code, created_at=now, updated_at=now))
    return list_id


def _codes(conn: Connection, list_id) -> set[str]:
    _, reference_value = _tables()
    return set(conn.execute(sa.select(reference_value.c.value_code).where(reference_value.c.list_id == list_id)).scalars())


def _move(conn: Connection, *, codes: tuple[str, ...], source_id, target_id) -> tuple[int, int]:
    """Moves the rows `codes` of one list into another, keeping each row (id,
    labels, `active`, order); its version moves once. A code the target
    already holds keeps the target's row, and the source's goes. Returns
    (moved, duplicates removed)."""

    _, reference_value = _tables()
    held = _codes(conn, target_id)
    moved = removed = 0
    for code in codes:
        row = sa.and_(reference_value.c.list_id == source_id, reference_value.c.value_code == code)
        if code in held:
            removed += conn.execute(reference_value.delete().where(row)).rowcount
            continue
        moved += conn.execute(
            reference_value.update()
            .where(row)
            .values(list_id=target_id, version=reference_value.c.version + 1, updated_at=sa.func.now())
        ).rowcount
    return moved, removed


def apply(conn: Connection) -> Result:
    """The upgrade's three steps. Takes a connection so the tests can run it
    against the create_all schema without Alembic."""

    _, reference_value = _tables()
    size_id = _list_id(conn, SIZE_LIST, create=True)
    homologation_id = _list_id(conn, HOMOLOGATION_LIST, create=True)

    moved, duplicates_removed = _move(conn, codes=HOMOLOGATION_CODES, source_id=size_id, target_id=homologation_id)

    existing = _codes(conn, size_id)
    left = sorted(existing - set(SIZE_CODES))
    next_order = conn.execute(
        sa.select(sa.func.coalesce(sa.func.max(reference_value.c.sort_order) + 1, 0)).where(
            reference_value.c.list_id == size_id
        )
    ).scalar_one()
    inserted = 0
    now = sa.func.now()
    for value_code, label_de, label_fr, label_it, label_en in SIZE_VALUES:
        if value_code in existing:
            continue
        conn.execute(
            reference_value.insert().values(
                id=uuid7(), list_id=size_id, value_code=value_code,
                label_de=label_de, label_fr=label_fr, label_it=label_it, label_en=label_en,
                sort_order=next_order, active=True, version=1, created_at=now, updated_at=now,
            )
        )
        next_order += 1
        inserted += 1
    return Result(moved, duplicates_removed, inserted, left)


def revert(conn: Connection) -> None:
    reference_list, reference_value = _tables()
    size_id = _list_id(conn, SIZE_LIST, create=True)
    homologation_id = _list_id(conn, HOMOLOGATION_LIST, create=False)
    conn.execute(
        reference_value.delete().where(
            reference_value.c.list_id == size_id, reference_value.c.value_code.in_(SIZE_CODES)
        )
    )
    if homologation_id is None:
        return
    _move(conn, codes=HOMOLOGATION_CODES, source_id=homologation_id, target_id=size_id)
    conn.execute(reference_value.delete().where(reference_value.c.list_id == homologation_id))
    conn.execute(reference_list.delete().where(reference_list.c.id == homologation_id))


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-587 reference lists skipped: moving and insert-if-absent read rows and need a live database")
        return
    result = apply(op.get_bind())
    print(
        f"KAN-587: moved {result.moved} type-approval classes to '{HOMOLOGATION_LIST}' "
        f"({result.duplicates_removed} already there), added {result.inserted} size classes to '{SIZE_LIST}'."
    )
    if result.left_in_size_list:
        print(
            f"KAN-587: NOT moved — values an admin added to '{SIZE_LIST}', left there for review: "
            f"{', '.join(result.left_in_size_list)}."
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-587 reference lists downgrade skipped: it reads rows and needs a live database")
        return
    revert(op.get_bind())
