"""Customer sort indexes (U-02/U-03)

U-03: "Every column declared sortable must have a supporting index. A
column with no index is not shipped as sortable." customer_number and
company_name already had one; last_name, updated_at and created_at didn't
— this adds them, tenant_id-prefixed since every list query is already
`WHERE tenant_id = ...`, same pattern as ix_customer_tenant_id.
updated_at is the sortable set's default (`?sort=updatedAt:desc` when the
client sends nothing — UI/UX Core Principles FR-UI-01: "Default sort: ...
For most grids: updatedAt:desc"), so it's the one this migration can't skip.

Revision ID: d2f7b0e9c453
Revises: a6c2f8de41b7
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd2f7b0e9c453'
down_revision: Union[str, Sequence[str], None] = 'a6c2f8de41b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_customer_tenant_id_last_name", "customer", ["tenant_id", "last_name"])
    op.create_index("ix_customer_tenant_id_updated_at", "customer", ["tenant_id", "updated_at"])
    op.create_index("ix_customer_tenant_id_created_at", "customer", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_customer_tenant_id_created_at", table_name="customer")
    op.drop_index("ix_customer_tenant_id_updated_at", table_name="customer")
    op.drop_index("ix_customer_tenant_id_last_name", table_name="customer")
