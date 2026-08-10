"""drop cross-context foreign keys (PR-2, ADR-015)

Fifteen columns across ten tables, per CLAUDE.md's rule 2 ("no
cross-context foreign keys... another context's ID is a plain GUID column
with a comment naming the owner") and the explicit PR-2 scope: the eight
named columns, plus every tenant_id -> dealer.id FK (seven columns, not
one). Column type, nullability and every existing index are untouched —
only the FK constraint goes away. The compensating control (P-10) is the
nightly reconciliation job shipped alongside this migration, checking
app.core.reconciliation / app.<ctx>.reconciliation.

Eleven of these constraints were created with an explicit name and can be
dropped directly. Four (customer_number_sequence, customer_phone,
customer_email, customer_external_id — all on tenant_id) were created via
an unnamed sa.ForeignKeyConstraint(...), so Postgres auto-assigned the
constraint name at DDL time; this migration looks that name up at runtime
via SQLAlchemy inspection rather than hardcoding a guessed name.

Revision ID: 22708a77f565
Revises: d2f7b0e9c453
Create Date: 2026-08-10 13:58:30.979652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '22708a77f565'
down_revision: Union[str, Sequence[str], None] = 'd2f7b0e9c453'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, constraint_name, target_table, target_column)
_NAMED_FKS = [
    ("vehicle_party", "customer_id", "fk_vehicle_party_customer_id_customer", "customer", "id"),
    ("vehicle_party", "vehicle_id", "fk_vehicle_party_vehicle_id_vehicle", "vehicle", "id"),
    ("vehicle_custody_event", "partner_id", "fk_vehicle_custody_event_partner_id_dealer", "dealer", "id"),
    (
        "vehicle_custody_event",
        "transaction_id",
        "fk_vehicle_custody_event_transaction_id_transaction",
        "transaction",
        "id",
    ),
    ("vehicle", "current_custodian_partner_id", "fk_vehicle_current_custodian_partner_id_dealer", "dealer", "id"),
    ("transaction", "customer_id", "fk_transaction_customer_id_customer", "customer", "id"),
    ("transaction", "vehicle_id", "fk_transaction_vehicle_id_vehicle", "vehicle", "id"),
    ("transaction", "primary_user_id", "fk_transaction_primary_user_id_user", "user", "id"),
    ("transaction", "tenant_id", "fk_transaction_tenant_id_dealer", "dealer", "id"),
    ("customer", "tenant_id", "fk_customer_tenant_id_dealer", "dealer", "id"),
    ("user", "tenant_id", "fk_user_tenant_id_dealer", "dealer", "id"),
]

# (table, column, target_table, target_column) — unnamed at creation time;
# Postgres auto-named these, so the real name is looked up at runtime.
_UNNAMED_FKS = [
    ("customer_number_sequence", "tenant_id", "dealer", "id"),
    ("customer_phone", "tenant_id", "dealer", "id"),
    ("customer_email", "tenant_id", "dealer", "id"),
    ("customer_external_id", "tenant_id", "dealer", "id"),
]


def _find_fk_name(inspector: sa.engine.reflection.Inspector, table: str, column: str) -> str:
    for fk in inspector.get_foreign_keys(table):
        if fk["constrained_columns"] == [column]:
            name = fk.get("name")
            if not name:
                raise RuntimeError(
                    f"Foreign key on {table}.{column} has no name — cannot drop it safely by inspection."
                )
            return name
    raise RuntimeError(f"No foreign key found on {table}.{column} — expected one to drop.")


def upgrade() -> None:
    for table, column, name, _target_table, _target_column in _NAMED_FKS:
        op.drop_constraint(name, table, type_="foreignkey")

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, column, _target_table, _target_column in _UNNAMED_FKS:
        name = _find_fk_name(inspector, table, column)
        op.drop_constraint(name, table, type_="foreignkey")


def downgrade() -> None:
    for table, column, name, target_table, target_column in _NAMED_FKS:
        op.create_foreign_key(name, table, target_table, [column], [target_column])

    for table, column, target_table, target_column in _UNNAMED_FKS:
        # Recreate unnamed, same as the original migration — Postgres
        # auto-assigns a name again, which need not match the pre-upgrade one.
        op.create_foreign_key(None, table, target_table, [column], [target_column])
