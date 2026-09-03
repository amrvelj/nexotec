"""Typenschein ↔ model variant becomes many-to-many

`vehicle_type_approval` shipped (rev 74b6c2794698) as a one-to-one model:
`type_approval_number` UNIQUE, a single `model_variant_id` FK. A Swiss
Typenschein is **not** an identifier — an importer homologates several
similar or identical variants under one number, and auto-i-dat's
`Typenscheine` Datenname returns a *list* for one FzKey. PRD-Vehicles'
identifier table always said "Not unique — many cars share one"; the
shipped schema contradicted it and made FR-C-02's Typenschein lookup
(1..n → picker) impossible.

This migration:
  * creates `vehicle_model_variant_type_approval`, the m2m link, carrying
    `first_registration_from` (a property of the (variant, Typenschein)
    pair, not of the number — see the model docstring);
  * backfills one link row per existing `vehicle_type_approval` row, so it
    is safe against a populated table;
  * drops `model_variant_id` and `first_registration_from` from
    `vehicle_type_approval`, and drops the UNIQUE constraint on
    `type_approval_number` (the non-unique btree index stays).

Revision ID: eb660a3213bd
Revises: b2e6f1a9c3d7
Create Date: 2026-09-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'eb660a3213bd'
down_revision: Union[str, Sequence[str], None] = 'b2e6f1a9c3d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicle_model_variant_type_approval",
        sa.Column(
            "model_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_model_variant.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "type_approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vehicle_type_approval.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("first_registration_from", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_vehicle_model_variant_type_approval_type_approval_id",
        "vehicle_model_variant_type_approval",
        ["type_approval_id"],
    )

    # Backfill: every existing one-to-one row becomes exactly one link,
    # carrying its first-registration date across unchanged.
    op.execute(
        """
        INSERT INTO vehicle_model_variant_type_approval
            (model_variant_id, type_approval_id, first_registration_from, created_at, updated_at)
        SELECT model_variant_id, id, first_registration_from, now(), now()
        FROM vehicle_type_approval
        """
    )

    op.drop_index("ix_vehicle_type_approval_model_variant_id", table_name="vehicle_type_approval")
    op.drop_constraint(
        "vehicle_type_approval_model_variant_id_fkey", "vehicle_type_approval", type_="foreignkey"
    )
    op.drop_column("vehicle_type_approval", "model_variant_id")
    op.drop_column("vehicle_type_approval", "first_registration_from")

    # The Typenschein number is no longer unique. Its non-unique companion
    # index (ix_vehicle_type_approval_type_approval_number, created
    # alongside the UNIQUE constraint in rev 74b6c2794698) stays and now
    # carries the lookup on its own.
    op.drop_constraint(
        "vehicle_type_approval_type_approval_number_key", "vehicle_type_approval", type_="unique"
    )


def downgrade() -> None:
    """Reverses the shape. Lossy by nature: a Typenschein that gained more
    than one variant, or a variant that gained more than one Typenschein,
    cannot be represented one-to-one again — the first link per approval is
    kept and the rest are dropped, and re-adding the UNIQUE constraint will
    fail if any Typenschein number ended up on more than one approval row.
    Only safe immediately after the upgrade, before any m2m data is added.
    """

    op.add_column(
        "vehicle_type_approval",
        sa.Column("first_registration_from", sa.Date(), nullable=True),
    )
    op.add_column(
        "vehicle_type_approval",
        sa.Column("model_variant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.execute(
        """
        UPDATE vehicle_type_approval ta
        SET model_variant_id = link.model_variant_id,
            first_registration_from = link.first_registration_from
        FROM (
            SELECT DISTINCT ON (type_approval_id)
                   type_approval_id, model_variant_id, first_registration_from
            FROM vehicle_model_variant_type_approval
            ORDER BY type_approval_id, model_variant_id
        ) AS link
        WHERE ta.id = link.type_approval_id
        """
    )
    op.execute("DELETE FROM vehicle_type_approval WHERE model_variant_id IS NULL")

    op.alter_column("vehicle_type_approval", "model_variant_id", nullable=False)
    op.create_foreign_key(
        "vehicle_type_approval_model_variant_id_fkey",
        "vehicle_type_approval",
        "vehicle_model_variant",
        ["model_variant_id"],
        ["id"],
    )
    op.create_index(
        "ix_vehicle_type_approval_model_variant_id", "vehicle_type_approval", ["model_variant_id"]
    )
    op.create_unique_constraint(
        "vehicle_type_approval_type_approval_number_key",
        "vehicle_type_approval",
        ["type_approval_number"],
    )

    op.drop_index(
        "ix_vehicle_model_variant_type_approval_type_approval_id",
        table_name="vehicle_model_variant_type_approval",
    )
    op.drop_table("vehicle_model_variant_type_approval")
