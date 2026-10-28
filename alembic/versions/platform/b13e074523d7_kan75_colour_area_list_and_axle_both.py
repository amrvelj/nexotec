"""KAN-75 — the canonical values FarbArt and AchsenCode resolve to

Anto's ruling (2026-10-12, option A, with his answers of 2026-10-23): the
SOAP adapter stops decoding `FarbArt`, `AchsenCode` and `PneuTyp` inline and
returns the raw codes, which the catalogue sync resolves through the provider
code map like every other coded field. Each needs a canonical list:

  * ``colour_area`` (new) — where a colour sits: ``exterior`` (Aussenfarbe,
    FarbArt 1) or ``interior`` (Polsterfarbe, FarbArt 2). Not ``colour_type``:
    that list already exists (``6ba0a99ed5c4``) and means the paint finish
    (solid / metallic / pearl / matte).
  * ``axle_position`` gains ``both`` (AchsenCode 1, Vorne + Hinten); it held
    only ``front`` and ``rear``.
  * Tyre season resolves into the existing ``tyre_type`` (summer / winter /
    all-season) — nothing to add.

Same shape and branch as ``a3d9c1e58f27``: ``reference_list`` /
``reference_value`` is platform-owned. Insert-if-absent, so a value an admin
already added by hand is left as it is. The vehicle branch's ``b0a125a0a583``
seeds the code map onto these values (it reads none of them, so no `depends_on`).

Revision ID: b13e074523d7
Revises: bda9fef79e94
Create Date: 2026-10-28 05:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "b13e074523d7"
down_revision: str | Sequence[str] | None = "bda9fef79e94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (list_code, [(value_code, label_de, label_fr, label_it, label_en), ...])
NEW_VALUES: list[tuple[str, list[tuple[str, str, str, str, str]]]] = [
    (
        "colour_area",
        [
            ("exterior", "Aussenfarbe", "Couleur extérieure", "Colore esterno", "Exterior colour"),
            ("interior", "Innenfarbe", "Couleur intérieure", "Colore interno", "Interior colour"),
        ],
    ),
    ("axle_position", [("both", "Vorne + hinten", "Avant + arrière", "Anteriore + posteriore", "Front + rear")]),
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
        op.execute("-- KAN-75 reference values skipped: insert-if-absent reads rows and needs a live database")
        return
    bind = op.get_bind()
    reference_list, reference_value = _tables()
    now = sa.func.now()
    for list_code, values in NEW_VALUES:
        list_id = bind.execute(
            sa.select(reference_list.c.id).where(reference_list.c.list_code == list_code)
        ).scalar_one_or_none()
        if list_id is None:
            list_id = uuid7()
            bind.execute(
                reference_list.insert().values(id=list_id, list_code=list_code, created_at=now, updated_at=now)
            )
        existing = set(
            bind.execute(sa.select(reference_value.c.value_code).where(reference_value.c.list_id == list_id)).scalars()
        )
        next_order = bind.execute(
            sa.select(sa.func.coalesce(sa.func.max(reference_value.c.sort_order) + 1, 0)).where(
                reference_value.c.list_id == list_id
            )
        ).scalar_one()
        for value_code, label_de, label_fr, label_it, label_en in values:
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
            "DELETE FROM reference_value WHERE value_code = 'both' AND list_id IN "
            "(SELECT id FROM reference_list WHERE list_code = 'axle_position')"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM reference_value WHERE list_id IN (SELECT id FROM reference_list WHERE list_code = 'colour_area')"
        )
    )
    bind.execute(sa.text("DELETE FROM reference_list WHERE list_code = 'colour_area'"))
