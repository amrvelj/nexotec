"""KAN-466: contact groups — the `contact_group` reference list and the
per-Betrieb switch `dealership_contact_group`

Anto, 2026-10-26: contact groups are a Nexotec-defined list in Dealer
Administration; each Betrieb switches on the ones it uses. The initial set is
his ruling, exactly: Privat, Gewerbe, Flotte, Händler, Importeur,
Versicherung, Leasing, Behörde / öffentliche Hand. Platform staff maintain the
list on the Reference data screen like every reference list.

The seed is insert-if-absent (the 4c8e1f2a9d57 shape), so a value an admin
already added by hand is left as it is; it is skipped offline because it reads
rows. `tests/test_contact_groups.py` holds these literals equal to
`app.platform.services.contact_group.SEEDED_CONTACT_GROUPS` (ADR-133).

`dealership_contact_group` holds one row per group a Betrieb has switched on;
none is on for any Betrieb after this migration.

Revision ID: d72281c678c0
Revises: a7d2c5e9f041
Create Date: 2026-11-13 23:47:50.082399

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import app.core.types
from alembic import context, op
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "d72281c678c0"
down_revision: str | Sequence[str] | None = "a7d2c5e9f041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIST_CODE = "contact_group"

# (value_code, label_de, label_fr, label_it, label_en)
VALUES: list[tuple[str, str, str, str, str]] = [
    ("private", "Privat", "Privé", "Privato", "Private"),
    ("commercial", "Gewerbe", "Professionnel", "Commerciale", "Commercial"),
    ("fleet", "Flotte", "Flotte", "Flotta", "Fleet"),
    ("dealer", "Händler", "Concessionnaire", "Concessionario", "Dealer"),
    ("importer", "Importeur", "Importateur", "Importatore", "Importer"),
    ("insurer", "Versicherung", "Assurance", "Assicurazione", "Insurer"),
    ("leasing", "Leasing", "Leasing", "Leasing", "Leasing"),
    (
        "public_authority",
        "Behörde / öffentliche Hand",
        "Autorité / secteur public",
        "Autorità / ente pubblico",
        "Public authority",
    ),
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


def _seed_values() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-466 contact_group values skipped: insert-if-absent reads rows and needs a live database")
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


def upgrade() -> None:
    op.create_table(
        "dealership_contact_group",
        sa.Column("dealership_id", app.core.types.GUID(), nullable=False),
        sa.Column("value_code", sa.String(length=64), nullable=False),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", app.core.types.GUID(), nullable=True),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.ForeignKeyConstraint(["dealership_id"], ["dealership.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dealership_id", "value_code", name="uq_dealership_contact_group_dealership_id_value_code"),
    )
    op.create_index(
        op.f("ix_dealership_contact_group_dealership_id"), "dealership_contact_group", ["dealership_id"], unique=False
    )
    _seed_values()


def downgrade() -> None:
    op.drop_index(op.f("ix_dealership_contact_group_dealership_id"), table_name="dealership_contact_group")
    op.drop_table("dealership_contact_group")
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM reference_value WHERE list_id IN "
            f"(SELECT id FROM reference_list WHERE list_code = '{LIST_CODE}')"
        )
    )
    bind.execute(sa.text(f"DELETE FROM reference_list WHERE list_code = '{LIST_CODE}'"))
