"""KAN-464 PR-1: customer_role — the roles a contact holds; every customer migrated as `customer`

One open row (valid_to NULL) per customer and role; a closed row is the
role's history. Every existing customer — merged ones included, so a
merged record's survivor keeps its history whole — gets an open `customer`
row valid from the customer's own `created_at`.

Revision ID: a4c1e6b9d2f7
Revises: fdf7c90750b7
Create Date: 2026-11-16 14:11:48.095334

"""

import datetime as dt
from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import context, op
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "a4c1e6b9d2f7"
down_revision: str | Sequence[str] | None = "fdf7c90750b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 1000


def backfill_customer_role(bind: sa.engine.Connection) -> int:
    """An open `customer` row for every customer that holds no role yet;
    the number written."""

    customers = bind.execute(
        sa.text(
            "SELECT id, group_id, created_at FROM customer c"
            " WHERE NOT EXISTS (SELECT 1 FROM customer_role r WHERE r.customer_id = c.id) ORDER BY id"
        ).columns(
            sa.column("id", app.core.types.GUID()),
            sa.column("group_id", app.core.types.GUID()),
            sa.column("created_at", app.core.types.UTCDateTime()),
        )
    ).fetchall()
    insert = sa.text(
        "INSERT INTO customer_role (id, group_id, customer_id, role, valid_from, valid_to, created_at, updated_at)"
        " VALUES (:id, :group_id, :customer_id, 'customer', :valid_from, NULL, :now, :now)"
    ).bindparams(
        sa.bindparam("id", type_=app.core.types.GUID()),
        sa.bindparam("group_id", type_=app.core.types.GUID()),
        sa.bindparam("customer_id", type_=app.core.types.GUID()),
        sa.bindparam("valid_from", type_=app.core.types.UTCDateTime()),
        sa.bindparam("now", type_=app.core.types.UTCDateTime()),
    )
    now = dt.datetime.now(dt.UTC)
    for start in range(0, len(customers), _BATCH):
        bind.execute(
            insert,
            [
                {
                    "id": uuid7(),
                    "group_id": row.group_id,
                    "customer_id": row.id,
                    "valid_from": row.created_at,
                    "now": now,
                }
                for row in customers[start : start + _BATCH]
            ],
        )
    return len(customers)


def upgrade() -> None:
    op.create_table(
        "customer_role",
        sa.Column(
            "group_id",
            app.core.types.GUID(),
            nullable=False,
            comment="Owned by the platform context (DealerGroup). No DB-level FK.",
        ),
        sa.Column("customer_id", app.core.types.GUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("valid_from", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("valid_to", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", app.core.types.GUID(), nullable=True),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.CheckConstraint("role IN ('customer', 'supplier')", name="ck_customer_role_role_enum"),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_customer_role_customer_id"), "customer_role", ["customer_id"], unique=False)
    op.create_index(op.f("ix_customer_role_group_id"), "customer_role", ["group_id"], unique=False)
    op.create_index(
        "uq_customer_role_customer_id_role_open",
        "customer_role",
        ["customer_id", "role"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
        sqlite_where=sa.text("valid_to IS NULL"),
    )
    if context.is_offline_mode():
        op.execute("-- customer_role backfill skipped: it reads rows and needs a live database")
    else:
        backfill_customer_role(op.get_bind())


def downgrade() -> None:
    op.drop_index("uq_customer_role_customer_id_role_open", table_name="customer_role")
    op.drop_index(op.f("ix_customer_role_group_id"), table_name="customer_role")
    op.drop_index(op.f("ix_customer_role_customer_id"), table_name="customer_role")
    op.drop_table("customer_role")
