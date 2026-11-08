"""KAN-211: the `interior_colour_type` reference list

The advisor names a configuration's upholstery type by hand (Anto,
2026-11-08: «we ask the user to select manually the colour type, as well as
for interior»); the paint finish already has its list (`colour_type`,
6ba0a99ed5c4). This list holds the upholstery materials a Swiss dealer
names: Stoff, Leder, Teilleder, Alcantara, Kunstleder, Sonstiges. An admin
edits it in Dealer Administration like every reference list (A-RULE-0).

Same shape as b13e074523d7: insert-if-absent, so a value an admin already
added by hand is left as it is; skipped offline because it reads rows.

Revision ID: 4c8e1f2a9d57
Revises: 7d3e9b2c4a18
Create Date: 2026-11-08 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "4c8e1f2a9d57"
down_revision: str | Sequence[str] | None = "7d3e9b2c4a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIST_CODE = "interior_colour_type"

# (value_code, label_de, label_fr, label_it, label_en)
VALUES: list[tuple[str, str, str, str, str]] = [
    ("fabric", "Stoff", "Tissu", "Tessuto", "Fabric"),
    ("leather", "Leder", "Cuir", "Pelle", "Leather"),
    ("part_leather", "Teilleder", "Cuir partiel", "Pelle parziale", "Part leather"),
    ("alcantara", "Alcantara", "Alcantara", "Alcantara", "Alcantara"),
    ("synthetic_leather", "Kunstleder", "Similicuir", "Similpelle", "Synthetic leather"),
    ("other", "Sonstiges", "Autre", "Altro", "Other"),
]


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


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-211 reference values skipped: insert-if-absent reads rows and needs a live database")
        return
    bind = op.get_bind()
    reference_list, reference_value = _tables()
    now = sa.func.now()
    list_id = bind.execute(
        sa.select(reference_list.c.id).where(reference_list.c.list_code == LIST_CODE)
    ).scalar_one_or_none()
    if list_id is None:
        list_id = uuid7()
        bind.execute(reference_list.insert().values(id=list_id, list_code=LIST_CODE, created_at=now, updated_at=now))
    existing = set(
        bind.execute(sa.select(reference_value.c.value_code).where(reference_value.c.list_id == list_id)).scalars()
    )
    next_order = bind.execute(
        sa.select(sa.func.coalesce(sa.func.max(reference_value.c.sort_order) + 1, 0)).where(
            reference_value.c.list_id == list_id
        )
    ).scalar_one()
    for value_code, label_de, label_fr, label_it, label_en in VALUES:
        if value_code in existing:
            continue
        bind.execute(
            reference_value.insert().values(
                id=uuid7(),
                list_id=list_id,
                value_code=value_code,
                label_de=label_de,
                label_fr=label_fr,
                label_it=label_it,
                label_en=label_en,
                sort_order=next_order,
                active=True,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        next_order += 1


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM reference_value WHERE list_id IN "
            f"(SELECT id FROM reference_list WHERE list_code = '{LIST_CODE}')"
        )
    )
    bind.execute(sa.text(f"DELETE FROM reference_list WHERE list_code = '{LIST_CODE}'"))
