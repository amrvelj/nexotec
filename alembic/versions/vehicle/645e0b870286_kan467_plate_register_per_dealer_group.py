"""KAN-467: the Kontrollschild register is kept per dealer group

Anto, 2026-10-31 (KAN-467, option 1 — "it's on a group level"): a plate is
recorded in the register of the group of the Betrieb that recorded it, plate
search reads only that group's register, and recording a plate that is
current on another car of the group moves it once the user confirms. ADR-039
as amended.

- `vehicle_plate.group_id` (NOT NULL; the DealerGroup, three-column-pattern
  reference without a label — the register is the group's own, nothing is
  displayed from it), and the lookup index leads with it.
- `vehicle_plate.version`: ending an assignment takes If-Match.
- `vehicle_plate_conflict` is dropped: within one group two current rows of
  one plate cannot be recorded, so the cross-group data-quality queue has
  nothing left to hold (the option Anto picked names this cost).

No data step: nothing has ever written `vehicle_plate` — the write service
had no caller until this revision's release (KAN-467's finding). The upgrade
checks that and stops with a report rather than guess a group for a row.

Revision ID: 645e0b870286
Revises: e4b7c2a91d36
Create Date: 2026-11-01 14:15:33.935049

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

import app.core.types

# revision identifiers, used by Alembic.
revision: str = "645e0b870286"
down_revision: Union[str, Sequence[str], None] = "e4b7c2a91d36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_the_register_is_empty() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-467 empty-register check skipped: it reads rows and needs a live database")
        return
    bind = op.get_bind()
    plates = bind.execute(sa.text("SELECT count(*) FROM vehicle_plate")).scalar_one()
    conflicts = bind.execute(sa.text("SELECT count(*) FROM vehicle_plate_conflict")).scalar_one()
    if plates or conflicts:
        raise RuntimeError(
            f"KAN-467 REPORT: vehicle_plate holds {plates} row(s) and vehicle_plate_conflict {conflicts}; "
            "nothing in the application ever wrote them, so their dealer group cannot be derived here. "
            "Decide each row's group by hand (or delete it), then run the upgrade again."
        )


def upgrade() -> None:
    _assert_the_register_is_empty()
    op.drop_index(op.f("ix_vehicle_plate_conflict_plate"), table_name="vehicle_plate_conflict")
    op.drop_table("vehicle_plate_conflict")
    op.add_column(
        "vehicle_plate",
        sa.Column(
            "group_id",
            app.core.types.GUID(),
            nullable=False,
            comment="Owned by the platform context (DealerGroup) — the register this assignment is kept in. "
            "No DB-level FK.",
        ),
    )
    # The model's default is client-side; the server default only fills the
    # (empty) table and is dropped again so the schema matches the model.
    op.add_column("vehicle_plate", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("vehicle_plate", "version", server_default=None)
    op.drop_index(op.f("ix_vehicle_plate_plate_canton"), table_name="vehicle_plate")
    op.create_index(
        "ix_vehicle_plate_group_plate_canton", "vehicle_plate", ["group_id", "plate", "canton"], unique=False
    )


def downgrade() -> None:
    # Dropping group_id loses which register a row was kept in; a downgraded
    # database holds ADR-039's original global register again.
    op.drop_index("ix_vehicle_plate_group_plate_canton", table_name="vehicle_plate")
    op.create_index(op.f("ix_vehicle_plate_plate_canton"), "vehicle_plate", ["plate", "canton"], unique=False)
    op.drop_column("vehicle_plate", "version")
    op.drop_column("vehicle_plate", "group_id")
    op.create_table(
        "vehicle_plate_conflict",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("plate", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column("canton", sa.VARCHAR(length=2), autoincrement=False, nullable=False),
        sa.Column("first_plate_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("second_plate_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("detected_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.Column("resolved", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
        sa.Column("resolved_by", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("resolution_note", sa.VARCHAR(length=500), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(
            ["first_plate_id"], ["vehicle_plate.id"], name=op.f("vehicle_plate_conflict_first_plate_id_fkey")
        ),
        sa.ForeignKeyConstraint(
            ["second_plate_id"], ["vehicle_plate.id"], name=op.f("vehicle_plate_conflict_second_plate_id_fkey")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("vehicle_plate_conflict_pkey")),
    )
    op.create_index(op.f("ix_vehicle_plate_conflict_plate"), "vehicle_plate_conflict", ["plate"], unique=False)
