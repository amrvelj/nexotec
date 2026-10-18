"""module_entitlement and dealership.entitlements_version (KAN-198, D-A-04, D-A-10)

Access is entitlement × role (D-A-04): a Betrieb holds module entitlements
(`sales`, `aftersales`, `parts`, `finance`, `reporting`) set by platform_admin,
and the entitlement check runs before the role check as a JWT claim. One row
per (dealership, module) for its whole history — a withdrawn entitlement keeps
its row with `valid_to`. `dealership.entitlements_version` is bumped by every
change and minted into the token (`ent_v`); a token with another value is out
of date and the client signs in again (Anto, 2026-10-17).

Backfill: every existing dealership receives all five modules, so nothing a
Betrieb uses today disappears when the gate switches on (ticket KAN-198:
"existing dealerships on staging are migrated with all five entitlements").
Pure SQL (INSERT … SELECT), so it renders offline too; the row count is
printed online only. The backfilled rows carry `granted_by` NULL and a v4 id
from gen_random_uuid() — the one place a platform row is not a UUIDv7, said
here so nobody reads it as a bug.

Downgrade drops the table and the column.

Revision ID: 9c2d7e4b1a56
Revises: 4f8a2c7e1d93
Create Date: 2026-10-17 10:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "9c2d7e4b1a56"
down_revision: str | Sequence[str] | None = "4f8a2c7e1d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen: app.core.permissions.ModuleCode at this revision, in enum order
# (the CHECK's wording is enum_check_condition's, tests/test_migrated_enum_checks.py).
_MODULES = ("sales", "aftersales", "parts", "finance", "reporting")


def upgrade() -> None:
    op.create_table(
        "module_entitlement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dealership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dealership.id"), nullable=False
        ),
        sa.Column("module", sa.String(length=16), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            '"module" IN (' + ", ".join(f"'{m}'" for m in _MODULES) + ")",
            name="ck_module_entitlement_module_enum",
        ),
        sa.UniqueConstraint("dealership_id", "module", name="uq_module_entitlement_dealership_id_module"),
    )
    op.create_index("ix_module_entitlement_dealership_id", "module_entitlement", ["dealership_id"])
    op.add_column(
        "dealership", sa.Column("entitlements_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.alter_column("dealership", "entitlements_version", server_default=None)

    values = ", ".join(f"('{m}')" for m in _MODULES)
    op.execute(
        sa.text(
            "INSERT INTO module_entitlement (id, dealership_id, module, granted_at, granted_by, valid_from, valid_to) "
            "SELECT gen_random_uuid(), d.id, m.module, now(), NULL, now(), NULL "
            f"FROM dealership d CROSS JOIN (VALUES {values}) AS m(module)"
        )
    )
    if not context.is_offline_mode():
        conn = op.get_bind()
        dealerships = conn.execute(sa.text("SELECT count(*) FROM dealership")).scalar()
        rows = conn.execute(sa.text("SELECT count(*) FROM module_entitlement")).scalar()
        print(f"KAN-198: {dealerships} dealership(s) backfilled with all {len(_MODULES)} modules: {rows} entitlement row(s)")
        if rows != (dealerships or 0) * len(_MODULES):
            raise RuntimeError("KAN-198: backfill row count does not match dealerships × modules — aborting")


def downgrade() -> None:
    op.drop_column("dealership", "entitlements_version")
    op.drop_index("ix_module_entitlement_dealership_id", table_name="module_entitlement")
    op.drop_table("module_entitlement")
