"""Customer moves from dealership to group scope (WP-3 PR-2, ADR-014)

Expand-migrate-contract, per the brief: add group_id everywhere tenant_id
used to be the scoping column on Customer's own tables — customer,
customer_number_sequence (whose tenant_id was its PRIMARY KEY),
customer_phone, customer_email, customer_external_id — backfill from each
row's dealership's dealer_group_id (join through `dealership`, itself
already backfilled to one-group-per-dealership by WP-3 PR-1), verify by row
count with a MIGRATION_DRY_RUN_REPORT line before anything is dropped, then
drop tenant_id and its indexes/constraints and add the group_id equivalents.

Depends on WP-3 PR-1's platform-chain migration (b3f7c92a5e1d) — this
migration's backfill reads dealership.dealer_group_id, which only exists
and is populated once that one has run. Customer and platform are
independent Alembic branches with no inherent ordering, so this is made
explicit via depends_on rather than assumed from commit order.

Every dealership is still a group of one at this point (WP-3's whole
reason for urgency — this migration must land before that stops being
true), so each customer_number_sequence row's dealership maps to exactly
one group with nothing to merge yet.

Revision ID: d4a1f6c8e3b7
Revises: 8efcbf914818
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4a1f6c8e3b7'
down_revision: Union[str, Sequence[str], None] = '8efcbf914818'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = ('b3f7c92a5e1d',)


def _dealership_table():
    return sa.table(
        "dealership",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("dealer_group_id", postgresql.UUID(as_uuid=True)),
    )


def _backfill_group_id(bind, table_name: str, tenant_id_column: str = "tenant_id") -> None:
    """UPDATE <table> SET group_id = (SELECT dealer_group_id FROM dealership
    WHERE dealership.id = <table>.<tenant_id_column>) — a portable
    correlated-subquery UPDATE rather than dialect-specific UPDATE...FROM.
    """

    dealership = _dealership_table()
    table = sa.table(
        table_name,
        sa.column(tenant_id_column, postgresql.UUID(as_uuid=True)),
        sa.column("group_id", postgresql.UUID(as_uuid=True)),
    )
    bind.execute(
        table.update().values(
            group_id=sa.select(dealership.c.dealer_group_id)
            .where(dealership.c.id == getattr(table.c, tenant_id_column))
            .scalar_subquery()
        )
    )


def _verify_no_unassigned(bind, table_name: str) -> None:
    table = sa.table(table_name, sa.column("group_id", postgresql.UUID(as_uuid=True)))
    unassigned = bind.execute(
        sa.select(sa.func.count()).select_from(table).where(table.c.group_id.is_(None))
    ).scalar()
    if unassigned:
        raise RuntimeError(
            f"WP-3 PR-2 backfill left {unassigned} row(s) in {table_name} with no group_id. Rolling back."
        )
    total = bind.execute(sa.select(sa.func.count()).select_from(table)).scalar()
    print(f"MIGRATION_DRY_RUN_REPORT: {table_name} — {total} row(s), 0 unassigned after group_id backfill.")


def upgrade() -> None:
    bind = op.get_bind()

    # --- customer -----------------------------------------------------
    op.add_column("customer", sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True))
    _backfill_group_id(bind, "customer")
    _verify_no_unassigned(bind, "customer")

    op.drop_index("ix_customer_tenant_id_created_at", table_name="customer")
    op.drop_index("ix_customer_tenant_id_updated_at", table_name="customer")
    op.drop_index("ix_customer_tenant_id_last_name", table_name="customer")
    op.drop_index("ix_customer_tenant_id", table_name="customer")
    with op.batch_alter_table("customer") as batch:
        batch.drop_constraint("uq_customer_tenant_id_customer_number", type_="unique")
        batch.alter_column("group_id", nullable=False)
        batch.drop_column("tenant_id")
        batch.create_unique_constraint("uq_customer_group_id_customer_number", ["group_id", "customer_number"])
    op.create_index("ix_customer_group_id", "customer", ["group_id"])
    op.create_index("ix_customer_group_id_last_name", "customer", ["group_id", "last_name"])
    op.create_index("ix_customer_group_id_updated_at", "customer", ["group_id", "updated_at"])
    op.create_index("ix_customer_group_id_created_at", "customer", ["group_id", "created_at"])

    # --- customer_number_sequence (tenant_id was its PRIMARY KEY) ------
    op.add_column(
        "customer_number_sequence", sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    _backfill_group_id(bind, "customer_number_sequence")
    _verify_no_unassigned(bind, "customer_number_sequence")
    with op.batch_alter_table("customer_number_sequence") as batch:
        batch.drop_constraint("customer_number_sequence_pkey", type_="primary")
        batch.alter_column("group_id", nullable=False)
        batch.drop_column("tenant_id")
        batch.create_primary_key("customer_number_sequence_pkey", ["group_id"])

    # --- customer_phone / customer_email / customer_external_id -------
    for table_name in ("customer_phone", "customer_email", "customer_external_id"):
        op.add_column(table_name, sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True))
        _backfill_group_id(bind, table_name)
        _verify_no_unassigned(bind, table_name)
        op.drop_index(f"ix_{table_name}_tenant_id", table_name=table_name)
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("group_id", nullable=False)
            batch.drop_column("tenant_id")
        op.create_index(f"ix_{table_name}_group_id", table_name, ["group_id"])

    # No explicit drop of uq_customer_external_id_tenant_system_external here:
    # it's a table-local constraint on tenant_id, which the loop above already
    # dropped via batch.drop_column("tenant_id") — Postgres cascades that drop
    # to any constraint referencing the column without needing CASCADE, since
    # the dependency is internal to the table. Dropping it again by name here
    # (as an earlier version of this migration did) fails with
    # psycopg.errors.UndefinedObject once run against real Postgres — SQLite's
    # create_all()-based test lane never exercises alembic migrations at all,
    # so this only surfaced once CI actually ran `alembic upgrade heads`.
    with op.batch_alter_table("customer_external_id") as batch:
        batch.create_unique_constraint(
            "uq_customer_external_id_group_system_external", ["group_id", "system_name", "external_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    dealership = _dealership_table()

    with op.batch_alter_table("customer_external_id") as batch:
        batch.drop_constraint("uq_customer_external_id_group_system_external", type_="unique")

    for table_name in ("customer_phone", "customer_email", "customer_external_id"):
        op.drop_index(f"ix_{table_name}_group_id", table_name=table_name)
        op.add_column(table_name, sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
        table = sa.table(
            table_name,
            sa.column("group_id", postgresql.UUID(as_uuid=True)),
            sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
        )
        # dealer_group_id was unique per dealership pre-PR-1 (group of one),
        # so this join back is unambiguous — same assumption the upgrade made.
        bind.execute(
            table.update().values(
                tenant_id=sa.select(dealership.c.id)
                .where(dealership.c.dealer_group_id == table.c.group_id)
                .scalar_subquery()
            )
        )
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("tenant_id", nullable=False)
            batch.drop_column("group_id")
        op.create_index(f"ix_{table_name}_tenant_id", table_name, ["tenant_id"])

    with op.batch_alter_table("customer_external_id") as batch:
        batch.create_unique_constraint(
            "uq_customer_external_id_tenant_system_external", ["tenant_id", "system_name", "external_id"]
        )

    op.add_column(
        "customer_number_sequence", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    cns = sa.table(
        "customer_number_sequence",
        sa.column("group_id", postgresql.UUID(as_uuid=True)),
        sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    )
    bind.execute(
        cns.update().values(
            tenant_id=sa.select(dealership.c.id)
            .where(dealership.c.dealer_group_id == cns.c.group_id)
            .scalar_subquery()
        )
    )
    with op.batch_alter_table("customer_number_sequence") as batch:
        batch.drop_constraint("customer_number_sequence_pkey", type_="primary")
        batch.alter_column("tenant_id", nullable=False)
        batch.drop_column("group_id")
        batch.create_primary_key("customer_number_sequence_pkey", ["tenant_id"])

    op.drop_index("ix_customer_group_id_created_at", table_name="customer")
    op.drop_index("ix_customer_group_id_updated_at", table_name="customer")
    op.drop_index("ix_customer_group_id_last_name", table_name="customer")
    op.drop_index("ix_customer_group_id", table_name="customer")
    op.add_column("customer", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    customer = sa.table(
        "customer",
        sa.column("group_id", postgresql.UUID(as_uuid=True)),
        sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    )
    bind.execute(
        customer.update().values(
            tenant_id=sa.select(dealership.c.id)
            .where(dealership.c.dealer_group_id == customer.c.group_id)
            .scalar_subquery()
        )
    )
    with op.batch_alter_table("customer") as batch:
        batch.drop_constraint("uq_customer_group_id_customer_number", type_="unique")
        batch.alter_column("tenant_id", nullable=False)
        batch.drop_column("group_id")
        batch.create_unique_constraint("uq_customer_tenant_id_customer_number", ["tenant_id", "customer_number"])
    op.create_index("ix_customer_tenant_id", "customer", ["tenant_id"])
    op.create_index("ix_customer_tenant_id_last_name", "customer", ["tenant_id", "last_name"])
    op.create_index("ix_customer_tenant_id_updated_at", "customer", ["tenant_id", "updated_at"])
    op.create_index("ix_customer_tenant_id_created_at", "customer", ["tenant_id", "created_at"])
