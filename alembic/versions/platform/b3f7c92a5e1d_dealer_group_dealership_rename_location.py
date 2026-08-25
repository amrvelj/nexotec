"""dealer_group, Dealer -> Dealership rename, location (WP-3 PR-1, ADR-014)

Three-level organisation model: dealer_group -> dealership -> location.
The dealership stays the tenant — tenant_id keeps pointing at it everywhere
else in the schema, unchanged. Every existing dealership becomes a group of
one, since that's the only lossless, mechanical migration available before a
real multi-dealership group ever signs (the whole reason WP-3 has a hard
external deadline).

Prints one MIGRATION_DRY_RUN_REPORT line per dealership before creating its
group, so the exact row-level mapping is visible in the migration's own
output before the transaction (Alembic wraps each migration in one) commits
— grep for that marker to review it. Verifies row counts afterward and
raises (rolling back the transaction) on any mismatch, same as this repo's
established migration-safety idiom.

location is created empty here — owned by platform (aftersales needs the
workshop, platform needs where a user works, finance needs the site on
document footers), carrying a calendar_ref column from day one so Aftersales
doesn't have to retrofit it onto a year of real location data later.

Revision ID: b3f7c92a5e1d
Revises: a7c4e91f6b2d
Create Date: 2026-08-25 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3f7c92a5e1d'
down_revision: Union[str, Sequence[str], None] = 'a7c4e91f6b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.rename_table("dealer", "dealership")

    op.create_table(
        "dealer_group",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        # ADR-030 (4): dealer_group carries a single point of contact for
        # data subjects. Optional — every group backfilled here never
        # supplied one.
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("contact_email", sa.String(length=254), nullable=True),
        sa.Column("contact_phone", sa.String(length=20), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.add_column("dealership", sa.Column("dealer_group_id", postgresql.UUID(as_uuid=True), nullable=True))

    dealership = sa.table(
        "dealership",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("legal_name", sa.String),
        sa.column("dealer_group_id", postgresql.UUID(as_uuid=True)),
    )
    dealer_group = sa.table(
        "dealer_group",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    dealership_rows = bind.execute(sa.select(dealership.c.id, dealership.c.legal_name)).all()
    print(f"MIGRATION_DRY_RUN_REPORT: {len(dealership_rows)} dealership row(s) found — each becomes a group of one.")

    for row in dealership_rows:
        group_id = uuid.uuid4()
        bind.execute(
            dealer_group.insert().values(
                id=group_id, name=row.legal_name, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        bind.execute(dealership.update().where(dealership.c.id == row.id).values(dealer_group_id=group_id))
        print(f"MIGRATION_DRY_RUN_REPORT: dealership {row.id} ({row.legal_name!r}) -> new dealer_group {group_id}")

    dealership_count = bind.execute(sa.select(sa.func.count()).select_from(dealership)).scalar()
    dealer_group_count = bind.execute(sa.select(sa.func.count()).select_from(dealer_group)).scalar()
    if dealership_count != dealer_group_count:
        raise RuntimeError(
            "WP-3 PR-1 backfill row-count mismatch: "
            f"{dealership_count} dealership row(s) but {dealer_group_count} dealer_group row(s) — "
            "expected exactly one group per dealership. Rolling back."
        )
    unassigned = bind.execute(
        sa.select(sa.func.count()).select_from(dealership).where(dealership.c.dealer_group_id.is_(None))
    ).scalar()
    if unassigned:
        raise RuntimeError(
            f"WP-3 PR-1 backfill left {unassigned} dealership row(s) with no dealer_group_id. Rolling back."
        )
    print(
        f"MIGRATION_DRY_RUN_REPORT: verified — {dealership_count} dealership row(s), "
        f"{dealer_group_count} dealer_group row(s), 0 unassigned."
    )

    op.alter_column("dealership", "dealer_group_id", nullable=False)
    op.create_index("ix_dealership_dealer_group_id", "dealership", ["dealer_group_id"])
    op.create_foreign_key(
        "fk_dealership_dealer_group_id_dealer_group",
        "dealership",
        "dealer_group",
        ["dealer_group_id"],
        ["id"],
    )
    op.drop_column("dealership", "parent_group_id")

    op.create_table(
        "location",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address_street", sa.String(length=200), nullable=True),
        sa.Column("address_house_number", sa.String(length=20), nullable=True),
        sa.Column("address_postal_code", sa.String(length=12), nullable=True),
        sa.Column("address_locality", sa.String(length=100), nullable=True),
        sa.Column("address_canton", sa.String(length=2), nullable=True),
        sa.Column("address_country", sa.String(length=2), nullable=True),
        sa.Column("calendar_ref", sa.String(length=100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_location_tenant_id", "location", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_location_tenant_id", table_name="location")
    op.drop_table("location")

    op.add_column("dealership", sa.Column("parent_group_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.drop_constraint("fk_dealership_dealer_group_id_dealer_group", "dealership", type_="foreignkey")
    op.drop_index("ix_dealership_dealer_group_id", table_name="dealership")
    op.drop_column("dealership", "dealer_group_id")

    op.drop_table("dealer_group")

    op.rename_table("dealership", "dealer")
