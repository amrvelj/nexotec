"""One configuration, one stock item — backed by a partial unique index (KAN-513)

`uq_stock_item_tenant_id_configuration_id` on stock_item (tenant_id,
configuration_id) where configuration_id is not null: a configuration is one
car's technical data (FR-C-11, KAN-509), so the database itself refuses a
second stock item of the Betrieb that holds it, whichever writer tries —
create and «Konfiguration zuordnen» (which already check under an advisory
lock), the FR-I-02/02b consumers (which now take the same lock, KAN-513) and
any writer that skips the lock (none is known; defence in depth).

Checked before: staging (the only environment, Anto 2026-11-12) holds no
duplicate (KAN-513, 2026-11-13: no stock item carries a configuration
there). Were one written since, the CREATE UNIQUE INDEX fails and the
upgrade stops, so nothing is decided silently. stock_item is small; a plain
CREATE INDEX in the upgrade's transaction.

Revision ID: 04ab4cc24646
Revises: 2001f0c37ef5
Create Date: 2026-11-13 10:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04ab4cc24646'
down_revision: Union[str, Sequence[str], None] = '2001f0c37ef5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'uq_stock_item_tenant_id_configuration_id',
        'stock_item',
        ['tenant_id', 'configuration_id'],
        unique=True,
        postgresql_where=sa.text('configuration_id IS NOT NULL'),
        sqlite_where=sa.text('configuration_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_stock_item_tenant_id_configuration_id', table_name='stock_item')
