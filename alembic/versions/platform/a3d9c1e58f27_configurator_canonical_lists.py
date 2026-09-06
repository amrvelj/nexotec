"""Configurator C-A (KAN-39) — three new canonical reference lists.

PRD-Configurator §"The canonical code map" adds three lists to the v1 set,
all **ours** (not a provider's), all DE/FR/IT/EN, all authored and
translated through the FR-V-11 admin screen that shipped in PR #59 (no
deploy needed — ADR-044):

  * ``engine_cycle`` — ``Antrieb`` CodeGrpNr **112** is 2-Takt / 4-Takt /
    Kein Takt, a *stroke count*, motorcycles only. It is **not** a
    drivetrain (PRD risk R-C-5); it needs its own list so "2-Takt" never
    lands in a drive-type filter.
  * ``valuation_classification`` — ``Einstufung`` (CodeGrpNr 041). Tells
    the advisor a valuation is impossible *before* they request one
    (Q-C-4).
  * ``option_relation_type`` — ``Aktion`` (CodeGrpNr 047), plus
    ``excludes`` (``OptionenAusschluss``) and ``contains``
    (``OptionenPack``). Stored and shown, never enforced (ADR-072).

Same shape and same branch as ``6ba0a99ed5c4`` — ``reference_list`` /
``reference_value`` is platform-owned, and its ``label_en`` column is only
guaranteed present on the platform branch's own chain.

The German source strings (from the auto-i-dat Webservice specification,
pages 32–34) are authoritative; the FR/IT/EN labels are ours and editable
in the admin screen if a translation needs refining.

Revision ID: a3d9c1e58f27
Revises: 46c6382bdbf5
Create Date: 2026-09-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "a3d9c1e58f27"
down_revision: Union[str, Sequence[str], None] = "46c6382bdbf5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (list_code, [(value_code, label_de, label_fr, label_it, label_en), ...])
NEW_LISTS: list[tuple[str, list[tuple[str, str, str, str, str]]]] = [
    (
        "engine_cycle",
        [
            ("two_stroke", "2-Takt", "2 temps", "2 tempi", "Two-stroke"),
            ("four_stroke", "4-Takt", "4 temps", "4 tempi", "Four-stroke"),
            ("no_stroke", "Kein Takt", "Sans temps", "Nessun tempo", "None"),
        ],
    ),
    (
        "valuation_classification",
        [
            ("definitive", "Definitiv", "Définitif", "Definitivo", "Definitive"),
            ("none", "Keine Einstufung", "Aucun classement", "Nessuna classificazione", "No classification"),
            ("provisional", "Provisorisch", "Provisoire", "Provvisorio", "Provisional"),
            (
                "not_yet",
                "Noch keine Einstufung",
                "Pas encore de classement",
                "Classificazione non ancora disponibile",
                "Not yet classified",
            ),
        ],
    ),
    (
        "option_relation_type",
        [
            ("not_with", "Nicht mit", "Pas avec", "Non con", "Not with"),
            ("only_with", "Nur mit", "Uniquement avec", "Solo con", "Only with"),
            (
                "price_in_combination_with",
                "Preis in Kombination mit",
                "Prix en combinaison avec",
                "Prezzo in combinazione con",
                "Price in combination with",
            ),
            (
                "becomes_standard_with",
                "Wechsel auf inklusiv",
                "Devient inclus",
                "Diventa incluso",
                "Becomes standard with",
            ),
            (
                "not_in_combination_with",
                "Nicht in Kombination mit",
                "Pas en combinaison avec",
                "Non in combinazione con",
                "Not in combination with",
            ),
            (
                "only_in_combination_with",
                "Nur in Kombination mit",
                "Uniquement en combinaison avec",
                "Solo in combinazione con",
                "Only in combination with",
            ),
            ("excludes", "Schliesst aus", "Exclut", "Esclude", "Excludes"),
            ("contains", "Enthält", "Contient", "Contiene", "Contains"),
        ],
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
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

    now = sa.func.now()
    for list_code, values in NEW_LISTS:
        list_id = uuid7()
        bind.execute(
            reference_list.insert().values(id=list_id, list_code=list_code, created_at=now, updated_at=now)
        )
        for sort_order, (value_code, label_de, label_fr, label_it, label_en) in enumerate(values):
            bind.execute(
                reference_value.insert().values(
                    id=uuid7(),
                    list_id=list_id,
                    value_code=value_code,
                    label_de=label_de,
                    label_fr=label_fr,
                    label_it=label_it,
                    label_en=label_en,
                    sort_order=sort_order,
                    active=True,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    list_codes = tuple(list_code for list_code, _ in NEW_LISTS)
    bind.execute(
        sa.text(
            "DELETE FROM reference_value WHERE list_id IN "
            "(SELECT id FROM reference_list WHERE list_code IN :codes)"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list_codes},
    )
    bind.execute(
        sa.text("DELETE FROM reference_list WHERE list_code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": list_codes},
    )
